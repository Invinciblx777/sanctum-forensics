# Demo evidence index

**Date:** 2026-09-24. This is the source of truth for the presentation. Every
claim a presenter makes maps to real software output: a screen, a command, a
test that pins it, and the recorded artifact behind it. A claim with no row
here does not get said.

Population labels, never mixed: **SYNTHETIC** (generated on host storage),
**PHYSICAL** (the one Toshiba TransMemory stick in `hardware.md`), **CI**
(virtual disks on hosted runners), **FALLBACK** (the committed signed report
and ledger under `docs/demo/fallback/`, which are not a physical-device result).

## The 3 to 5 minute demo, without the physical stick

Each step is about 25 seconds. The order follows section L of the upgrade brief.

| # | Step | Screen | Command (if the screen fails) | Pinned by | Evidence / artifact | Population |
|---|---|---|---|---|---|---|
| 1 | Discover a device | Overview, then Devices | `.venv/bin/python -c "from core.device.enumerate import enumerate_devices; import json; print(json.dumps([d.model_dump(mode='json') for d in enumerate_devices()], indent=2))"` | `tests/device/` | `docs/platform-support.md` (device discovery row) | live host |
| 2 | Show the safety checks | Devices badge and tooltip; Sanitize capability panel | same as 1 | `tests/device/`, `tests/erase/test_erase_preview.py` | capability evidence in report section 3 | live host |
| 3 | Show the mounted-device refusal | none (terminal) | `.venv/bin/python scripts/media_benchmark.py preflight --device /dev/disk/by-id/<id> --expect-serial <serial>` | `tests/scripts/test_media_benchmark_preflight.py`, `tests/scripts/test_media_benchmark_boundary.py` (plan reads `STATE BLOCKED`) | Refusal recorded on the stick on 2026-09-23: *has mounted filesystems: /run/media/…/SANCTUMREC. Unmount them yourself …* (`docs/demo/qa.md` §24) | PHYSICAL (recorded), or live if attached |
| 4 | Switch to simulation | Sanitize, dry run on; banner *SIMULATION / NO PHYSICAL DEVICE MODIFIED* | `POST /jobs/erase-drive` with `dry_run` omitted | `ui/tests/simulation.test.ts`, `tests/erase/test_erase_preview.py` | `api/routes/models.py` (`dry_run: bool = True`) | live host, no write |
| 5 | Recover difficult fragmented evidence | Recovery (for an image), or terminal | `.venv/bin/python scripts/demo_fragmented.py` | `tests/scripts/test_demo_fragmented.py`, `tests/carve/signature/test_png_fragmentation.py` | `docs/validation/png-reassembly.md` (120/120 to 7 MiB, 0/800 wrong accepts) | SYNTHETIC |
| 6 | Explain the evidence score | Recovery, Score breakdown panel; the `why` line of step 5 | step 5 output | `tests/report/test_confidence_is_not_a_probability.py`, `ui/tests/format.test.ts` | `docs/performance/calibration-pooled.md` | SYNTHETIC |
| 7 | Show the forensic report | Audit, report generator; Cases | `POST /reports/{job_id}` | `tests/report/test_render.py`, `tests/api/test_report_anchor.py` | `docs/demo/fallback/demo-erase.forensic.json` | FALLBACK |
| 8 | Show tamper verification | Audit, *Simulate tampering* (server copies to a scratch directory) | `SANCTUM_KEY_PASSPHRASE=sanctum-demo .venv/bin/python -m core.report.cli verify-report docs/demo/fallback/demo-erase.forensic.json --ledger-root docs/demo/fallback/demo-ledger` | `tests/api/test_tamper_demo.py`, `tests/report/test_report_verdict.py` | 5/5 PASS, `Verdict: VERIFIED_WITH_LIMITATIONS` on 2026-09-23 | FALLBACK |
| 9 | Show sanitization planning | Sanitize, plan panel after a dry run | `.venv/bin/python scripts/media_benchmark.py plan --device <by-id> --work <dir> --expect-serial <serial>` (prints STATE, WHY BLOCKED, NEXT) | `tests/test_workflow.py`, `tests/scripts/test_media_benchmark_boundary.py` | `core/workflow.py` | live host, no write |
| 10 | Show the certificate | Audit, open PDF and JSON | same as 8 | `tests/report/test_sign.py`, `tests/report/test_verify_report.py` | the signed JSON is the certificate; say *cryptographically integrity-protected*, never *government-signed* | FALLBACK |
| 11 | Show the benchmark comparison | none (slide or document) | `docs/performance/benchmark.md` §Results | `tests/testkit/` | Sanctum rows re-run 2026-09-21; calibration pooled over 8 seeds | SYNTHETIC |

