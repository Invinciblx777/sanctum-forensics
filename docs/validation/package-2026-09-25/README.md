# Package verification, 2026-09-25

**Rebuilt at `e81f491`** (later on 2026-09-25) with the trace sweep, the media map, the
Record of Destruction, the new certificate and the redesigned UI. The first build at
`e81f491`'s predecessor `43d0fa6` **failed** the isolated smoke: every certificate
request was a 500, `ModuleNotFoundError: reportlab.graphics.barcode.code128`.
reportlab imports its barcode symbologies through `exec()`, which PyInstaller cannot
see, and the certificate draws its QR code through that package. `e81f491` collects
them (`packaging/sanctum.spec`, guarded by `tests/test_packaging_spec.py`). The
renderer before the certificate rewrite caught that ImportError, so the earlier
packages built at `b163834` (this README's previous version, in git history) shipped
PDFs with no QR code; the JSON report, which is the authoritative artifact, and its
signature were unaffected.

Packages built with `scripts/build-linux-portable.sh` (podman, `python:3.11-bullseye`,
glibc 2.31 image) from a clean clone checked out at
**`e81f491`**, the last commit that changes packaged code. Later commits change documentation, tests and `scripts/` only, none of which is
packaged (`packaging/sanctum.spec` collects `core`, `api`, `helper`, `ui/dist` and
build metadata). Check that for any later HEAD with:

```sh
git diff --stat e81f491 HEAD -- api core helper ui/src packaging pyproject.toml constraints.txt
```

An empty result means the packages still hold that HEAD's code. The packages report
**the build commit**, not the repository HEAD; `/health` from a source checkout reports
the live HEAD instead.

| Artifact | SHA-256 |
|---|---|
| `Sanctum-0.0.0-x86_64.AppImage` | `bbbadf170b0668ffd3fef5cea8a08d2324e8387835fc524f692e2d5ef6d0950e` |
| `sanctum_0.0.0_amd64.deb` | `6386c4612da665541e56edb20929234080cad70ddf2e0107c681ea7c398409b6` |

## Identity: `identity.py`

Reads the unpacked payloads; nothing from a package is executed.
`identity-appimage.json` and `identity-deb.json`, both **PASS**:

- `build_info.json` names `e81f491…` exactly, with no `+dirty`.
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
