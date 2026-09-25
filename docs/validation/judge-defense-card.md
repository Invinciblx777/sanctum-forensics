# Judge defense card

**Date:** first written 2026-09-24 at `8e1777d`; reconciled 2026-09-26 with the
release-hold remediation · **Code and packages at:** `76dde42` (see
[`release-report-2026-09-25.md`](release-report-2026-09-25.md)) · **Physical
validation in this release: none** — every PHYSICALLY VALIDATED row below is a run
on 2026-09-05 or 2026-09-23 with an earlier build · Companion to
[`demo-evidence-index.md`](demo-evidence-index.md) and
[`feature-matrix.md`](feature-matrix.md), which stay the source of truth. If
this card and either of them disagree, they win and this card is wrong.

Rules for the presenter:

1. Answer in one or two sentences, then point at the evidence. Do not argue.
2. Say the population label out loud. Never let a synthetic or simulated
   number stand in for a physical one.
3. If a row says **NOT CURRENTLY PROVEN**, say exactly that. Do not improvise
   a better answer.

## Evidence labels

These five labels are used on every row below and in the final slide. They
never merge.

| Label | Meaning | What is in it |
|---|---|---|
| **PHYSICALLY VALIDATED** | Ran on a real device, recorded in `hardware.md`, with the build of that date | One Toshiba TransMemory USB stick (7.76 GB): three Phase A overwrite Clear runs (the third clean, 2026-09-05), a power-cycle re-verification, three Phase B recovery passes, the mounted-device refusal (2026-09-23). None was repeated for the current release |
| **SYNTHETICALLY VALIDATED** | Generated images with a ground-truth manifest, on host storage | Recovery benchmark and calibration, PNG/JPEG bifragment reassembly, 1 GiB and 7 GiB runs, fuzz |
| **SIMULATION ONLY** | Host files standing in for a device, run through the real engine | `scripts/demo_simulation.py`; every dry run; the Sanitize screen browser checks (fixture devices) |
| **DOCUMENTED** | A written mapping or procedure, not an executed result | NIST SP 800-88 Rev. 2 / IEEE 2883 / ISO 27040 mapping, runbook, this card |
| **HARDWARE-UNVERIFIED** | Software path tested with fixtures, never run on a real device | ATA SANITIZE, ATA SECURITY ERASE, NVMe sanitize/format, crypto erase, HPA/DCO unlock, backup restoration, the registered physical recovery benchmark |

CI-validated (virtual disks on hosted runners) is its own population. It is
not physical validation.

## Interruptions: one-line answers

| They say | Say | Show |
|---|---|---|
| "Why?" | Answer the reason for the thing on screen in one sentence, then go back to the beat. If there is no reason on record, say "that is a design choice, not a measured one." | the row below for that beat |
| "What proves that?" | Name the test file or the recorded run. | the Evidence column of the row below |
| "Is that real hardware?" | Physical only for the overwrite Clear and the recovery passes on one USB stick (2026-09-05) and the mounted-device refusal (2026-09-23), all with earlier builds; nothing was run on hardware for this release. Everything else on screen is synthetic or simulation, and it is labelled. | Overview, LIMITATIONS: NOT PHYSICALLY VALIDATED column; `hardware.md` run table |
| "Can you demonstrate it?" | Check the CAN DEMONSTRATE LIVE? column in `demo-evidence-index.md`. YES: run it. SIMULATION ONLY: run it and say "simulation". Otherwise: "not live; here is the recorded artifact." | `demo-evidence-index.md` |
| "What happens if this fails?" | The operation stops, the job is recorded as failed in the ledger, and no sanitization level or certificate is issued. It never falls back to a weaker method or another device. | Q5, Q8, Q30 below |
| "How is this different from PhotoRec?" | PhotoRec returns files. Sanctum also returns why it believes each one: six named evidence components, a bucket calibrated against ground truth, and a split file rebuilt only when its own bytes prove the join. Every run is written to a signed, hash-chained record. | Recovery screen score breakdown; `scripts/demo_fragmented.py` |
| "How is this different from formatting the drive?" | A format rewrites filesystem metadata and leaves the data. On the physical stick, after a FAT32 quick format, carving still recovered all 10 of the 10 carvable planted files byte-exact. Sanitization overwrites or purges every addressable sector, verifies it by reading it back, and says what it could not reach. | `hardware.md` Phase B, `results-20260905T082656Z` |

