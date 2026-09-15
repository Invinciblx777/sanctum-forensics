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

Implemented and under test. `make check` runs ruff, `mypy --strict` and the suite:
**1211 passing, 12 skipped** (the skips need root, a Windows host, or an E01-writing
libewf build; each names its reason).

Validated against real removable media over six recorded runs — see
[`docs/validation/hardware.md`](docs/validation/hardware.md), which includes the run
where the erase covered 512 bytes of a 7.76 GB device and printed `COMPLETE`, the
eight other defects that run found, and the fixes.

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
make run            # http://127.0.0.1:8787
```

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
| [`docs/validation/hardware.md`](docs/validation/hardware.md) | Real-media validation: method, runs, defects found |
| [`docs/performance/calibration.md`](docs/performance/calibration.md) | How the confidence weights were derived and bounded |
| [`docs/performance/acquisition.md`](docs/performance/acquisition.md) | Acquisition throughput against `ewfacquire` |
| [`docs/privilege-boundary.md`](docs/privilege-boundary.md) | The single root process and what it will accept |

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
curl -s http://127.0.0.1:8787/health        # "ui_bundled": true
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
