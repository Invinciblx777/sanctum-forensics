# Sanctum Forensics

SIH 26149 (NTRO). Integrated secure data sanitization and forensic file recovery,
in one offline tool.

- **M1 Secure Drive Eraser** — capability-driven sanitization. Clear / Purge /
  Destroy as NIST SP 800-88r2 defines them (r1 was withdrawn on 2025-09-26), with
  the method selected from what the device reported it can do, never from a
  dropdown.
- **M2 Secure File & Folder Eraser** — targeted erasure, *document* metadata
  cleansing (EXIF, OOXML `docProps`, PDF info, OLE summary), and an enumeration of
  everything the filesystem kept anyway. Filesystem metadata — MFT resident data,
  the FS journal, the USN journal, MFT slack, `$I30` index slack — is **detected and
  reported, never cleansed**; that enumeration is the deliverable, not a claim to
  have removed it.
- **M3 Advanced File Carving & Recovery** — write-blocked acquisition, filesystem-aware
  undelete, signature carving, structure carving, bifragment reassembly, decoder
  validation, calibrated confidence. Structure carving and reassembly run in the
  pipeline `POST /jobs/carve` executes (`api/carve_job.py` calls
  `core.carve.structure.carve_structures`); a reassembled object records the runs it
  was built from, so its digest can be recomputed from the image rather than trusted.

Every operation is recorded in a hash-chained ledger and can be exported as a signed
forensic report that a third party verifies with a tool they run themselves.

## Status

Implemented and under test. `make check` runs ruff, four `mypy --strict` passes
(Linux, plus `--platform win32` and `--platform darwin` for the platform
adapters) and the suite: **1835 passing, 34 skipped, 0 failing** as of
2026-09-24 on this Linux host. Each skip names its reason; on this host they
need root and `losetup` (10), a Windows host (10) or a macOS host (8), and on a
host whose libewf build cannot write E01 that adds a few more. The Windows and
macOS skips are not gaps — those suites run on their own runner in
`platform-ci`, and `docs/validation/platform-matrix.md` records what each one
reported. The UI has
its own 32 unit tests (`cd ui && npm test`). Validation results — a 7 GiB carve, a
fuzz pass, the pooled calibration — are summarised in
[`docs/validation/final-sih-readiness.md`](docs/validation/final-sih-readiness.md).

Validated against real removable media over six recorded runs — see
[`docs/validation/hardware.md`](docs/validation/hardware.md), which includes the run
where the erase covered 512 bytes of a 7.76 GB device and printed `COMPLETE`, the
eight other defects that run found, and the fixes.

## Platforms

One product with a native adapter per OS (`core/platform/`). Linux has the
full engine, including whole-drive Clear and firmware Purge. Windows and macOS
discover and assess devices, erase files and folders, and issue certificates;
they refuse whole-drive sanitization with the reason rather than offering
something unvalidated.

All three are exercised on their own operating system in CI
(`.github/workflows/platform-ci.yml`): the suites, the adapter against that
runner's real disks, and the built package installed and driven through a
folder erase and a signed certificate. On the Windows runner the boot disk
and the page-file disk were both refused with their reasons; on the macOS
runner the internal disk was refused because the running system boots from an
APFS container on it. Packaged as an AppImage and `.deb`, `SanctumSetup.exe`
and `Sanctum.dmg`, all unsigned — see
[`docs/platform-support.md`](docs/platform-support.md) for what each platform
does, and [`docs/validation/hardware-platform-matrix.md`](docs/validation/hardware-platform-matrix.md)
for what no CI run can establish.

## What it does not claim

Read [`docs/limitations.md`](docs/limitations.md) before reading anything else. The
short version:

- Overwriting an SSD does not reach remapped or over-provisioned blocks. Where the
  device cannot Purge, the report says Clear and says why.
- Verification above 64 GiB is sampled, and the report carries the detection
  probability rather than a bare percentage.
- ext4 undelete recovers essentially nothing, by design, because
  `ext4_ext_remove_space` zeroes the extent tree on unlink. That is measured, not
  assumed.
- Per-file erasure is usually unverifiable, and is reported as unverifiable rather
  than as a pass.

A guarantee we cannot make is printed as a limitation. That is the whole design.

## Quick start

Fedora or Debian/Ubuntu, Python 3.11 (see [`docs/technical.md`](docs/technical.md) —
the host `python3` is probably not 3.11, and that matters):

```bash
./scripts/devsetup.sh
source .venv/bin/activate
make check          # ruff + mypy --strict + pytest
```

Run the local control surface. It binds `127.0.0.1` only, serves its own bundled
assets, and makes no network call of any kind:

```bash
make run            # prints http://127.0.0.1:8787/session/<token> - open that
```

The server mints a session token per run and refuses every request without its
cookie, and every request addressed to a non-loopback name. The packaged
desktop app does the same and opens the window on that URL itself.

Whole-device operations need the privileged helper; everything else runs unprivileged.
See [`docs/privilege-boundary.md`](docs/privilege-boundary.md).

## Documentation