## Adversarial audit: 37 questions

Status is one of the five labels above, or **NOT CURRENTLY PROVEN**. Paths
are relative to the repository root.

### Safety

| # | Question | Answer | Evidence | Status |
|---|---|---|---|---|
| 1 | How can the wrong disk be selected? | The operator must type the serial of the selected disk. A mismatch refuses. The system disk and any disk with a mounted filesystem are refused before that. | `core/device/guard.py:assert_erasable`, `assert_serial_confirmed`; `tests/device/test_guard.py`; Devices screen WHY BLOCKED (`browser-2026-09-24/06`) | SYNTHETICALLY VALIDATED (unit tests). Mounted refusal PHYSICALLY VALIDATED. The typed-serial gate was not reached on the physical host: `qa.md` §14 |
| 2 | What prevents `/dev/sda` changing to another device? | Three re-reads. The API gate compares serial, model and size with what was approved. The helper does it again at the write seam, with plan and backup, before the engine starts. The engine then re-reads the serial and the `/dev/disk/by-id` link and raises `DeviceVanished` on a difference. | `api/authorization.py`, `helper/authorization.py`, `core/erase/drive.py:_reread_serial`; `tests/helper/test_write_seam_authorization.py`, `tests/api/test_write_seam_integration.py`; `tests/erase/test_drive_loopback.py::test_serial_swap_between_confirmation_and_execution_is_caught` | API gate and helper seam: SYNTHETICALLY VALIDATED, including a change made after the API gate passed. Engine re-read: the test needs root and a loop device and is **skipped** in the unprivileged suite, NOT CURRENTLY PROVEN. Race freedom between the helper's check and the first write is not claimed |
| 3 | What happens if the filesystem is mounted? | Refused, with the mount point named. No automatic unmount. The workflow state reads BLOCKED with WHY BLOCKED. | `core/device/guard.py`; `tests/device/test_guard.py`; `tests/scripts/test_media_benchmark_preflight.py`; `qa.md` §24 | PHYSICALLY VALIDATED (refusal on the stick, 2026-09-23; `/sys/block/sda/stat` unchanged, so no I/O) |
| 4 | What happens if the serial does not match? | `ConfirmationMismatch` with the remediation "Nothing has been modified". The erase does not start. | `core/device/guard.py:assert_serial_confirmed`; `tests/device/test_guard.py`, `tests/helper/test_daemon.py`; Sanitize screen: the approve and erase buttons stay disabled until the typed serial matches, and a mismatch the server sees is a structured REFUSED (`browser-2026-09-25/05`) | SYNTHETICALLY VALIDATED; UI on fixture devices |
| 5 | What happens if the device disappears? | Before the job: `DeviceVanished`, nothing written. In the benchmark harness a missing or stale by-id path is a structured refusal, exit 2, and no other device is tried. During a write: the non-EIO OS error ends the job as `failed`, recorded by the durable job outcome. No verification runs, so no level and no certificate. | `core/device/enumerate.py`, `tests/device/test_enumerate.py::test_get_device_raises_device_vanished`; `tests/scripts/test_media_benchmark_absent_device.py`; `api/jobs.py` (failed state), `api/durable.py` | Before the job: SYNTHETICALLY VALIDATED. Removal **during** a write: no test injects it, and no physical unplug is recorded. NOT CURRENTLY PROVEN |
| 6 | What happens if backup verification fails? | A real whole-drive erase is refused: the server will not open the workflow without a backup image it has hashed and sized to cover the device, and the helper refuses at the write seam if the image changed. The benchmark write refuses too: it re-runs its own backup verification straight before writing, and writes only if `sufficient_for_restoring_the_modified_region` is true. | `scripts/media_benchmark.py:verify_backup`, `write_image`; `tests/scripts/test_media_benchmark_boundary.py`; `qa.md` §25 | SYNTHETICALLY VALIDATED. The benchmark write re-verifies its own backup; a real whole-drive erase, since 2026-09-25, needs a backup image the server hashed and sized, bound by size, mtime, ctime and inode and re-checked by the helper (`tests/test_authorization_binding.py`). Neither proves the image is a copy of the device. Restoration HARDWARE-UNVERIFIED |
| 7 | What happens if permission is denied? | A privilege failure during probing raises and says to run the privileged helper. It is never reported as "unsupported", so a method is never downgraded because the tool could not see the device. | `core/device/capabilities.py`; `tests/device/test_capabilities.py::test_permission_error_raises_rather_than_reporting_unsupported`, `::test_usb_bridge_permission_error_still_raises` | SYNTHETICALLY VALIDATED |
| 8 | What happens if I/O fails halfway through? | An EIO span is retried block by block. Unwritable blocks are recorded with their errno, the wipe continues, and verification then fails for that region. Any other I/O error stops the job as failed. | `core/erase/drive.py:_write_block_by_block`; `tests/erase/test_overwrite_file.py::test_eio_is_recorded_and_the_wipe_continues`, `::test_eio_region_makes_verification_fail`; `tests/erase/test_residual_risk.py` | SYNTHETICALLY VALIDATED (injected errors). No physical bad-sector run recorded |
| 9 | Can the application auto-unmount? | No. No code path calls `umount` or `udisksctl`. The refusal tells a human to unmount. | `core/device/guard.py`; refusal text in `qa.md` §24; Overview SAFETY column | SYNTHETICALLY VALIDATED; refusal PHYSICALLY VALIDATED |
| 10 | Can it silently escalate privileges? | No. The UI and API run unprivileged. Raw device work goes through a separate helper that a human starts with `sudo`, over a static allowlist of typed operations. No code calls `sudo` or `pkexec`. | `helper/__main__.py`, `helper/daemon.py`; `docs/privilege-boundary.md`; `tests/helper/`; status strip `PRIVILEGE: Standard user` | SYNTHETICALLY VALIDATED; DOCUMENTED |
| 11 | What requires human approval before execution? | Whole-drive erase: dry run off, a backup image, an explicit acknowledgement with the typed serial (recorded as the approval, against the OS account; the API does not authenticate a person), then the typed serial again to spend the one-use authorization. Benchmark write: a recorded methodology decision, identity reconfirmation, manual unmount, a verified backup, a reviewed plan, and running `write` with the acknowledgement flag. | `api/routes/models.py` (`dry_run` default true); `api/routes/workflow.py`; `core/workflow.py` (HUMAN_APPROVAL_REQUIRED); `physical-benchmark-checklist.md` | SYNTHETICALLY VALIDATED; UI on fixture devices (`browser-2026-09-25/`) |

