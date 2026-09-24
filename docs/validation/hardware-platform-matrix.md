# Hardware validation matrix

CI success is not hardware validation. Everything in `platform-ci` runs on a
virtual machine with virtual disks; it proves the code runs on that operating
system, not that a physical device was sanitized. This page keeps the two
apart.

Four states, and they are not interchangeable:

| State | Meaning |
|---|---|
| **VALIDATED** | Performed on real hardware, on real media, with the run recorded in `docs/validation/`. |
| **CI-VALIDATED** | Executed on a real runner of that OS, against that runner's own disks and filesystems. No physical media. |
| **NOT YET VALIDATED** | The software path exists; nobody has run it on hardware of that kind. |
| **UNSUPPORTED** | Not implemented on that platform. Refused by the app with a reason. |

## Whole-drive sanitization

| Target | State | Evidence |
|---|---|---|
| Linux, USB flash (TransMemory 7.76 GB), Clear by overwrite | **VALIDATED** | `docs/validation/hardware.md`: three Phase A runs, the third (2026-09-05) clean |
| Linux, SATA/NVMe internal, Clear | NOT YET VALIDATED | refused on this host: internal disks hold the running system |
| Linux, firmware Purge (ATA SANITIZE, SECURITY ERASE, NVMe sanitize/format, Opal) | NOT YET VALIDATED | selected and dispatched in code; no drive has executed it here |
| Linux, HPA/DCO unlock on a drive that has one | NOT YET VALIDATED | probe exercised; no device with an HPA was available |
| Windows, any | **UNSUPPORTED** | no engine in this build; refused with the reason |
| macOS, any | **UNSUPPORTED** | no engine in this build; refused with the reason |

## Device discovery and protection

| Target | State | Evidence |
|---|---|---|
| Linux, host disks + USB stick | **VALIDATED** | `scripts/platform_smoke.py` on the development host and in CI |
| Windows, runner's own disks | **CI-VALIDATED** | `platform-smoke-Windows.json`, `platform-ci` |
| Windows, physical machine with removable media | NOT YET VALIDATED | needs a Windows 11 machine and a disposable stick |
| macOS, runner's own APFS disks | **CI-VALIDATED** | `platform-smoke-macOS.json`, `platform-ci` |
| macOS, physical Mac with removable media | NOT YET VALIDATED | needs a Mac and a disposable stick |

## File and folder erasure

| Target | State | Evidence |
|---|---|---|
| Linux, ext4/xfs/tmpfs | **VALIDATED** | suite plus packaged smoke on the development host |
| Windows, NTFS on the runner | **CI-VALIDATED** | `validation-Windows.json` |
| Windows, real junction / reparse point | **CI-VALIDATED** | `tests/platform/test_windows_filesystem.py` creates a real junction on the runner |
| macOS, APFS on the runner | **CI-VALIDATED** | `validation-macOS.json` |
| Any platform, SSD residual behaviour after erase | NOT YET VALIDATED | needs physical media and out-of-band reading |

## Packages

| Target | State | Evidence |
|---|---|---|
| Linux AppImage / `.deb` on the build host | **VALIDATED** | packaged smoke; `.deb` installed and removed in Debian 12, AppImage run in Debian 12 and Ubuntu 22.04 |
| Windows `SanctumSetup.exe`, silent install → run → uninstall | **CI-VALIDATED** | `package-smoke-Windows.json` |
| macOS `Sanctum.dmg` mounted and run | **CI-VALIDATED** | `package-smoke-macOS.json` |
| Windows/macOS install on a physical machine by a human | NOT YET VALIDATED | |
| Code signing / notarization | NOT PERFORMED | no certificates; `docs/packaging.md` |

## What would close the remaining rows

1. A Windows 11 machine and a Mac, each with a **disposable** USB stick: install
   the package, open *Platform*, confirm the stick and the refusal on the
   internal disk, erase a scratch folder on the stick, issue the certificate.
2. A Linux host with a spare SATA or NVMe drive that supports ATA SANITIZE or
   NVMe sanitize, for the firmware Purge path.
3. A drive with an HPA or DCO set, for the hidden-area path.

Record each run here with the date, the OS build, the device model and the
resulting certificate, as `docs/validation/hardware.md` does.

## Recorded CI evidence, 2026-09-22

`platform-ci` run 35680288845 on `release/cross-platform-validation`, all
jobs green. What the runners actually reported:

| Runner | Disks found | Protected, and why |
|---|---|---|
| Ubuntu 24.04.5, x86_64 | 1 (`/dev/sda`, 150 GB) | 1 — holds the running root filesystem |
| Windows 11 10.0.26100, AMD64 | 2 (`PhysicalDrive0`, `PhysicalDrive1`, 150 GB each) | 2 — `IsBoot` on the first, an active page file on `D:` for the second |
| macOS 14.8.9, arm64 | 1 (`disk0`, 325 GB, SSD) | 1 — the running macOS boots from an APFS container on it |

Every protected device was assessed NOT AVAILABLE, every capability row
carried a source, and whole-drive rows were UNSUPPORTED on Windows and macOS.

This is still not hardware validation: those are virtual disks on hosted
runners, and no removable media was attached to any of them.
