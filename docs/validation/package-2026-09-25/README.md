# Package verification, 2026-09-25

**Rebuilt at `d95603d`** (the final polish, later on 2026-09-25), after two commits that
change packaged code: `0d14af2` (the Platform screen reads firmware Purge as Unverified
until a hardware result is recorded) and `d95603d` (the Cases and Platform screens, and
narrow-width table layout on Audit and Devices). The previous build, at `e81f491`, is
this README's previous version in git history.

`e81f491` is where the reportlab fix landed: reportlab imports its barcode symbologies
through `exec()`, which PyInstaller cannot see, and the certificate draws its QR code
through that package, so a build without it answers every certificate request with a
500 (`ModuleNotFoundError: reportlab.graphics.barcode.code128`). The fix is still in
`packaging/sanctum.spec`, guarded by `tests/test_packaging_spec.py`, and the isolated
smoke below issues and verifies a certificate from each package.

**What `dist/` held before this rebuild.** Not the `e81f491` packages this record then
named: its AppImage and `.deb` hashed `5d8d59f4…` and `18b85962…`, the `b163834` build
(no QR code in any PDF). Both were replaced by the `d95603d` build below.

Packages built with `scripts/build-linux-portable.sh` (podman, `python:3.11-bullseye`,
glibc 2.31 image) from a clean clone checked out at **`d95603d`**, with `ui/dist`
built in that clone. Later commits change documentation and evidence only, none of
which is packaged (`packaging/sanctum.spec` collects `core`, `api`, `helper`,
`ui/dist` and build metadata). Check that for any later HEAD with:

```sh
git diff --stat d95603d HEAD -- api core helper ui/src packaging pyproject.toml constraints.txt
```

An empty result means the packages still hold that HEAD's code. The packages report
**the build commit**, not the repository HEAD; `/health` from a source checkout reports
the live HEAD instead. `build_info.json` records the branch as `HEAD`, because the
clone was a detached checkout of the commit.

| Artifact | SHA-256 |
|---|---|
| `Sanctum-0.0.0-x86_64.AppImage` | `d3f1fe6cb7ad6ec68edce5ede9190439c8a21172cdab257e5aa085a1045a02ff` |
| `sanctum_0.0.0_amd64.deb` | `ebe9f43d3500c2224442fd7b1d367f052c41ce6209346d24e626c0595f0afe4d` |

## Identity: `identity.py`

Reads the unpacked payloads; nothing from a package is executed.
`identity-appimage.json` and `identity-deb.json`, both **PASS**:

- `build_info.json` names `d95603d…` exactly, with no `+dirty`.
- All **92 of 92** Python modules under `core/`, `api/` and `helper/` at that commit are
  in the frozen archive, and every archived code object equals the one Python 3.11
  compiles from the commit's source. None missing, none extra. This includes
  `helper.authorization`, `api.authorization` and `core.authorization`, and the
  2026-09-25 additions `core.erase.traces`, `core.carve.mediamap` and `core.destroy`.
- The bundled UI equals `ui/dist` built from the same sources, **8 of 8** files
  (the bundle now carries its own fonts).
- The two packages carry the same executable and byte-identical payload trees.
- `.deb`: package `sanctum` 0.0.0 amd64, no maintainer scripts, 202 payload files all
  owned `root:root`, no set-uid, set-gid or world-writable file. It now declares
  `Depends: libc6 (>= 2.30), zlib1g`; the executable needs `libc.so.6` (symbols up to `GLIBC_2.30`), `libz.so.1`,
  `libdl.so.2` and `libpthread.so.0` from the system, all of which are required packages
  on Debian and Ubuntu.

## Behaviour: `scripts/package_smoke.py --isolated`

The unpacked executable of each package ran inside a bubblewrap sandbox with no `/sys`,
no block device in `/dev`, no udev database and no removable-media mount; the view was
read from inside the sandbox before the app started (`sandbox_view` in the JSON).
`smoke-isolated-appimage.json` and `smoke-isolated-deb.json`: **22 PASS, 2 NOT RUN, 0
FAIL** each. Session protection, host check, `/platform`, device discovery (it ran and
found nothing, because nothing was there), a real folder erase inside the script's own
scratch directory, a signed certificate that verifies, and quit.

**NOT RUN, and why:** "every device assessed" and "protected devices are NOT AVAILABLE"
need a real device. No physical device was enumerated, opened or written by either run.

Reproduce: `bash scripts/build-linux-portable.sh` from a clean checkout, unpack
(`--appimage-extract`; `ar x` then `tar -xzf data.tar.gz`), then
`PYTHONPATH=<build venv site-packages> python3.11 identity.py <app dir> <commit>
--ui-dist ui/dist [--deb …]` and `python scripts/package_smoke.py --isolated <app
dir>/Sanctum`.