### Recovery

| # | Question | Answer | Evidence | Status |
|---|---|---|---|---|
| 12 | How does recovery differ from ordinary signature carving? | Signatures only find candidates. Each candidate is then bounded by the format's own length fields, decoded, scored on named evidence, de-duplicated by SHA-256, and cross-checked with surviving filesystem metadata where it exists. | `core/carve/structure.py`, `validate.py`, `score.py`, `classify.py`; `docs/supported-formats.md` | SYNTHETICALLY VALIDATED; Phase B passes PHYSICALLY VALIDATED |
| 13 | How is fragmentation detected? | A candidate whose structure parse does not close is flagged `possibly_fragmented`. | `core/models.py`; `tests/carve/` | SYNTHETICALLY VALIDATED |
| 14 | How is a candidate validated? | A real decoder reads it (valid, truncated, corrupt, or no decoder), PNG chunk CRCs are checked, and entropy is compared with the format. A reassembled PNG must pass every chunk CRC-32 and a zlib stream that inflates to exactly the header size. A reassembled JPEG must pass an exact Huffman scan count. | `core/carve/validate.py`, `core/carve/fragmentation.py`; `tests/carve/test_validate_malformed.py`, `tests/carve/signature/test_png_fragmentation.py` | SYNTHETICALLY VALIDATED |
| 15 | What does the evidence score mean? | A sum of named components in basis points: header 2000, exact length 1500, decoder 4000, entropy 1000, filesystem metadata 1500, no overlap 500, with a reassembly hold that keeps a rebuilt object at 7999. HIGH is 8000 or more. | `core/carve/score.py`; Recovery screen score breakdown (`browser-2026-09-24/03`); `scripts/demo_fragmented.py` `why` line | SYNTHETICALLY VALIDATED |
| 16 | Why is it not a probability? | The components sum to 10,500 and are clamped at 10,000, so the top of the scale is a clamp, not a certainty. What was measured is a bucket's precision on a population: 104 of 104 HIGH byte-exact over eight synthetic seeds. That is not a rate for seized media. | `core/carve/score.py` docstring; `docs/performance/calibration-pooled.md`; `tests/report/test_confidence_is_not_a_probability.py`, `tests/ui/test_no_percent_on_evidence_score.py` | SYNTHETICALLY VALIDATED |
| 17 | How are false positives handled? | They are measured against ground truth and reported per bucket. Weights were moved only where the calibration showed it. A footer-bound defect that let 39 wrong PDFs into HIGH on the 7 GiB run was found and fixed (247 of 247 HIGH after). 0 of 800 deliberately wrong PNG joins were accepted. | `docs/performance/calibration.md`; `docs/validation/large-image.md`; `docs/validation/png-reassembly.md`; `tests/carve/signature/test_footer_bound.py` | SYNTHETICALLY VALIDATED |
| 18 | What is proven only on synthetic data? | Recall and precision figures, calibration weights, bifragment reassembly, and the 1 GiB and 7 GiB throughput. The registered physical recovery benchmark has not been run. | `docs/performance/benchmark.md` (states SYNTHETIC in its first paragraph); `methodology-open-decision.md` | SYNTHETICALLY VALIDATED; physical benchmark HARDWARE-UNVERIFIED |

