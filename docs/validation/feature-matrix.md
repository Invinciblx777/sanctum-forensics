# Feature matrix: SIH 26149 requirements against what the code does

**Audit date:** 2026-09-23, starting from `761dffe`; updated 2026-09-24 after the
upgrade waves. This matrix and [`demo-evidence-index.md`](demo-evidence-index.md)
are the source of truth for the presentation. A row states what the code does,
where it is, what tests it, and what it does not do. It does not describe plans
as features.

The SIH 26149 problem statement text is not in this repository. The rows below
follow the three modules named in `README.md` and `CLAUDE.md`, plus the
requirement areas (A to P) in the upgrade brief dated 2026-09-23.

## Status words

| Word | Meaning |
|---|---|
| **IMPLEMENTED + TESTED + DEMONSTRABLE** | The code exists, tests cover it, and a demo step shows it through real software output. |
| **IMPLEMENTED + TESTED + NOT YET DEMONSTRATED** | The code exists and tests cover it, but no demo step shows it yet. |
| **PARTIAL** | Implemented with a material gap, which the Limitation column names. |
| **HARDWARE-UNVERIFIED** | The software path is tested, but no physical device of that kind has run it in a recorded session. |
| **UNVERIFIED** | Code exists but has not been executed in the environment named. |
| **UNSUPPORTED** | Deliberately unavailable. The software refuses and says why. |

Evidence populations are kept apart everywhere in this file: **SYNTHETIC**
(generated images on host storage), **PHYSICAL** (a real device, recorded in
`docs/validation/hardware.md`), and **CI** (virtual disks on hosted runners).

---

## A1. Secure Drive Eraser (M1)

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Device capability discovery | `core/device/capabilities.py:probe` reads `hdparm -I`, NVMe Identify (SANICAP, FNA), `sedutil-cli --scan`, sysfs | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/device/`; Devices screen `CLEAR ONLY` badge | A capability that was not observed is reported as not supported; a privilege failure is raised, not treated as "unsupported" |
| Method selection with rationale | `recommend_method` picks the strongest probed mechanism; the justification and the probed flags go into report section 3 | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/erase/`, `tests/device/`; report `method.justification`, `method.capability_evidence` | Never downgrades silently: NOT AUTHORIZED when the requested level is not reachable |
| ATA Secure Erase (enhanced) | `EraseMethod.ATA_SECURITY_ERASE_ENHANCED` dispatch via `hdparm` | HARDWARE-UNVERIFIED | fixture tests in `tests/erase/` | Counted as Purge only on magnetic media, and that rule is unvalidated (`docs/limitations.md`); USB bridges block ATA pass-through; the password is fixed and published |
| ATA SANITIZE (block, overwrite, crypto scramble) | `ATA_SANITIZE_*` methods | HARDWARE-UNVERIFIED | fixture tests | Never executed on a drive in a recorded run |
| NVMe Sanitize / Format (SES1) | `NVME_SANITIZE_BLOCK`, `NVME_FORMAT_SES1` via `nvme-cli` | HARDWARE-UNVERIFIED | fixture tests | Format is per namespace; a multi-namespace controller needs sanitize |
| Cryptographic erase | `ATA_SANITIZE_CRYPTO_SCRAMBLE`, `SED_CRYPTO_ERASE` (TCG Opal, not Pyrite) | HARDWARE-UNVERIFIED | fixture tests | Pyrite is deliberately not counted as Opal |
| Overwrite Clear | `SINGLE_PASS_OVERWRITE` with `O_DIRECT`, 0xA5 write calibration | IMPLEMENTED + TESTED + DEMONSTRABLE (PHYSICAL) | `docs/validation/hardware.md`, six recorded runs on a USB flash stick | Overwrite cannot reach remapped or over-provisioned flash; stated in every report |
| Removable-media clearing | same engine, flash detected by transport, not by `rotational` (`core/device/media.py`) | IMPLEMENTED + TESTED + DEMONSTRABLE (PHYSICAL) | hardware.md; `tests/device/` | Some controllers elide zero fills (`CONTROLLER_WRITE_ELISION` finding) |
| Verification after the operation | `core/erase/verify.py`: full read to 64 GiB, seeded sampling above | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/erase/test_verify.py`; hardware.md | Sampled above 64 GiB; drive attestation is the drive's claim about itself |
| HPA / DCO detection and unlock | `core/device/hidden_areas.py` | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/device/` | Linux only; needs ATA pass-through, which USB bridges usually block |
| Sector size, logical and physical capacity | report `device_identity.logical_block_size`, `physical_block_size`, `size_bytes` | IMPLEMENTED + TESTED + DEMONSTRABLE | `core/report/render.py:build_report` | none recorded |
| Removable and read-only status | enumeration and preflight refuse a read-only or non-removable device without override | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/device/`, `tests/scripts/test_media_benchmark*.py` | none recorded |
| Encryption state | Opal SSC detected from `sedutil-cli`; macOS FileVault/APFS roles in `core/platform/macos.py` | PARTIAL | `tests/device/`, `tests/platform/` | No LUKS or BitLocker volume detection on the drive-erase path |
| Device health (SMART) | none | UNSUPPORTED | none | Not queried. Not a sanitization input; would be an observation only |
| Failure handling | typed errors in `core/errors.py`; unwritable ranges skipped and named | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/erase/` | Unwritable ranges are skipped, not fixed |
| Resume | overwrite resumes from the last ledgered checkpoint; firmware methods restart | PARTIAL | `tests/api/test_resume.py` | Linux overwrite only |
| Dry-run planning | `dry_run` defaults to true in the API model | IMPLEMENTED + TESTED + DEMONSTRABLE | `api/routes/models.py`, `tests/erase/test_erase_preview.py` | none recorded |

