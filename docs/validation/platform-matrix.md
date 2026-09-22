# Cross-platform test matrix

What was run, where, and what was not. **NOT RUN is not PASS.** A row marked
NOT RUN has code and, usually, fixture tests on another host; it has not been
executed on the platform named.

Recorded 2026-09-22 from `platform-ci` on the
`release/cross-platform-validation` branch, three runners:

| Runner | OS | Architecture | Python |
|---|---|---|---|
| `ubuntu-latest` | Ubuntu 24.04.5 (kernel 6.17) | x86_64 | 3.11 |
| `windows-latest` | Windows 11, build 10.0.26100 | AMD64 | 3.11.9 |
| `macos-14` | macOS 14.8.9 | arm64 | 3.11.9 |

plus the development host (Fedora Linux 44, x86_64, unprivileged user).

**No physical device was written by any of it.** CI runners have virtual
disks and no removable media; the stick attached to the development host was
mounted and refused by every path.

## Automated (CI and real runners)

| Area | Linux | Windows | macOS | Where |
|---|---|---|---|---|
| Lint, four strict typecheck passes, UI build and unit tests | PASS | — | — | `gate` |
| Full Python suite | PASS (1644 passed, 34 skipped on the host; PASS on the runner) | PASS on the runner | PASS on the runner | `platform` job |
| Platform adapter against the runner's own disks | PASS | PASS - 2 disks, both protected: `IsBoot`, and a page file on `D:` | PASS - internal disk protected: the running macOS boots from an APFS container on it | `scripts/platform_smoke.py`, `platform-smoke-*.json` |
| Capability rows all carry a source; whole-drive UNSUPPORTED off Linux | PASS | PASS | PASS | same |
| File, folder, batch erase; metadata; cancellation | PASS | PASS (`file_erase` suite: 201 passed, 71 skipped) | PASS (`file_erase` suite: 169 passed, 103 skipped) | `validation-*.json` |
| NTFS specifics: real junction, alternate data streams, resident MFT data | n/a | PASS | n/a | `tests/platform/test_windows_filesystem.py` |
| APFS: erase runs, verification refused with a reason, residual recorded | n/a | n/a | PASS | `tests/platform/test_macos_filesystem.py` |
| Protected system locations, path traversal | PASS | PASS | PASS | per-platform lists |
| Session token, DNS-rebinding host check, stale and cross-origin sessions | PASS | PASS | PASS | `tests/platform/test_boundary_and_security.py` |
| Platform recorded in the signed report; typed signing passphrase | PASS | PASS | PASS | `tests/api/test_platform_in_report.py` |
| API suite | PASS | PASS (201 passed, 9 skipped) | PASS (203 passed, 7 skipped) | `validation-*.json` |
| Recovery suite | PASS (400 passed) | not run as a suite | not run as a suite | `validation-linux` |
| UI units | PASS (32) | — | — | `ui/tests` |

## Packages

| Check | Linux | Windows | macOS |
|---|---|---|---|
| Package built | PASS - AppImage + `.deb`, locally and on `ubuntu-22.04` | PASS - `SanctumSetup.exe` on `windows-latest` | PASS - `Sanctum.dmg` (44,013,635 bytes) on `macos-14` |
| Installed the way a user would | PASS - `.deb` installed and removed on Debian 12 | PASS - silent install to `%LOCALAPPDATA%\Programs\Sanctum`, then uninstalled | PASS - DMG mounted, `Sanctum.app` copied and run |
| Runs with no developer environment | PASS - Debian 12 and Ubuntu 22.04 containers with no Python | PASS - runner Python not on the app's path | PASS |
| Session refusal, non-loopback Host refusal | PASS | PASS | PASS |
| Discovery and assessment through the package | PASS | PASS | PASS |
| Folder erase confined to its scratch directory | PASS | PASS | PASS |
| Signed certificate issued and verified | PASS | PASS | PASS |
| Quit stops the process | PASS | PASS | PASS |
| Packaged checks | 23 of 23 | 24 of 24 | 24 of 24 |

Linux runs one check fewer: *whole-drive unsupported off Linux* is a Windows and macOS check, and there is nothing for it to assert on the platform where whole-drive sanitization is supported.

The Windows column above is from the run that followed the one where the
packaged smoke test itself failed on the quit: Windows resets the connection
(`WinError 10054`) instead of closing it, and the script treated a missing
reply as an error. The installer, the install, the run and the uninstall all
worked in that earlier run too.

## Hardware

Nothing here changed in this work. See
[`hardware-platform-matrix.md`](hardware-platform-matrix.md).

| Check | Linux | Windows | macOS |
|---|---|---|---|
| Whole-drive Clear on real media | VALIDATED earlier (`hardware.md`); not re-run | UNSUPPORTED | UNSUPPORTED |
| Firmware Purge on real media | NOT RUN | UNSUPPORTED | UNSUPPORTED |
| Discovery against a physical disk set | VALIDATED on the development host | NOT RUN | NOT RUN |
| File erase on physical NTFS / APFS media | n/a | NOT RUN | NOT RUN |
| Install by a human on a physical machine | VALIDATED | NOT RUN | NOT RUN |

## To close the remaining rows

1. A Windows 11 machine, a Mac, and a **disposable** USB stick each: install
   the package, open *Platform*, confirm the stick appears and the internal
   disk reads NOT AVAILABLE with its reason, erase a scratch folder on the
   stick, issue the certificate. Record the run here with the date, OS build
   and device model.
2. A Linux host with a spare drive that reports ATA SANITIZE or NVMe sanitize,
   for the firmware Purge path.
3. A drive with an HPA or DCO set, for the hidden-area path.
