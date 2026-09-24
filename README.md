# Sanctum Forensics

**Secure sanitization, forensic recovery and tamper-evident verification in one offline workflow.**

Built for SIH 26149 (NTRO): one tool that erases storage by a method the
device itself supports, recovers evidence without writing to it, and signs a
record of both that a third party can check on their own machine.

`SECURE ERASE` · `FILE ERASURE` · `FORENSIC RECOVERY` · `VERIFICATION` · `AUDIT`

![tests](https://img.shields.io/badge/pytest-1835%20passed%20·%2034%20skipped%20·%200%20failed-2ea44f)
![python](https://img.shields.io/badge/Python-3.11-3776ab)
![stack](https://img.shields.io/badge/FastAPI%20%2B%20React-localhost%20only-555)
![signing](https://img.shields.io/badge/reports-Ed25519-555)
![network](https://img.shields.io/badge/runtime-offline-555)
![license](https://img.shields.io/badge/license-GPL--3.0-555)

> **Current release evidence**
>
> | | |
> |---|---|
> | Release | `main` at `08bee5f` (2026-09-24). Application code frozen at `8e1777d`; later commits are documentation and one test marker |
> | Test suite | 1835 passed · 34 skipped · 0 failed (Linux host, 2026-09-24); Linux, Windows and macOS suites green in `platform-ci` |
> | Primary platform | Linux. Whole-drive sanitization runs only there |
> | Physically validated | One USB flash stick (Toshiba TransMemory, 7.76 GB): overwrite Clear with full read-back, three recovery passes, mounted-device refusal |
> | Hardware-unverified | Firmware Purge (ATA SANITIZE, SECURITY ERASE, NVMe sanitize/format, crypto erase), HPA/DCO unlock, backup restoration, the registered physical recovery benchmark, any Windows or macOS physical device |

---

## Why Sanctum?

Forensic and sanitization tools usually do one job each. One tool erases. A
different tool recovers. The report is written by hand, and nobody can later
prove it was not edited.

Sanctum puts those jobs in one system, with one audit trail, and keeps every
destructive step behind gates a human has to pass on purpose.

```text
SANITIZE   DISCOVER → PREFLIGHT → PLAN → HUMAN APPROVAL → EXECUTE → VERIFY → REPORT
RECOVER    ACQUIRE  → CARVE     → VALIDATE → SCORE → EXPLAIN → REPORT
                                              │
                        both append to one hash-chained ledger
                        and end in one Ed25519-signed report
```

| Module | What it does |
|---|---|
| **M1 Secure Drive Eraser** | Whole-drive Clear / Purge / Destroy in NIST SP 800-88 Rev. 2 vocabulary, method chosen from probed device capability |
| **M2 Secure File & Folder Eraser** | File, folder and batch erasure, document metadata cleansing, and a report of what the filesystem kept anyway |
| **M3 File Carving & Recovery** | Read-only acquisition, filesystem-aware undelete, signature and structure carving, bifragment reconstruction, decoder validation, evidence scoring |

## What makes it different

**Device-aware sanitization.** The erase method comes from what the device
reports it can do (`hdparm -I`, NVMe Identify, `sedutil-cli`, sysfs). The UI
has no method dropdown. If the requested level cannot be reached, the job is
NOT AUTHORIZED; it never silently downgrades.

**Destructive-operation safety.** Serial and `/dev/disk/by-id` binding, a
typed-serial confirmation, refusal of mounted and system disks, dry run by
default, and no automatic `sudo` or unmount. See [The safety model](#the-safety-model).

**Explainable recovery.** Every candidate is bounded by its format's own
length fields, read by a real decoder, and scored on named evidence. A file
split in two is rebuilt only when its own bytes prove the join.

**Tamper-evident reporting.** Every operation appends to a hash-chained
ledger. Reports are signed with Ed25519 and checked by a verifier a third
party runs themselves. One changed field fails verification.

**Reproducible validation.** Deterministic synthetic corpora with ground-truth
manifests, a bounded fuzz pass, 1 GiB and 7 GiB image runs, and a benchmark
against PhotoRec and Foremost. Every figure links to its raw result.

**Honest cross-platform adapters.** Linux, Windows and macOS each report what
they can really do. Where an operation is not validated, the app refuses with
the reason instead of offering it.

**Explicit uncertainty.** Every result carries its population: physical,
synthetic, simulation, CI, documented or hardware-unverified. They are never
merged.

## The safety model

Destructive work follows one state machine (`core/workflow.py`), which refuses
any illegal transition. The Sanitize screen draws it, and `BLOCKED` always
carries a **WHY BLOCKED** reason and the human action that clears it.

```text
DISCOVERED
   ↓
PREFLIGHT ─────────────→ BLOCKED   device absent, mounted, system disk,
   ↓                               serial sources disagree …
(BACKUP REQUIRED → BACKUP VERIFIED)   physical benchmark write only
   ↓
HUMAN APPROVAL REQUIRED   a person reviews the plan; dry run off
   ↓
PLAN READY                operator types the device serial
   ↓
EXECUTING
   ↓
VERIFYING
   ↓
COMPLETE / FAILED
```

- **Mounted and system disks are refused**, with the mount point named. No
  code path calls `umount` or `udisksctl`; a human unmounts.
- **Identity is the serial, not the kernel name.** The operator types the
  serial of the selected disk. Right before writing, the engine re-reads the
  device and raises `DeviceVanished` if the serial or by-id link changed.
- **Nothing is substituted.** A missing or stale device path is a structured
  refusal; no other device is tried.
- **Dry run is the default.** A write needs dry run turned off and the serial
  typed; the erase button stays disabled until it matches.
- **Privilege is explicit.** The UI and API run unprivileged. Raw device work
  goes through one helper that a human starts with `sudo`, over a static
  allowlist of typed operations ([privilege boundary](docs/privilege-boundary.md)).
- **Verification follows every erase.** Full read-back up to 64 GiB, seeded
  sampling above that, with the detection probability in the report.
- **The physical benchmark adds a backup gate.** `scripts/media_benchmark.py
  write` re-verifies, by itself and just before the first write, a backup on
  another disk bound to the device serial, the write extent and the image's
  SHA-256. Whole-drive sanitization has no backup gate by design.

## Forensic recovery

```text
Acquisition (O_RDONLY, SHA-256 + BLAKE3)
   ↓
Filesystem-aware undelete   NTFS, FAT12/16/32, exFAT, ext2/3/4
   ↓
Signature carving           24 signatures
   ↓
Structure carving           16 parsers derive the exact end from length fields
   ↓
Fragment reconstruction     baseline JPEG and PNG, exactly two runs
   ↓
Decoder validation          17 decoders
   ↓
Evidence scoring            six named components
   ↓
Explainable candidate       offset, runs, SHA-256, score breakdown
```

The evidence path is read-only: `core/carve/evidence.py` opens `O_RDONLY` and
has no write method. Format coverage is generated from the code into
[`docs/supported-formats.md`](docs/supported-formats.md), and a test fails if
it drifts.

**Bifragment reconstruction.** A candidate whose structure does not close is
flagged `possibly_fragmented`. Sanctum then searches for a second run and
accepts a join only if it is unique and the bytes prove it: an exact Huffman
scan count for JPEG, every chunk CRC-32 plus a zlib stream of exactly the
header's size for PNG. On synthetic images, 120 of 120 PNG layouts were
rebuilt and 0 of 800 deliberately wrong joins were accepted
([`png-reassembly.md`](docs/validation/png-reassembly.md)). A rebuilt object
keeps both runs, so its digest can be recomputed from the image.

**The evidence score.** A sum of named components in basis points: header
2000, exact length 1500, decoder 4000, entropy 1000, filesystem metadata 1500,
no overlap 500, clamped at 10,000. HIGH is 8000 or more. A reassembled object
is held at 7999, so it is never HIGH. **It is an evidence score, not a
probability.** What was measured is how often each bucket was right on a
population: 104 of 104 HIGH candidates byte-exact across eight synthetic seeds
([`calibration-pooled.md`](docs/performance/calibration-pooled.md)). That is
not a rate for seized media.

## Trust and forensics

```text
DEVICE
  ↓
ACQUISITION HASH        SHA-256 + BLAKE3; SHA-256 per carved object
  ↓
OPERATION LOG
  ↓
HASH-CHAINED LEDGER     entry N holds SHA-256 of entry N−1
  ↓
REPORT                  canonical JSON (authoritative) + PDF (for reading)
  ↓
ED25519 SIGNATURE
  ↓
INDEPENDENT VERIFICATION   `sanctum verify-report`, five checks
```

`verify-report` checks the signature over canonical JSON, the key fingerprint
against the ledger genesis, the chain excerpt inside the report, the store
chain, and the stored blobs. It returns a graded verdict: `VERIFIED`,
`VERIFIED_WITH_LIMITATIONS`, `PARTIAL` or `FAILED_VERIFICATION`, with a reason
for every downgrade.

**Change one field and verification fails.** The Audit screen shows this live
on a server-side scratch copy: chain `VALID` before, `BROKEN` at the altered
entry after.

**What the signature proves:** whoever held this private key signed exactly
these bytes, and they have not changed since.

**What it does not prove:** who signed. The key is local, not PKI and not
government-issued, so identity needs the key fingerprint from another channel.
Someone with write access to the whole state directory can rebuild the chain
unless an external anchor is configured, and none is by default. The PDF is
not signed and says so.

How to verify a report on your own machine:
[user manual §7](docs/user-manual.md#7-reports-and-verification).

## Validation

**Automated.** 1835 passed · 34 skipped · 0 failed on the Linux host
(2026-09-24). Each skip names its reason: root and `losetup` (10), a Windows
host (10), a macOS host (8); the rest name their own host condition. The Windows and
macOS suites run on their own runners in `platform-ci`.

**Static analysis.** Ruff clean. Four `mypy --strict` passes clean: Linux,
two `--platform win32` passes, and `--platform darwin`.

**UI.** 66 of 66 unit tests pass (`cd ui && npm test`, 2026-09-24).

**Browser.** Playwright in Chromium at 1366 × 768: 24 of 24 checks on the real
API, 16 of 16 on fixture devices, no page errors
([`browser-2026-09-24/`](docs/validation/browser-2026-09-24/README.md)). The
Devices and Sanitize screens were checked against fixtures, not real devices.

**Recovery (synthetic).** Against PhotoRec and Foremost on the same 25
volumes, carve only: Sanctum 423 of 446 byte-identical, PhotoRec 372,
Foremost 220. Sanctum is about 68 times slower than PhotoRec and returns 45
false positives to PhotoRec's 0 ([`benchmark.md`](docs/performance/benchmark.md)).
7 GiB image: recall 288 of 288, 247 of 247 HIGH correct, peak RSS 513 MiB
([`large-image.md`](docs/validation/large-image.md)). Fuzz: 66,000 cases, 0
crashes after one fix ([`fuzz.md`](docs/validation/fuzz.md)).

**Physical.** One USB flash stick, recorded in
[`hardware.md`](docs/validation/hardware.md): three overwrite Clear runs (the
first two found nine defects; the third, 2026-09-05, is clean), a power-cycle
re-verification, detection of a controller that acknowledges zero writes 3.25
to 3.6 times faster than it programs them, three recovery passes (FAT32 delete
456/456, exFAT delete 460/460, FAT32 quick format 10 of the 10 carvable), and
the mounted-device refusal (2026-09-23).

**Hardware-unverified.** Firmware Purge on any drive, HPA/DCO unlock, backup
restoration, a power cut or device removal during a write, the registered
physical recovery benchmark (blocked at its first gate:
[`physical-benchmark-checklist.md`](docs/validation/physical-benchmark-checklist.md)),
and Windows or macOS on physical media.

## What we have actually proven

| Capability | Evidence | Status |
|---|---|---|
| Overwrite Clear with read-back, USB flash | three recorded runs on one stick, the third clean | **PHYSICAL VALIDATION** (one device, one model) |
| Mounted-device refusal | refusal on the stick, no I/O recorded | **PHYSICAL VALIDATION** |
| Recovery after delete and quick format | three passes on the same stick | **PHYSICAL VALIDATION** (not the registered benchmark) |
| Secure file and folder erase | suites plus packaged app on each OS runner | **CI VALIDATION** (Linux host also validated) |
| Cross-platform adapters, discovery, system-disk refusal | `platform-ci` against each runner's own disks | **CI VALIDATION** |
| Fragmented JPEG recovery | 10 of 10 on the benchmark volumes | **SYNTHETIC VALIDATION** |
| Fragmented PNG recovery | 120/120 layouts, 0/800 wrong joins accepted | **SYNTHETIC VALIDATION** |
| Recovery benchmark and calibration | 40 images, 8 pooled seeds | **SYNTHETIC VALIDATION** |
| Tamper-evident report and ledger | `tests/report/`, `tests/ledger/`, live tamper demo | **SYNTHETIC VALIDATION**; verifier also run on the physical-run report |
| Discovery-to-certificate journey | `scripts/demo_simulation.py` on host files, real engine | **SIMULATION** |
| NIST SP 800-88 Rev. 2, IEEE 2883, ISO/IEC 27040 | section-by-section mapping | **DOCUMENTED** (mapped, not certified) |
| Firmware Purge (ATA, NVMe, Opal) | selected and dispatched in fixture tests only | **HARDWARE-UNVERIFIED** |
| HPA/DCO unlock | detection tested; unlock never run on a drive | **HARDWARE-UNVERIFIED** |
| Physical recovery benchmark | harness built, preflight run; no result | **HARDWARE-UNVERIFIED** |

Row by row with code paths and tests: [`feature-matrix.md`](docs/validation/feature-matrix.md).

## The 4½-minute demo

Nothing is erased on stage. The Sanitize beat stops at the approval gate.
Every step maps to a screen, a command, a test and a recorded artifact in the
[demo evidence index](docs/validation/demo-evidence-index.md).

| # | Step | Shown with | Population |
|---|---|---|---|
| 1 | Overview: four workflows and the six-part executive summary, including what is not physically validated | Overview screen | live host |
| 2 | Device identity and refusal: serial, capability badge, `BLOCKED · WHY BLOCKED`; a missing device path refused, exit 2 | Devices; `scripts/media_benchmark.py plan` | live host |
| 3 | Simulation from discovery to certificate, labelled `SIMULATION / NO PHYSICAL DEVICE MODIFIED` | `scripts/demo_simulation.py` | SIMULATION |
| 4 | Fragmented recovery: split PNG and JPEG rebuilt, checked against ground truth | `scripts/demo_fragmented.py`; Recovery | SYNTHETIC |
| 5 | Evidence explanation: the six components and the reassembly hold | Recovery score breakdown | SYNTHETIC |
| 6 | Signed report generated for the job | Audit | SYNTHETIC |
| 7 | Tamper verification: one byte changed, chain `BROKEN` | Audit, *Simulate tampering* | SYNTHETIC |
| 8 | Certificate: case, report SHA-256, key fingerprint, signed JSON and PDF | Audit report panel | SYNTHETIC |
| 9 | Sanitization workflow: probe, selected method, approval gate. **Erase is not pressed** | Sanitize | live host, no write |
| 10 | Benchmark evidence, with its population said aloud | [`benchmark.md`](docs/performance/benchmark.md) | SYNTHETIC |

Every beat ran in a technical rehearsal on 2026-09-24. The spoken script has
not yet been timed aloud; it and the rehearsal log are in the
[judge defense card](docs/validation/judge-defense-card.md#spoken-script-430).

## Judge questions

**Why not just format the drive?** A format rewrites filesystem metadata and
leaves the data. On the physical stick, after a FAT32 quick format, carving
still recovered all 10 of the 10 carvable planted files byte-exact.

**How do you prevent erasing the wrong device?** The operator types the
serial; a mismatch refuses. System and mounted disks are refused before that,
and the serial is re-read right before writing. That re-read test needs root
and a loop device, so it is skipped in the unprivileged suite.

**What happens when the filesystem is mounted?** Refused with the mount point
named, and the tool never unmounts. Physically validated on the stick.

**How is SSD or flash different?** Overwrite cannot reach remapped or
over-provisioned blocks, so on flash it is at most Clear and every report says
so. Purge needs the device's own firmware, and that path is hardware-unverified.

**How is fragmented recovery different from normal carving?** A split file is
rebuilt only when a decoder-level check proves the join, and the result is
held below HIGH. Two formats, exactly two runs.

**Is the evidence score a probability?** No. It is a sum of named evidence
components, clamped at 10,000. Its buckets were measured against synthetic
ground truth, not seized media.

**How do you prove the report was not changed?** Ed25519 over canonical JSON,
checked with the ledger chain by `verify-report`. One changed field fails.

**What is physically validated?** Overwrite Clear, three recovery passes and
the mounted refusal, on one USB stick.

**What is still hardware-unverified?** Firmware Purge, HPA/DCO unlock, backup
restoration, the registered physical benchmark, a power cut mid-write, and
Windows or macOS physical devices.

**What happens if the device disappears?** Before the job: `DeviceVanished`,
nothing written, no other device tried. During a write: the job ends `failed`
and no certificate is issued. Removal during a write has not been tested.

All 34 questions, with evidence and a status for each:
[`judge-defense-card.md`](docs/validation/judge-defense-card.md).

## What Sanctum does not claim

- Firmware Purge has not run on any physical drive. It is selected and
  dispatched from probed capability, and tested with fixtures.
- An overwrite does not reach remapped or over-provisioned flash blocks.
- Fragmented-file reconstruction is not general: baseline JPEG and PNG,
  exactly two runs, nothing else.
- The certificate is integrity-protected, not identity-proving. It is not
  government-signed and not PKI-backed.
- No certification or compliance is claimed. Sanctum uses NIST SP 800-88
  Rev. 2 vocabulary and maps to it, IEEE 2883 and ISO/IEC 27040. DoD
  5220.22-M is a legacy engine method with a warning, not a standard claimed.
- Windows and macOS do not do whole-drive sanitization. They refuse with the
  reason.
- Recovery rates are synthetic. No result exists yet under the registered
  physical benchmark.
- Per-file erasure is often unverifiable (copy-on-write filesystems, FAT
  extents), and is reported as such rather than as a pass.
- ext4 undelete recovers almost nothing, because the kernel zeroes the extent
  tree on unlink. That is measured.

**When the evidence is insufficient, Sanctum reports the limitation instead of
upgrading it into a guarantee.** The full list is
[`docs/limitations.md`](docs/limitations.md).

## Platform support

| Platform | Recovery | File erase | Whole-drive Clear | Firmware Purge | Status |
|---|---|---|---|---|---|
| Linux | full pipeline; synthetic + one physical stick | VALIDATED | VALIDATED on real USB flash | HARDWARE-UNVERIFIED | primary platform |
| Windows | not run as a suite | CI-VALIDATED (NTFS) | UNSUPPORTED, refused | UNSUPPORTED | CI only, no physical device |
| macOS | not run as a suite | CI-VALIDATED; APFS reported NOT VERIFIABLE | UNSUPPORTED, refused | UNSUPPORTED | CI only, no physical device |

Packages: AppImage and `.deb`, `SanctumSetup.exe`, `Sanctum.dmg`. All are
unsigned and not notarized. CI runners have virtual disks, so CI-validated is
not hardware-validated. Details: [`platform-support.md`](docs/platform-support.md),
[`hardware-platform-matrix.md`](docs/validation/hardware-platform-matrix.md).

## Quick start

Fedora or Debian/Ubuntu with Python 3.11. The host `python3` is often not
3.11; [`docs/technical.md`](docs/technical.md) explains why that matters.

```bash
./scripts/devsetup.sh
source .venv/bin/activate
make check          # ruff + four mypy --strict passes + pytest
make run            # prints http://127.0.0.1:8787/session/<token>; open it
```

The control surface binds `127.0.0.1` only, serves its own bundled assets, and
makes no network call. It mints a session token per run and refuses any
request without it, or addressed to a non-loopback host name.

Run the two terminal demos without any device:

```bash
python scripts/demo_simulation.py     # SIMULATION: discovery to certificate
python scripts/demo_fragmented.py     # SYNTHETIC: split PNG and JPEG rebuilt
```

Whole-device operations need the privileged helper; see the
[user manual §3](docs/user-manual.md#3-starting-it).

## Documentation map

**For judges**
[Feature matrix](docs/validation/feature-matrix.md) ·
[Judge defense card](docs/validation/judge-defense-card.md) ·
[Demo evidence index](docs/validation/demo-evidence-index.md) ·
[Browser validation](docs/validation/browser-2026-09-24/README.md)

**For examiners**
[User manual](docs/user-manual.md) ·
[Report verification](docs/user-manual.md#7-reports-and-verification) ·
[Threat model](docs/threat-model.md) ·
[Limitations](docs/limitations.md) ·
[Compliance mapping](docs/compliance.md)

**For engineers**
[Technical reference](docs/technical.md) ·
[Architecture](docs/architecture.md) ·
[Privilege boundary](docs/privilege-boundary.md) ·
[Platform support](docs/platform-support.md) ·
[Supported formats](docs/supported-formats.md) ·
[Acquisition performance](docs/performance/acquisition.md) ·
[Fuzzing](docs/validation/fuzz.md) ·
[Packaging](docs/packaging.md) ·
[Cross-platform security review](docs/security-review-cross-platform.md)

**For validation**
[Hardware runs](docs/validation/hardware.md) ·
[Hardware platform matrix](docs/validation/hardware-platform-matrix.md) ·
[CI platform matrix](docs/validation/platform-matrix.md) ·
[Release readiness](docs/release-readiness.md) ·
[Recovery benchmark](docs/performance/benchmark.md) ·
[Calibration](docs/performance/calibration.md) ·
[Pooled calibration](docs/performance/calibration-pooled.md) ·
[PNG reassembly](docs/validation/png-reassembly.md) ·
[Large-image runs](docs/validation/large-image.md) ·
[Physical benchmark gates](docs/validation/physical-benchmark-checklist.md)

`CLAUDE.md` holds the non-negotiables every change is checked against.
[`final-sih-readiness.md`](docs/validation/final-sih-readiness.md) is a
2026-09-21 snapshot, kept for history.

---

## Engineering and build details

### Layout

```text
core/device/   enumeration, capability probe, HPA/DCO, safety guards
core/erase/    M1 whole-device and M2 file/folder engines
core/carve/    M3 acquisition, undelete, signature, structure, validate, score
core/ledger/   hash-chained append-only audit log
core/report/   render, detached-sign, independently verify
core/platform/ Linux, Windows and macOS adapters
helper/        the one privileged process
api/           FastAPI, localhost, SSE progress
ui/            React + Vite, fully bundled, zero CDN
testkit/       synthetic media generator and ground-truth evaluator
```

### Container

```bash
docker build -t sanctum-forensics .          # or: podman build -t sanctum-forensics .
docker run --rm --network host \
    -e SANCTUM_STATE_DIR=/var/lib/sanctum \
    sanctum-forensics
```

`--network host` is required: the API binds `127.0.0.1` only, so there is no
port to publish. The image has **no privileged helper**, so device operations
inside it fail rather than escalate; it is for the API, UI, recovery and
reporting. The build refuses a UI bundle that references any external origin,
and builds a `libewf-python` that can write E01.

Building on a Fedora host directly, and the libewf details:
[`docs/technical.md`](docs/technical.md). Desktop packages:
[`docs/packaging.md`](docs/packaging.md).