## A2. Secure File and Folder Eraser (M2)

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Single file, recursive folder, batch | `core/erase/files.py` | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/erase/files/`; CI on three OSes (`docs/platform-support.md`) | Hard-linked files are not overwritten by default |
| Document metadata cleanse | EXIF, OOXML `docProps`, PDF info, OLE summary (`core/erase/metadata.py`) | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/erase/` | Runs before the overwrite; never claims a false clean |
| Filesystem metadata | rename chain and stepped truncation; journal, MFT and index copies reported | PARTIAL | `tests/erase/` | Detected and reported, never cleansed |
| Free-space (residual) wipe | `core/erase/freespace.py` | PARTIAL | `tests/erase/` | FAT32, exFAT and ext4 on Linux only |
| Verification | physical read-back of pre-captured extents | PARTIAL | `tests/erase/` | Needs raw read access; FAT/exFAT cannot be verified by extents; copy-on-write filesystems report NOT VERIFIABLE |
| Copy-on-write and journaling filesystems | Btrfs, F2FS, APFS, ReFS: the erase runs and the report says NOT VERIFIABLE with the reason | IMPLEMENTED + TESTED + DEMONSTRABLE | `core/platform/filesystems.py`, `tests/platform/` | A file-level overwrite cannot address old copies on copy-on-write storage |
| Audit trail, partial success | each file has its own outcome; ledger entry per operation | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/erase/files/`, `tests/ledger/` | none recorded |
| Dry-run preview | default in the API model | IMPLEMENTED + TESTED + DEMONSTRABLE | `api/routes/jobs.py` | none recorded |

## A3. Advanced File Recovery and Carving (M3)

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Signature carving | 24 signatures (`core/carve/signature.py`) | IMPLEMENTED + TESTED + DEMONSTRABLE | `docs/supported-formats.md` (generated), `tests/carve/signature/` | none recorded |
| Structure validation, footer bounds | 16 structure parsers derive the exact end from length fields (`core/carve/structure.py`) | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/carve/`; footer bound fixes (`tests/carve/signature/test_footer_bound.py`) | Footerless formats are bounded by the next header of any type |
| Internal consistency, corruption detection | 17 decoders give a verdict that feeds the score (`core/carve/validate.py`); PNG CRC per chunk | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/carve/test_validate_malformed.py`; fuzz 66,000 cases (`docs/validation/fuzz.md`) | none recorded |
| Fragment detection | `possibly_fragmented` on every candidate whose parse does not close | IMPLEMENTED + TESTED + DEMONSTRABLE | `core/models.py`, `tests/carve/` | none recorded |
| Fragmented reconstruction | bifragment reassembly for baseline JPEG (exact Huffman scan oracle) and PNG (chunk CRC-32 plus exact-length zlib oracle) (`core/carve/fragmentation.py`) | PARTIAL | `tests/carve/signature/test_fragmentation.py`, `tests/carve/signature/test_png_fragmentation.py`, `scripts/demo_fragmented.py` | Two formats, exactly two runs; PDF and every other format are not reassembled |
| PNG bifragment reassembly | exhaustive, unique-or-refused join search; bound across both runs; held below HIGH | IMPLEMENTED + TESTED + DEMONSTRABLE (SYNTHETIC) | `docs/validation/png-reassembly.md`: 120/120 layouts to a 7 MiB gap, 40/40 with text or zero gaps, 0/800 adversarial joins accepted | Synthetic images from one encoder; CRC-32 and Adler-32 are not cryptographic |
| Multi-run (more than two) reconstruction | none | UNSUPPORTED | none | General reassembly is an open research problem; claiming it would not survive questioning |
| File-type classification | `core/carve/classify.py` | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/carve/` | none recorded |
| Duplicate detection | SHA-256 per candidate; identical objects collapse and keep every offset (`duplicate_offsets`) | IMPLEMENTED + TESTED + DEMONSTRABLE (SYNTHETIC) | `tests/scripts/test_demo_fragmented.py`, `scripts/demo_fragmented.py` | none recorded |
| Evidence score, components, ranking | six components, buckets HIGH/MEDIUM/LOW (`core/carve/score.py`) | IMPLEMENTED + TESTED + DEMONSTRABLE | calibration docs; Recovery screen Score breakdown | An evidence score, not a probability; a reassembled object is never HIGH |
| Preview where safe | real recovered-image preview, HTML/SVG never rendered | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/api/test_artifacts.py`, `ui/tests/artifacts.test.ts` | none recorded |
| Provenance, raw offsets | every candidate carries offset, length, runs and SHA-256 | IMPLEMENTED + TESTED + DEMONSTRABLE | `core/models.py:CarveCandidate` | none recorded |
| Undelete from filesystem metadata | NTFS, FAT12/16/32, exFAT, ext2/3/4 via pytsk3 | IMPLEMENTED + TESTED + DEMONSTRABLE (SYNTHETIC) | `docs/performance/benchmark.md` | ext4 recovers almost nothing by design |
| PII triage | counts identity and financial shapes, stores no values | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/carve/` | Reads only some types |
| Read-only evidence path | `core/carve/evidence.py` opens `O_RDONLY` and has no write method | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/carve/` | none recorded |

## A4. Filesystem and format support

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| FAT/FAT32, exFAT | undelete, carve, file erase, free-space wipe (Linux) | IMPLEMENTED + TESTED + DEMONSTRABLE | `docs/platform-support.md` | File erase unverifiable by extents |
| NTFS | undelete, carve, file erase | IMPLEMENTED + TESTED + DEMONSTRABLE | same | Resident files and journal copies are reported, not removed |
| ext4 | undelete (little by design), carve, file erase, free-space wipe | IMPLEMENTED + TESTED + DEMONSTRABLE | same | none further |
| APFS | carve only; file erase runs on macOS, NOT VERIFIABLE | PARTIAL | same | No APFS undelete |
| Raw image, E01 | raw images and E01 acquisition | PARTIAL | `docs/performance/acquisition.md` | E01 writing depends on the libewf build; uncompressed |

## B/C. Forensic integrity and tamper-evident reporting

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Hash-chained ledger | entry N holds SHA-256 of N-1; BROKEN vs INCOMPLETE_TAIL (`core/ledger/chain.py`) | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/ledger/` | An insider with the whole state directory can rebuild it; only an external anchor prevents that, and none is configured by default |
| Evidence hashing | SHA-256 and BLAKE3 at acquisition; SHA-256 per carved object | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/carve/` | none recorded |
| Case ID, operator, timestamps, tool version | report `case_identity` | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/api/test_cases.py`, `tests/api/test_operator_identity.py` | Operator is a local account, not a verified person |
| Source device identity and serial | report `device_identity`, by-id path | IMPLEMENTED + TESTED + DEMONSTRABLE | report section 2 | none recorded |
| JSON report (authoritative) | Ed25519 over canonical JSON | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/report/` | Embedded key proves consistency, not identity |
| PDF report (human-readable) | `render_pdf`, states it is not authoritative | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/report/test_render.py` | Not signed; editable by design |
| HTML report | none | UNSUPPORTED | none | The PDF and the UI cover human reading |
| Report verification | `verify-report`: signature, fingerprint vs genesis, excerpt chain, store chain, blobs | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/report/`, fallback verified 5/5 on 2026-09-23 | Identity needs an out-of-band fingerprint |
| Graded report verdict | `VERIFIED`, `VERIFIED_WITH_LIMITATIONS`, `PARTIAL`, `FAILED_VERIFICATION`, with a reason for every downgrade; CLI, API and Audit screen | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/report/test_report_verdict.py`, `tests/api/test_report_verdict_api.py`, `ui/tests/verdict.test.ts`; fallback reads `VERIFIED_WITH_LIMITATIONS` | Exit code still follows `Result:`, not the verdict |
| Tamper demonstration | server-side copy, real verifier (`api/routes/audit.py:tamper_demo`) | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/api/test_tamper_demo.py` | none recorded |
| Merkle root and anchor receipt | in every report | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/api/test_report_anchor.py` | No external witness ships |
| Chain-of-custody timeline UI | Cases screen: evidence, operations, reports, audit events | PARTIAL | `ui/src/screens/Cases.tsx` | Not presented as one who/what/when timeline |

