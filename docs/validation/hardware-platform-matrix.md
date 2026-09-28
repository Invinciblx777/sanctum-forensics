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
| **NOT YET VALIDATED** | The software path exists; nobody has run it on hardware of that kind (supported, not physically validated). |
| **UNSUPPORTED** | Not implemented on that platform (unsupported / not implemented). Refused by the app with a reason. |

The per-capability evidence search behind this page, with every physical run
found and every capability that has none, is
[`evidence-reconciliation-2026-09-28/`](evidence-reconciliation-2026-09-28/README.md).

Most VALIDATED rows below are a run from 2026-09-05 or 2026-09-23 with the build of
that date, on Linux. **No physical validation was run for the `76dde42`
release** (2026-09-26). A later, separate run on 2026-09-27, first at
`2d00526` and then, after two packaging fixes that run itself found
(`ba66fbe`, `437081e`), at `437081e`, physically validated the Windows rows
below for the first time — see
[`windows-hardware-2026-09-27-fixes/`](windows-hardware-2026-09-27-fixes/README.md)
(the `2d00526` record is preserved at
[`windows-hardware-2026-09-27/`](windows-hardware-2026-09-27/README.md)) —
and is marked accordingly. The app shows firmware Purge as *Unverified*, and
lists HPA/DCO unlock as not run on hardware, until a hardware result is
recorded.

## Whole-drive sanitization

| Target | State | Evidence |
|---|---|---|
| Linux, USB flash (TransMemory 7.76 GB), Clear by overwrite | **VALIDATED** | `docs/validation/hardware.md`: three Phase A runs, the third (2026-09-05) clean |
| Linux, SATA/NVMe internal, Clear | NOT YET VALIDATED | refused on this host: internal disks hold the running system |
| Linux, firmware Purge (ATA SANITIZE, SECURITY ERASE, NVMe sanitize/format, Opal) | NOT YET VALIDATED | selected and dispatched in code; no drive has executed it here |
| Linux, HPA/DCO unlock on a drive that has one | NOT YET VALIDATED | probe exercised; no device with an HPA was available |
| Windows, any | **UNSUPPORTED / NOT IMPLEMENTED** | no engine in this build; refused with the reason |
| macOS, any | **UNSUPPORTED / NOT IMPLEMENTED** | no engine in this build; refused with the reason |
| Any platform, backup restoration | **UNSUPPORTED / NOT IMPLEMENTED** | no restore in the app; `scripts/media_benchmark.py` prints a manual `dd` command, never run |

## Device discovery and protection

| Target | State | Evidence |
|---|---|---|
| Linux, host disks + USB stick | **VALIDATED** | `scripts/platform_smoke.py` on the development host and in CI |
| Windows, runner's own disks | **CI-VALIDATED** | `platform-smoke-Windows.json`, `platform-ci` |
| Windows, physical machine with removable media | **VALIDATED** (2026-09-27) | installed package on a physical Windows 11 machine found 3 real devices including a USB stick, and correctly assessed the mounted one NOT AVAILABLE; [`windows-hardware-2026-09-27-fixes/`](windows-hardware-2026-09-27-fixes/README.md) |
| macOS, runner's own APFS disks | **CI-VALIDATED** | `platform-smoke-macOS.json`, `platform-ci` |
| macOS, physical Mac with removable media | NOT YET VALIDATED | needs a Mac and a disposable stick |

## File and folder erasure

| Target | State | Evidence |
|---|---|---|
| Linux, ext4/xfs/tmpfs | **VALIDATED** | suite plus packaged smoke on the development host |
| Windows, NTFS on the runner | **CI-VALIDATED** | `validation-Windows.json` |
| Windows, real junction / reparse point | **CI-VALIDATED** | `tests/platform/test_windows_filesystem.py` creates a real junction on the runner |
| Windows, real machine, NTFS via the installed package | **VALIDATED** (2026-09-27) | erase → read-back verify → certificate issue → certificate verify, real, in a scratch directory on the machine's own system disk; trace sweep enumeration confirmed real (searched this machine's actual Recycle Bin and Recent shortcuts, found nothing to remove); [`windows-hardware-2026-09-27-fixes/`](windows-hardware-2026-09-27-fixes/README.md) |
| Any platform, trace sweep removing a real desktop trace | NOT YET VALIDATED | only synthetic homes have had a trace removed; no Linux or macOS desktop run |
| macOS, APFS on the runner | **CI-VALIDATED** | `validation-macOS.json` |
| Any platform, SSD residual behaviour after erase | NOT YET VALIDATED | needs physical media and out-of-band reading |

## Packages

| Target | State | Evidence |
|---|---|---|
| Linux AppImage / `.deb` on the build host | **VALIDATED** | packaged smoke; `.deb` installed and removed in Debian 12, AppImage run in Debian 12 and Ubuntu 22.04 |
| Windows `SanctumSetup.exe`, silent install → run → uninstall | **CI-VALIDATED**; also **VALIDATED** silent-install on a physical machine, 2026-09-27 (uninstall not exercised there) | `package-smoke-Windows.json`; [`windows-hardware-2026-09-27-fixes/`](windows-hardware-2026-09-27-fixes/README.md) |
| macOS `Sanctum.dmg` mounted and run | **CI-VALIDATED** | `package-smoke-macOS.json` |
| Windows install on a physical machine by a human | **VALIDATED** (2026-09-27) | [`windows-hardware-2026-09-27-fixes/`](windows-hardware-2026-09-27-fixes/README.md) |
| macOS install on a physical machine by a human | NOT YET VALIDATED | |
| Code signing / notarization | NOT PERFORMED | no certificates; `docs/packaging.md` |

## Acquisition (Windows)

| Target | State | Evidence |
|---|---|---|
| Windows, raw physical-device acquisition (`\\.\PhysicalDriveN`, a raw volume) | **UNSUPPORTED / NOT IMPLEMENTED** — not merely untested; since 2026-09-28 refused with that reason rather than "not found" | `Path.exists()` and `open()` cannot address the Win32 device namespace; no code in `core/carve/acquire.py` / `core/carve/evidence.py` special-cases a Windows device path. Confirmed directly, 2026-09-27: [`windows-hardware-2026-09-27/`](windows-hardware-2026-09-27/README.md) §4 |
| Windows, M3 carving over a synthetic image, via `scripts/demo_fragmented.py` (dev venv, direct) | **SYNTHETIC, run on physical Windows hardware** (2026-09-27) | 6/6 ground-truth scenarios correct; [`windows-hardware-2026-09-27/`](windows-hardware-2026-09-27/README.md) §3 |
| Windows, M3 acquire + carve + media map, through the *installed package's own API* (`/jobs/acquire`, `/jobs/carve`), synthetic image | **SYNTHETIC, run through the installed package on physical Windows hardware** (2026-09-27) | Failed before `437081e` — the packaged app could not find its own signature table (`testkit/signatures.yaml` excluded from every prior build); fixed, then 5/6 candidates recovered (1 correctly deduplicated), media map present; [`windows-hardware-2026-09-27-fixes/`](windows-hardware-2026-09-27-fixes/README.md) §4–§5 |

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
