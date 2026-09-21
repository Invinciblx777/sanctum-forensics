# Cross-platform test matrix

What was run, where, and what was not. **NOT RUN is not PASS.** A row marked
NOT RUN has code and, usually, fixture tests on another host; it has not been
executed on the platform named.

Recorded 2026-09-21 on the development host (Fedora Linux 44, x86_64, Python
3.11, unprivileged user). No Windows or macOS machine and no designated
destructive test media were used in this change; the removable stick attached
to the host during this work was mounted and was refused by every path, and
nothing was written to any device.

## Automated (CI and fixtures)

| Area | Linux | Windows | macOS | Where |
|---|---|---|---|---|
| Platform detection, host facts, privilege | PASS | PASS (fixtures, on Linux) · NOT RUN on Windows | PASS (fixtures, on Linux) · NOT RUN on macOS | `tests/platform/test_paths_and_host.py` |
| Device normalization | PASS (`lsblk` path unchanged, `tests/device`) | PASS from a captured `Get-Disk` inventory · NOT RUN on Windows | PASS from captured `diskutil` plists · NOT RUN on macOS | `tests/platform/test_windows_adapter.py`, `test_macos_adapter.py` |
| System/boot/page-file/APFS-role protection | PASS | PASS (fixtures) | PASS (fixtures) | same |
| Capability matrix: sources, UNVERIFIED without a record | PASS | PASS (fixtures) | PASS (fixtures) | `tests/platform/test_assessment_and_matrix.py` |
| Refused whole-drive on Windows/macOS never reaches an engine | n/a | PASS (adapter swapped in on Linux) | PASS | `tests/platform/test_boundary_and_security.py` |
| Protected paths (case-insensitive, real `%SystemRoot%`) | PASS | PASS (pure-path tests) | PASS (pure-path tests) | `tests/platform/test_paths_and_host.py` |
| File, folder, batch erase; metadata; cancellation | PASS (`tests/erase/files`) | NOT RUN — CI job `platform-ci / tests (windows-latest)` configured | NOT RUN — CI job configured | `.github/workflows/platform-ci.yml` |
| NTFS-only tests (ADS, resident MFT data, VSS) | skipped with reason | NOT RUN | n/a | `tests/erase/files` (`ntfs_only`) |
| Junction / reparse-point walk refusal | PASS (simulated attribute) | NOT RUN on a real junction | n/a | `test_a_folder_erase_does_not_descend_through_a_junction` |
| Session token, DNS-rebinding host check | PASS | NOT RUN | NOT RUN | `tests/platform/test_boundary_and_security.py` |
| Platform in the signed report | PASS | NOT RUN | NOT RUN | `tests/api/test_platform_in_report.py` |
| Typed signing-key passphrase | PASS | NOT RUN | NOT RUN | same |
| UI units (status words, flow, device kind) | PASS (Node) | — | — | `ui/tests/platform.test.ts` |
| Type check as win32 / darwin | PASS (`mypy --platform`) | — | — | Makefile `typecheck` |

## Packages

| Check | Linux | Windows | macOS |
|---|---|---|---|
| Package built | PASS — AppImage + `.deb` on Fedora 44; portable build in `python:3.11-bullseye` | NOT RUN | NOT RUN |
| Launches without a developer environment | PASS — frozen AppImage, fresh state dir | NOT RUN | NOT RUN |
| Session protection in the frozen app | PASS — 401 without cookie, 303 on the session link | NOT RUN | NOT RUN |
| Discovery, platform matrix in the frozen app | PASS | NOT RUN | NOT RUN |
| File erase + certificate + report verification in the frozen app | PASS — scratch files on tmpfs; verification honestly *not possible* (tmpfs has no extents) | NOT RUN | NOT RUN |
| Quit from the UI stops the process | PASS | NOT RUN | NOT RUN |
| `.deb` installs and removes (`dpkg -i`, `dpkg -r`) | PASS — Debian 12 container | n/a | n/a |
| Runs on an older distribution | FAIL for the host build (needs glibc 2.38) → fixed by the portable build; see `docs/packaging.md` | n/a | n/a |

## Hardware

| Check | Linux | Windows | macOS |
|---|---|---|---|
| Whole-drive Clear on real media | PASS in earlier recorded runs (`hardware.md`); **not re-run** in this change | UNSUPPORTED (refused) | UNSUPPORTED (refused) |
| Firmware Purge on real media | NOT RUN | UNSUPPORTED | UNSUPPORTED |
| Windows discovery on a real disk set | n/a | NOT RUN | n/a |
| macOS discovery on a real disk set | n/a | n/a | NOT RUN |
| File erase on real NTFS / APFS | n/a | NOT RUN | NOT RUN |

## To close the NOT RUN rows

1. Push the branch; `platform-ci` runs the file-erase and adapter suites on
   `windows-latest` (NTFS) and `macos-14` (APFS) and uploads each
   `validation-<OS>.json`.
2. Merge them: `python scripts/record_platform_validation.py --merge
   validation-*.json`, commit `core/platform/validation_record.json`.
3. On a Windows 11 machine and a Mac with a **designated disposable** USB
   stick attached: install the package, open *Platform*, confirm the stick is
   listed with its size and bus, confirm the internal disk is *NOT AVAILABLE*
   with the system reason, erase a scratch folder on the stick, and issue the
   certificate. Record the result here with the date, OS build and stick
   model. Never point any of this at a disk that holds data.