### Integrity

| # | Question | Answer | Evidence | Status |
|---|---|---|---|---|
| 19 | How do you detect a changed forensic report? | `verify-report` runs five checks: Ed25519 signature over canonical JSON, key fingerprint against the ledger genesis, the chain excerpt inside the report, the store chain, and the blobs. One changed field fails verification. | `core/report/verify_report.py`; `tests/report/`; `tests/api/test_tamper_demo.py`; Audit screen *Simulate tampering* (`browser-2026-09-24/04`); `demo_simulation.py` last stage | SYNTHETICALLY VALIDATED; also run against the physical-run report (`hardware.md` A.7) |
| 20 | How do the ledger links work? | Entry N holds the SHA-256 of entry N-1 over canonical JSON. A torn last line is `INCOMPLETE_TAIL`, not `BROKEN`, and every earlier entry still verifies. | `core/ledger/chain.py`; `tests/ledger/test_chain.py` | SYNTHETICALLY VALIDATED |
| 21 | What does the Ed25519 signature prove? | That whoever held this private key signed exactly these bytes, and that the bytes have not changed since. | `core/report/sign.py` docstring; `tests/report/test_sign.py` | SYNTHETICALLY VALIDATED |
| 22 | What does it NOT prove? | Who signed it. The public key is embedded, so identity needs the fingerprint from another channel. The key is local, not PKI or government-issued. Someone with write access to the whole state directory can rebuild the chain unless an external anchor is configured, and none is by default. | `core/report/sign.py`; `docs/limitations.md`; Overview INTEGRITY column ("proves the report was not altered, not who signed it") | DOCUMENTED |
| 23 | What is in the certificate? | The signed erase report: case identity, device identity (model, serial, by-id path, capacity, block sizes), method with justification and probed capability, hidden areas, verification, residual risk, limitations, audit-trail excerpt, tool version, key fingerprint, signature. | `core/report/render.py:build_report`; `docs/demo/fallback/demo-erase.forensic.json`; `docs/validation/results-20260905T033655Z/reports/HW-VALIDATION.forensic.json` (physical run); `docs/compliance.md` | SYNTHETICALLY VALIDATED; the HW-VALIDATION report is from the PHYSICALLY VALIDATED run |

### Sanitization

