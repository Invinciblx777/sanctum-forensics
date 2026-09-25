# Release record, 2026-09-25

Population: SYNTHETIC VALIDATION, SIMULATION and PACKAGE IDENTITY. **PHYSICAL
VALIDATION: none.** No sudo was used. Nothing was pushed.

## Correction to the first version of this record

The first version (commit `7f308ba`) said no physical device was "opened, read, hashed,
written, erased, acquired or restored". That holds for opens and I/O, and it was
incomplete. Every full test run up to then, this session's included, ran **real host
discovery** from inside the suite: five platform tests and one helper test ran `lsblk -J
-O -b` over sysfs (and, on its fallback, listed `/dev/disk/by-id` and `/sys/block`).
That enumerates the metadata of every attached disk - name, size, serial, mount points -
removable media included. lsblk reads kernel tables; no device was opened and no I/O
was issued to one. The file-erase tests also asked to open the host's own system disk
read-only to verify extents; the kernel refused, because the account is not in the
`disk` group, so nothing was read.

Since `d761125` the suite refuses all of that before the syscall
(`tests/_host_device_guard.py`), the affected tests are hermetic, and any refusal fails
the run. The runs below had **0 refusals**. The first version also said that source,
API and package `/health` "agree"; they did for the build commit, not for the record's
own later commit. The packages now report the build commit named below, and a source
checkout reports its live HEAD.

## Commits (branch `docs/readme-redesign`)

| Commit | Content |
|---|---|
| `9d601b8` | helper re-checks the erase authorization at the write seam; API gate hardening |
| `ace1e93` | Sanitize screen drives the workflow authorization API |
| `0127172` | tests, browser evidence, audit, doc fixes |
| `7f308ba` | first version of this record (docs only) |
| `d761125` | host-device guard in the suite; hermetic discovery and disk reads; per-field binding tests; `test_offline_serving` un-masked |
| `db38ea7` | job registry keeps the helper's refusal kind; a failure record is not called a certificate |
| `b163834` | `--isolated` package smoke. **The packages are built from this commit.** |
| this record's commit | documentation and evidence only |

## Results

Tests at `b163834` (the later commits touch documentation only).

| Check | Result |
|---|---|
| Full pytest | **1950 passed, 33 skipped, 0 failed**; host-device guard 0 refusals |
| Skips | 10 need root and `losetup`; 12 are Windows-only and 10 macOS-only behaviour; 1 is a Pillow TIFF byte-order case |
| Helper, authorization bindings, guard self-test | 92 passed |
| Workflow gate, write seam, failure modes, erase jobs, resume, streaming (one command) | 79 passed |
| `tests/api/test_resume.py` alone | 9 passed |
| `tests/test_workflow.py`, `tests/ui` | 32 passed |
| `tests/erase` | 317 passed, 12 skipped |
| Ruff; mypy `--strict` (Linux, two win32, darwin) | clean |
| UI unit tests; `tsc -b`; build | 78 of 78; clean; built |
| Browser, Sanitize screen, fixture server in a no-device sandbox | **59 of 59**, 0 JavaScript errors (`browser-2026-09-25/`) |
| `scripts/demo_simulation.py`, `scripts/demo_fragmented.py` | ran; `SIMULATION / NO PHYSICAL DEVICE MODIFIED` printed |
| Missing device (`media_benchmark.py preflight`, nonexistent by-id path) | refused before any probe: "No other device was substituted. Nothing was read and nothing was written." |

`test_offline_serving` had been skipped since 2026-09-21 with a message blaming the
host; the real cause was a `NameError` in its own probe. It now runs and passes.

## Package identity

Built from a clean clone at **`b1638340bf4e27834c8a096a6302b7883c5b3978`**; details and
reproduction in [`package-2026-09-25/`](package-2026-09-25/README.md).

| Artifact | SHA-256 |
|---|---|
| `Sanctum-0.0.0-x86_64.AppImage` | `5d8d59f492ac52cc8e31f52c3412c4de6173755015dbe4bde435582f14c7aefd` |
| `sanctum_0.0.0_amd64.deb` | `18b859622cee8eaa6be965dc4cd91cf73939653b21702681deb2aaeec601e023` |

- Identity **PASS** for both: `build_info.json` names that commit with no `+dirty`;
  88 of 88 `core`/`api`/`helper` modules are bytecode-identical to the commit's source,
  none missing or extra; the bundled UI equals the build, file for file; both packages
  carry the same executable.
- `.deb`: no maintainer scripts, all files `root:root`, no set-uid, set-gid or
  world-writable file. Needs glibc ≥ 2.30 and zlib from the system; declares no
  `Depends`.
- Isolated smoke (`scripts/package_smoke.py --isolated`), both packages: **22 PASS,
  2 NOT RUN** (the two checks that need a real device), in a sandbox with no block
  device, no sysfs, no udev database and no removable media. The packaged `/health`
  reports `b1638340…`.
- The packages from `0127172` are superseded and were replaced in `dist/`.

## What is established, and what is not

Established by test, synthetic devices only: a real erase needs a server-issued,
one-use authorization bound to the device's serial, model and size, the plan and a
backup image; the API gate and, again, the privileged helper refuse a missing,
fabricated, reused, cross-device or stale one, including a device or backup changed
after the API gate passed; twelve concurrent attempts admit one; a dry run never
reaches a write path.

Not established: race freedom between the helper's check and the first write; that a
backup image is a copy of the device; who approved (the API authenticates no person);
anything on physical hardware. A simulation opens a device read-only for its size at
most, and never for writing.