## D. Certificate

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Sanitization certificate | the signed erase report is the certificate: device, serial, capacity, method, rationale, verification, residual risk, limitations, tool version, chain status, signature | IMPLEMENTED + TESTED + DEMONSTRABLE | `core/report/render.py`, `docs/compliance.md` (Sec. 4.6 / Appendix C mapping) | Signed with a local Ed25519 key, not a PKI. Label: "cryptographically integrity-protected", never "government-signed" |
| Verifier identity (second person) | none | UNSUPPORTED | none | Only the operator's local account is recorded |

## E. Safety of destructive operations

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Stable identity, independent serial sources | by-id path; `lsblk` vs sysfs serial cross-check | IMPLEMENTED + TESTED + DEMONSTRABLE (PHYSICAL preflight) | `scripts/media_benchmark.py`, preflight run 2026-09-23 | Both sources report what the firmware says |
| Mounted, root and system refusal | preflight and `core/device/guard.py` | IMPLEMENTED + TESTED + DEMONSTRABLE (PHYSICAL) | preflight refusal on the attached stick, 2026-09-23 | none recorded |
| Typed serial, acknowledgement, dry-run default | API and `media_benchmark.py write` | IMPLEMENTED + TESTED + DEMONSTRABLE | `tests/scripts/`, `tests/api/` | none recorded |
| Verified backup on another disk, hash and extent binding | `verify_backup`, re-run by `write_image` | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/scripts/test_media_benchmark*.py` | Restoration never validated |
| No automatic sudo or unmount | no code path does either | IMPLEMENTED + TESTED + DEMONSTRABLE | refusal text | none recorded |
| Visible workflow state machine | `core/workflow.py`: eleven states, `derive()` with WHY BLOCKED and next action, `advance()` refuses illegal transitions; printed at the top of the benchmark `plan` | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `tests/test_workflow.py`, `tests/scripts/test_media_benchmark_boundary.py` | A model, not a gate: the write path still re-checks everything itself and does not read it. Not yet shown in the Sanitize screen |
| Missing device path in `plan` / `verify-backup` | these two commands raise a `CalledProcessError` traceback instead of a clean refusal when the path does not exist | PARTIAL | observed 2026-09-24 | Fails closed: nothing is created or written, and `write` checks the path first. Left unchanged because the harness is protected |

## F. Simulation mode

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Full workflow with no device | dry run for erase jobs; offline demo state (`scripts/demo_setup.py`); fallback report; `scripts/demo_fragmented.py` | PARTIAL | `tests/scripts/test_demo_workflow.py`, `tests/scripts/test_demo_fragmented.py` | No single mode walks discovery to certificate on its own; the demo index strings the real steps together |
| Simulation label on screen | *SIMULATION / NO PHYSICAL DEVICE MODIFIED* on every screen showing a dry-run job, decided from the flag the server recorded | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `ui/tests/simulation.test.ts` | Not checked in a browser in this work |

## G/H/I. Benchmarking, demo corpus, performance

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Precision, recall, false positives, corrupt recoveries | `testkit/benchmark.py`, `testkit/calibrate.py` | IMPLEMENTED + TESTED + DEMONSTRABLE (SYNTHETIC) | `docs/performance/benchmark.md`, `calibration-pooled.md` | Synthetic images only |
| Physical benchmark | `scripts/media_benchmark.py` | PARTIAL | preflight only; run blocked on the methodology decision | No physical result exists |
| Deterministic corpus with ground truth | `testkit/generate_corpus.py`, `testkit/damage.py`, `testkit/fsimage.py` | IMPLEMENTED + TESTED + DEMONSTRABLE (SYNTHETIC) | `tests/testkit/` | Fragmented PNG/PDF cases are not reconstructable |
| Throughput and memory | 1 GiB and 7 GiB runs, peak RSS | IMPLEMENTED + TESTED + DEMONSTRABLE (SYNTHETIC) | `docs/validation/large-image.md` | Measured on one host |

## J. Cross-platform

See `docs/platform-support.md` for the full matrix. Summary: whole-drive
sanitization is Linux only and UNSUPPORTED on Windows and macOS, with a
reason. File erase is validated in CI on all three. Packages build in CI and
are unsigned and not notarized.

## K/L/M. UI, demo, judge questions

| SIH requirement | Capability | Status | Evidence | Limitation |
|---|---|---|---|---|
| Device card with capability and blocked reason | Devices and Sanitize screens | IMPLEMENTED + TESTED + DEMONSTRABLE | `ui/src/screens/Devices.tsx`, `Sanitize.tsx` | none recorded |
| Landing screen with four workflows | Overview screen: Recover Evidence, Secure Erase, File / Folder Erase, Verify Report, each with the server's live status | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `ui/src/screens/Home.tsx`; TypeScript build | Not checked in a browser in this work |
| Executive summary screen | WHAT WAS FOUND / ERASED / HOW VERIFIED / WHAT REMAINS UNVERIFIED from the case record; dry runs never counted as erasures | IMPLEMENTED + TESTED + NOT YET DEMONSTRATED | `ui/tests/summary.test.ts` | Not checked in a browser in this work |
| Demo runbook | `docs/demo/runbook.md` | IMPLEMENTED | rehearsal pending | Needs the physical stick for the live beats |
| Judge Q&A | `docs/demo/qa.md`, 29 questions, each traced to code or a record | IMPLEMENTED | `docs/demo/qa.md` | Power loss is answered from design and tests; no real power cut has been recorded |

## N. Standards

| Claim | Status | Evidence |
|---|---|---|
| NIST SP 800-88 Rev. 2 vocabulary (Clear / Purge / Destroy) | Aligned, including `CLAUDE.md`. Rev. 1 withdrawn 2025-09-26 | `docs/compliance.md` |
| IEEE 2883-2022, ISO/IEC 27040:2024 | Mapped, not claimed as compliance | `docs/compliance.md` |
| DoD 5220.22-M | Legacy method offered by the engine with a warning; not offered in the UI; no compliance claim | `docs/compliance.md`, `core/erase/drive.py` |