| # | Question | Answer | Evidence | Status |
|---|---|---|---|---|
| 24 | How is the method selected? | From probed capability: `hdparm -I`, NVMe Identify, `sedutil-cli`, sysfs. The strongest mechanism the device reports is chosen, with the justification in the report. If the requested level cannot be reached the job is NOT AUTHORIZED; it never silently downgrades. The UI has no method chooser. | `core/device/capabilities.py`, `core/erase/drive.py:select_method`; `tests/erase/test_select_method.py`, `tests/erase/test_erase_preview.py`; report `method.justification` | SYNTHETICALLY VALIDATED |
| 25 | What happens on SSD/NVMe/flash? | Purge only through the device's own firmware (sanitize, crypto erase). Host overwrite on flash is at most Clear, and every flash report says overwrite cannot reach remapped or over-provisioned blocks. A write calibration catches controllers that fake zero writes. | `core/device/capabilities.py:purge_mechanisms`, `recommend_method`; `tests/device/test_purge_by_device_class.py`; `core/erase/calibrate.py`; `core/platform/base.py:FLASH_LIMITATION` | USB flash overwrite Clear PHYSICALLY VALIDATED; firmware Purge HARDWARE-UNVERIFIED |
| 26 | What is validated on physical hardware? | Overwrite Clear with read-back verification on one USB flash stick, including detection of zero-write elision (zero fill acknowledged 3.25 to 3.6 times faster than the medium programs), a power-cycle re-verification, three recovery passes, and the mounted-device refusal. | `docs/validation/hardware.md`; `results-20260905T033655Z` | PHYSICALLY VALIDATED (one device, one model, 2026-09-05 and 2026-09-23, earlier builds; not re-run for this release) |
| 27 | What is still firmware-unverified? | ATA SANITIZE, ATA SECURITY ERASE, NVMe sanitize and format, SED crypto erase, HPA/DCO unlock. Selected and dispatched in fixture tests only. | `tests/erase/`, `tests/device/`; `feature-matrix.md` A1 | HARDWARE-UNVERIFIED |
| 28 | What if the device reports misleading capacity? | HPA and DCO are detected by comparing accessible and native max sectors. A nonsense reading from a USB bridge is discarded and recorded as "no determination", after it once produced a 512-byte "erase". The engine erases no less than the kernel's `BLKGETSIZE64`. A controller that lies consistently to every read cannot be caught from the host, and the report says so. | `core/device/hidden_areas.py`; `tests/device/test_hidden_areas.py`; `core/erase/drive.py` (kernel size floor); `qa.md` §23; `hardware.md` run 1 | Bridge discard PHYSICALLY VALIDATED (on the stick, 2026-09-05: the probe was skipped behind the USB bridge, so no hidden area was measured or unlocked); HPA/DCO detection and unlock on a drive that has a hidden area HARDWARE-UNVERIFIED |
| 29 | How are HPA/DCO limitations handled? | Detected and reported, unlocked only when the method needs it, and restored afterwards. Linux only, and it needs ATA pass-through, which most USB bridges block. When it cannot be probed, the report and the verdict (`VERIFIED_WITH_LIMITATIONS`) say so. | `core/device/hidden_areas.py`; `tests/erase/test_hidden_area_phases.py`; `qa.md` §29 | Detection SYNTHETICALLY VALIDATED; unlock HARDWARE-UNVERIFIED |
| 30 | What happens after power loss? | The ledger survives a torn write (`INCOMPLETE_TAIL`). An overwrite resumes from the last ledgered checkpoint. A firmware method restarts from the beginning. No certificate for a job that did not finish and verify. | `core/ledger/chain.py`; `tests/api/test_resume.py`; `tests/erase/test_cancelled_erase.py`; `qa.md` §22 | SYNTHETICALLY VALIDATED (cancel and torn tail). A real power cut during an erase: NOT CURRENTLY PROVEN. The power-cycle in `hardware.md` was after the erase finished, not during it |

### Added 2026-09-25: traces, Destroy, the media map