**Do not use the tamper step (8) on the fallback files directly.** The runbook
flips a byte in a copy under `/var/lib/sanctum-demo/`; the fallback copy must
stay byte-identical. The Audit screen's *Simulate tampering* works on a
server-side scratch copy and never touches the live tree.

## Claims and where each one is proved

| Claim | Screen | Command | Test | Report / benchmark | Artifact |
|---|---|---|---|---|---|
| The erase method comes from probed capability, not preference | Sanitize | device probe (step 1) | `tests/device/`, `tests/erase/test_erase_preview.py` | report `method.capability_evidence` | `core/device/capabilities.py` |
| Dry run is the default and writes nothing | Sanitize, File eraser (banner) | `POST /jobs/*` without `dry_run` | `ui/tests/simulation.test.ts` | none | `api/routes/models.py` |
| A mounted device is refused, with no sudo and no unmount | none | preflight (step 3) | `tests/scripts/test_media_benchmark_preflight.py` | `docs/demo/qa.md` §24 | refusal text, 2026-09-23 |
| The write re-verifies the backup itself | none | `media_benchmark.py write` (not demonstrated) | `tests/scripts/test_media_benchmark_boundary.py` | `docs/demo/qa.md` §25 | `scripts/media_benchmark.py:write_image` |
| Workflow state and WHY BLOCKED are named | none | `media_benchmark.py plan` | `tests/test_workflow.py` | none | `core/workflow.py` |
| Split PNG and JPEG are rebuilt only when their bytes prove the join | Recovery | `scripts/demo_fragmented.py` | `tests/carve/signature/test_png_fragmentation.py`, `tests/carve/signature/test_fragmentation.py` | `docs/validation/png-reassembly.md`, `docs/limitations.md` | `core/carve/fragmentation.py` |
| A reassembled object is never HIGH | Recovery | step 5 output (`reassembly -1001`) | `tests/scripts/test_demo_fragmented.py` | `docs/limitations.md` | `core/carve/score.py` |
| The evidence score is not a probability | Recovery | step 5 output | `ui/tests/format.test.ts`, `tests/report/test_confidence_is_not_a_probability.py` | `docs/performance/calibration-pooled.md` | `core/carve/score.py` |
| Duplicates are reported once with every offset | Recovery | step 5 output | `tests/scripts/test_demo_fragmented.py` | none | `core/carve/classify.py:dedupe` |
| Report bytes are tamper-evident | Audit | `verify-report` (step 8) | `tests/api/test_tamper_demo.py` | none | fallback report |
| The verdict never rounds a limited report up | Audit | `verify-report` | `tests/report/test_report_verdict.py`, `ui/tests/verdict.test.ts` | `docs/demo/qa.md` §29 | `core/report/verify_report.py:grade_report` |
| The ledger is hash-chained and a torn tail is not a break | Audit | `GET /ledger/verify` | `tests/ledger/` | `docs/demo/qa.md` §22 | `core/ledger/chain.py` |
| The executive summary never counts a dry run as an erasure | Overview | none | `ui/tests/summary.test.ts` | none | `ui/src/lib/summary.ts` |
| Overwrite was run on a real USB flash stick | none | none | none | `docs/validation/hardware.md` | six recorded runs, 2026-09-05 |

## Captures still required from the live rehearsal

These three files do not exist. Nothing here generates them, and no placeholder
is acceptable.

| File | What it must show | Runbook line |
|---|---|---|
| `docs/demo/fallback/devices.png` | Devices screen, stick listed, `CLEAR ONLY` badge, tooltip open | `docs/demo/runbook.md:260` |
| `docs/demo/fallback/recovery.png` | Recovery filtered to HIGH, a candidate selected, all score components readable | `docs/demo/runbook.md:523` |
| `docs/demo/fallback/wipe-start.mp4` | confirm dialog, PREFLIGHT, plan panel, `CONTROLLER_WRITE_ELISION` at HIGH, residual risk **high** | `docs/demo/runbook.md:371` |

Line numbers were read on 2026-09-24 and move when the runbook is edited.

## What must not be said

- That the system was benchmarked on real media for recovery. No physical
  benchmark result exists.
- That the fallback report is a physical-device result.
- That any certificate is government-signed or PKI-backed.
- That firmware Purge (ATA SANITIZE, SECURITY ERASE, NVMe sanitize/format) has
  run on a drive. It has been selected and dispatched only.
- That general fragmented-file reconstruction is solved. Two runs, two formats.
- That the evidence score is a probability or a confidence percentage.
- Compliance with DoD 5220.22-M, or with NIST SP 800-88 beyond using its
  vocabulary and mapping to it.
