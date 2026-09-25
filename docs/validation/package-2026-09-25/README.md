# Package verification, 2026-09-25

Packages built with `scripts/build-linux-portable.sh` (podman, `python:3.11-bullseye`,
glibc 2.31 image) from a clean clone checked out at
**`b1638340bf4e27834c8a096a6302b7883c5b3978`**, the last commit that changes packaged
code. Later commits change documentation, tests and `scripts/` only, none of which is
packaged (`packaging/sanctum.spec` collects `core`, `api`, `helper`, `ui/dist` and
build metadata). Check that for any later HEAD with:

```sh
git diff --stat b1638340 HEAD -- api core helper ui/src packaging pyproject.toml constraints.txt
```

An empty result means the packages still hold that HEAD's code. The packages report
**the build commit**, not the repository HEAD; `/health` from a source checkout reports
the live HEAD instead.

| Artifact | SHA-256 |
|---|---|
| `Sanctum-0.0.0-x86_64.AppImage` | `5d8d59f492ac52cc8e31f52c3412c4de6173755015dbe4bde435582f14c7aefd` |
| `sanctum_0.0.0_amd64.deb` | `18b859622cee8eaa6be965dc4cd91cf73939653b21702681deb2aaeec601e023` |

## Identity: `identity.py`

Reads the unpacked payloads; nothing from a package is executed.
`identity-appimage.json` and `identity-deb.json`, both **PASS**:

- `build_info.json` names `b1638340…` exactly, with no `+dirty`.
- All **88 of 88** Python modules under `core/`, `api/` and `helper/` at that commit are
  in the frozen archive, and every archived code object equals the one Python 3.11
  compiles from the commit's source. None missing, none extra. This includes
  `helper.authorization`, `api.authorization` and `core.authorization`.
- The bundled UI equals `ui/dist` built from the same sources, **4 of 4** files.
- The two packages carry the same executable and byte-identical payload trees.
- `.deb`: package `sanctum` 0.0.0 amd64, no maintainer scripts, 198 payload files all
  owned `root:root`, no set-uid, set-gid or world-writable file. It declares no
  `Depends`; the executable needs `libc.so.6` (symbols up to `GLIBC_2.30`), `libz.so.1`,
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