| # | Question | Answer | Evidence | Status |
|---|---|---|---|---|
| 35 | The file is erased, but its thumbnail and its recent-files entry are still there. | Not any more. After a file erase the sweep finds the thumbnail named by the MD5 of the file's URI, the recent-files entry, and older copies in the Trash or Recycle Bin, and removes the ones tied to the path on evidence. A same-name file in the macOS Trash is reported, not removed. Each report lists what was searched and what was not. | `core/erase/traces.py`; `tests/erase/files/test_trace_sweep.py` (31 tests, synthetic homes); report section 6 | SYNTHETICALLY VALIDATED. No test ran against a real desktop session |
| 36 | Where is Destroy? | Destroy is physical: a shredder does it and no software can, or can watch it. Sanctum records what the people who did it attest, chains it and signs it, and the record says the tool observed nothing and did not authenticate the names. | `core/destroy.py`; `tests/test_destroy.py`, `tests/api/test_destroy_record_api.py`; Devices screen, *Record a physical destruction* | SYNTHETICALLY VALIDATED (the record, not a destruction) |
| 37 | What does "intelligent carving" mean here? | Three things you can check: every candidate's score is six named evidence components; a split JPEG or PNG is rebuilt only when its own bytes prove the join; and the media map shows, before carving, where the image is zeroed, filled, text or high-entropy and where file headers sit. The map does not identify content, and says so. | `core/carve/score.py`, `core/carve/fragmentation.py`, `core/carve/mediamap.py`; `tests/carve/test_mediamap.py` | SYNTHETICALLY VALIDATED |

### Evidence

| # | Question | Answer | Evidence | Status |
|---|---|---|---|---|
| 31 | Which benchmark results are synthetic? | The recovery benchmark against PhotoRec and Foremost (40 images), pooled calibration (8 seeds), PNG reassembly, 1 GiB and 7 GiB runs, fuzz (66,000 cases). | `docs/performance/benchmark.md`, `calibration-pooled.md`; `docs/validation/png-reassembly.md`, `large-image.md`, `fuzz.md` | SYNTHETICALLY VALIDATED |
| 32 | Which are physical? | Phase A (three runs, third clean) and Phase B (FAT32 delete 456/456, exFAT delete 460/460, FAT32 quick format 10/10 carvable) on one USB stick, 2026-09-05; mounted refusal 2026-09-23. | `docs/validation/hardware.md`; `results-20260905T*` | PHYSICALLY VALIDATED |
| 33 | Which are documentation-only? | Standards mapping (NIST SP 800-88 Rev. 2, IEEE 2883, ISO/IEC 27040), the judge Q&A, the runbook, this card. | `docs/compliance.md`, `docs/demo/qa.md` | DOCUMENTED |
| 34 | Which are still unverified? | Firmware Purge on any drive, HPA/DCO unlock, backup restoration, the registered physical recovery benchmark, a real power cut, device removal mid-write, whole-drive sanitization on Windows and macOS (refused by design), Windows and macOS on physical devices. | `feature-matrix.md`; `demo-evidence-index.md` "What must not be said"; `docs/validation/hardware-platform-matrix.md` | HARDWARE-UNVERIFIED / NOT CURRENTLY PROVEN |

## What Sanctum does that the presentation is built on

Stated as facts about this tool, with evidence. No comparison or ranking is
claimed.

| Property | Evidence | Label |
|---|---|---|
| Device-aware destructive safety: method from probed capability, never downgraded | Q24 | SYNTHETICALLY VALIDATED |
| Serial and by-id identity binding, re-read before execution | Q1, Q2 | SYNTHETICALLY VALIDATED (re-read test root-only) |
| Backup gate, re-verified by the write itself | Q6 | SYNTHETICALLY VALIDATED |
| Human approval gate: dry run default, backup image, acknowledgement and typed serial, one-use server-issued authorization | Q4, Q11; `browser-2026-09-25/` | SYNTHETICALLY VALIDATED; UI on fixtures |
| Authorization re-checked by the privileged helper at the write seam | Q2; `tests/helper/test_write_seam_authorization.py` | SYNTHETICALLY VALIDATED; race freedom not claimed |
| Evidence score with named components, not a probability | Q15, Q16 | SYNTHETICALLY VALIDATED |
| Bifragment recovery (PNG, baseline JPEG) accepted only when the bytes prove the join | Q14, Q17 | SYNTHETICALLY VALIDATED |
| Tamper-evident hash-chained ledger | Q20 | SYNTHETICALLY VALIDATED |
| Ed25519-signed reports with a graded verdict | Q19, Q21 | SYNTHETICALLY VALIDATED |
| Deterministic simulation from discovery to certificate on the real engine | `scripts/demo_simulation.py`, `tests/scripts/test_demo_simulation.py` | SIMULATION ONLY |
| Reproducible benchmark infrastructure with seeds and manifests | `testkit/benchmark.py`, `testkit/calibrate.py`, `testkit/generate_corpus.py` | SYNTHETICALLY VALIDATED |
| Explicit population labels on every result | this card; `feature-matrix.md`; Overview LIMITATIONS column | DOCUMENTED |
| Zero-write elision detection on real flash | Q26 | PHYSICALLY VALIDATED |