| Document | What it is for |
|---|---|
| [`docs/user-manual.md`](docs/user-manual.md) | Task-oriented, one section per workflow, for an examiner or administrator who will not read the source. Starting the two processes, both erase workflows, acquisition and recovery, report verification by a third party, reading the ledger, every refusal message with its remediation, and what a cancelled operation leaves. |
| [`docs/technical.md`](docs/technical.md) | Architecture, module interfaces, threat model, build environment |
| [`docs/architecture.md`](docs/architecture.md) | Layer map and the invariants each layer holds |
| [`docs/compliance.md`](docs/compliance.md) | What the tool does against NIST SP 800-88r2, and against Indian instruments (DPDP Act 2023 and Rules 2025, IT Act §43A, CERT-In, IS/ISO/IEC 27040) — including where it does not, and that IEEE 2883-2022 conformance has not been verified |
| [`docs/limitations.md`](docs/limitations.md) | Every guarantee this tool does not make |
| [`docs/platform-support.md`](docs/platform-support.md) | Linux / Windows / macOS capability matrix: FULL, PARTIAL, UNVERIFIED, UNSUPPORTED, each traced to code |
| [`docs/packaging.md`](docs/packaging.md) | Building and running the AppImage, `.deb`, `SanctumSetup.exe` and `Sanctum.dmg`; signing status |
| [`docs/validation/platform-matrix.md`](docs/validation/platform-matrix.md) | What ran on which platform, and what is NOT RUN |
| [`docs/validation/hardware-platform-matrix.md`](docs/validation/hardware-platform-matrix.md) | VALIDATED, CI-VALIDATED, NOT YET VALIDATED, UNSUPPORTED — CI success is not hardware validation |
| [`docs/release-readiness.md`](docs/release-readiness.md) | The release gate, condition by condition, with the evidence for each |
| [`docs/security-review-cross-platform.md`](docs/security-review-cross-platform.md) | Review of the adapters, launcher, API front door and installers |
| [`docs/validation/hardware.md`](docs/validation/hardware.md) | Real-media validation: method, runs, defects found |
| [`docs/performance/calibration.md`](docs/performance/calibration.md) | How the confidence weights were derived and bounded |
| [`docs/performance/acquisition.md`](docs/performance/acquisition.md) | Acquisition throughput against `ewfacquire` |
| [`docs/privilege-boundary.md`](docs/privilege-boundary.md) | The single root process and what it will accept |
| [`docs/threat-model.md`](docs/threat-model.md) | Assets, trust boundaries, each threat with its control, its test, and its limit |
| [`docs/supported-formats.md`](docs/supported-formats.md) | Generated from the signature table and parser/decoder registries; a test fails if it drifts |
| [`docs/performance/calibration-pooled.md`](docs/performance/calibration-pooled.md) | Eight-seed pooled re-run of the confidence calibration |
| [`docs/performance/benchmark.md`](docs/performance/benchmark.md) | Recovery benchmark against PhotoRec and Foremost |
| [`docs/validation/large-image.md`](docs/validation/large-image.md) | 7 GiB carve: wall clock, peak memory, throughput, false positives |
| [`docs/validation/fuzz.md`](docs/validation/fuzz.md) | Bounded fuzz pass over every structure parser and decoder |
| [`docs/validation/final-sih-readiness.md`](docs/validation/final-sih-readiness.md) | What is implemented, what is measured, what is not verified |

`CLAUDE.md` holds the non-negotiables every change is checked against.

## Layout

```
core/device/   enumeration, capability probe, HPA/DCO, safety guards
core/erase/    M1 whole-device and M2 file/folder engines
core/carve/    M3 acquisition, undelete, signature, structure, validate, score
core/ledger/   hash-chained append-only audit log
core/report/   render, detached-sign, independently verify
helper/        the one privileged process
api/           FastAPI, localhost, SSE progress
ui/            React + Vite, fully bundled, zero CDN
testkit/       synthetic media generator and ground-truth evaluator
```

## Container

```bash
docker build -t sanctum-forensics .          # or: podman build -t sanctum-forensics .
docker run --rm --network host \
    -e SANCTUM_STATE_DIR=/var/lib/sanctum \
    sanctum-forensics
```

`--network host` is required and is not laziness: the API binds `127.0.0.1` and
nothing else (`api/main.py:45`), so there is no port to publish. Publishing one
would mean binding `0.0.0.0`, which is the single thing this application refuses
to do.

The image is built in two stages. The first builds the UI from
`ui/package-lock.json` with `npm ci` and fails the build if the bundle
references any external origin, so the "zero CDN" claim above cannot be broken
without the image refusing to build. The second installs the Python
dependencies under `constraints.txt` and then runs
`scripts/build-libewf-python.sh`, because a stock `pip install libewf-python`
produces a module that reads E01 and **cannot write one** — the script's own
verification round-trips a 1 MiB E01 during the build, so an image that cannot
write E01 does not get built.

Confirm both after starting it:

```bash
curl -s --cookie "sanctum_session=$SANCTUM_SESSION_TOKEN" \
  http://127.0.0.1:8787/health             # "ui_bundled": true
docker exec <container> python -c \
    "from core.carve.acquire import e01_write_supported; print(e01_write_supported())"
```

**The container has no privileged helper.** `/health` reports the
`HELPER_IN_PROCESS` limitation, and device operations inside it will fail rather
than escalate. Drive sanitization is a host operation: run the helper on the
host as `docs/demo/runbook.md` describes. The image is for the API, the UI, the
carving and recovery path, and reporting.

The image uses Debian package names for `libtsk` and `libewf`; `docs/technical.md`
covers building on a Fedora host directly, including why the stock `libewf-python`
build cannot write E01 and how `scripts/build-libewf-python.sh` fixes it.
