# SIH 26149 final push (submission 2026-09-30)

Goal: win on the problem statement, the demo and the judges' questions, without
spending the honesty that is this project's differentiator. Nothing here runs a
physical destructive operation; SANCTUMREC is not touched.

## Problem statement coverage (2026-09-25 audit)

| Requirement | Where it stands | Action |
|---|---|---|
| Drive eraser: HDD/SSD/USB/cards, verification, audit log, tamper-resistant report, standards | Linux engine, capability-driven Clear/Purge, read-back verification, hash-chained ledger, Ed25519 reports, NIST 800-88r2 mapping | Certificate PDF is a key:value dump; make it a real certificate (C1). Destroy is named but not recorded (F3) |
| File/folder eraser: selective, metadata cleanse, residual traces, batch, verify, multi-FS/OS | Implemented; residual *reported*, but OS traces of an erased file (thumbnails, recent-files, Trash copies) are left behind | Trace sweep (F1) |
| Recovery: formatted/damaged media, signature + structure + intelligent carving, no-FS recovery, fragments, classification, confidence, reporting | Implemented and benchmarked vs PhotoRec/Foremost | Media map of an image - what each region holds, from byte statistics (F2) |
| Reporting and audit management | Signed reports, graded verifier, tamper demo, cases | Chain explorer, blockchain-style (U4) |
| UI dashboard, user-friendly GUI | Functional, text-dense, system fonts | Redesign (U1-U6) |
| Validation, manuals, technical docs, performance reports | Extensive | Refresh screenshots, README, performance summary (D1-D3) |

## UI/UX (U)

- [ ] U1 Design system v2: self-hosted Inter + JetBrains Mono, tokens with a brand
      accent that never collides with the destructive red, Lucide icons, AA+ contrast
- [ ] U2 Shell: grouped icon navigation, page headers, compact status bar
- [ ] U3 Overview dashboard: module cards, KPI tiles, chain widget, activity, standards
- [ ] U4 Audit: chain explorer (blocks and hash links), tamper demo on the chain
- [ ] U5 Screens: Devices, Sanitize, File eraser, Recovery, Cases, Platform
- [ ] U6 Visual QA at 1366x768 and 1920x1080; browser checks kept green

## Features (F) and certificate (C)

- [ ] C1 Certificate of Sanitization PDF: NIST 800-88r2 fields, verdict, QR with the
      report hash, chain head, signature block; fix empty level fields
- [ ] F1 Trace sweep for erased files: thumbnails, recently-used, Trash copies
      (Linux), Recent and Recycle Bin (Windows), Trash (macOS); dry run, ledger, report
- [ ] F2 Media map for recovery: region classes from byte statistics
- [ ] F3 Destroy record: a signed record of physical destruction, attested by a person

## Documentation and delivery (D)

- [ ] D1 README and user manual updated for the new UI and features
- [ ] D2 Screenshots and browser evidence regenerated
- [ ] D3 Packages rebuilt, identity-checked, isolated smoke