## Spoken script, 4:30

Word counts are measured from the text below. At a natural 130 words per
minute with pauses for the screen, the target is no more than about 85% of
each window spent speaking.

| Window | Say | Show | Words | At 130 wpm |
|---|---|---|---:|---:|
| 0:00–0:30 | "Sanctum does three things. It recovers evidence from an image without ever writing to it. It erases a drive only with a method the drive itself reports it supports. And it signs a tamper-evident record of both. This summary shows what is proven, and on the right, what is not yet physically validated." | Overview | 53 | 24 s |
| 0:30–1:00 | "Every device shows its serial and what it can do. This one has a mounted filesystem, so it is blocked, and the tool tells a human to unmount it. It never unmounts or escalates on its own. A missing device path is refused, and no other device is substituted." | Devices; then the `media_benchmark.py plan` refusal | 49 | 23 s |
| 1:00–1:45 | "This is a simulation, and it says so. The real engine runs from discovery to certificate on host files. The mounted medium is blocked. The other is overwritten and read back in full, a signed report is produced, and when we change one field of that report, verification fails." | `scripts/demo_simulation.py` | 49 | 23 s |
| 1:45–2:30 | "Synthetic image, known ground truth. These two files were split in two pieces. We rebuild a split file only when its own bytes prove the join: every PNG chunk checksum and an exact decompressed length. Both match ground truth, and both are held below HIGH on purpose. The score is a sum of named evidence, not a probability." | `scripts/demo_fragmented.py`; Recovery score breakdown | 58 | 27 s |
| 2:30–3:15 | "Every operation appends to a hash chain. Each entry holds the hash of the one before. The report is signed with Ed25519, and five independent checks pass. Watch: we change one byte in a copy, and the chain breaks at that entry. The signature proves the report was not altered. It does not prove who signed it, and we say that." | Audit: Verify, Simulate tampering, report panel | 61 | 28 s |
| 3:15–4:00 | "Sanitization is a state machine. Dry run is on by default. To write, a human turns it off and types the device serial, and the button stays disabled until it matches. Right before writing, the tool re-reads the device to confirm it is still the same disk. For our physical benchmark, a verified backup is also required. We do not press erase today." | Sanitize: workflow strip, approval dialog | 63 | 29 s |
| 4:00–4:30 | "What is proven where. On a real USB stick: overwrite Clear, including catching a controller that fakes zero writes, and recovery passes. On synthetic images: the benchmark and calibration. Not yet validated on hardware: firmware Purge, HPA and DCO unlock, backup restore, and our registered physical benchmark." | Overview LIMITATIONS column; `benchmark.md` | 47 | 22 s |
| **Total** | | | **380** | **2:55** |

Spoken time is about 2:55, which leaves about 1:35 across seven beats for
screen changes, commands and one interruption. That fits under 4:30 without
rushing, **on paper**. It has not been timed aloud; see the rehearsal status
below.

## Rehearsal status

| Item | Status |
|---|---|
| Technical run of every beat | Done 2026-09-24 (`demo-evidence-index.md`). At `8e1777d`: `demo_simulation.py` exit 0 in 0.48 s, `demo_fragmented.py` exit 0 in 0.57 s, the absent-device refusal exit 2 |
| Spoken rehearsal against a stopwatch | **Not done.** It needs a human presenter. Log each run in the table below |
| Interruption rehearsal | **Not done.** A second person reads the seven interruptions above at random points |

| Run | Date | 0:30 | 1:00 | 1:45 | 2:30 | 3:15 | 4:00 | End | Interruptions | Under 4:30? |
|---:|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | | |
| 2 | | | | | | | | | | |
| 3 | | | | | | | | | | |
