# Platform support

One sanitization product, one capability model, three platform adapters.
This page is the capability matrix, and every cell in it is traceable to code
and to evidence. **Nothing here is inferred from the platform name.**

The same matrix is computed live by the app (the *Platform* screen, `GET
/platform`), from the adapter running on that machine, with the probe or code
path behind every row printed underneath it. This document is the static
summary of what those rows can say on each OS, and why.

## Legend

Four states, kept apart on purpose. Collapsing them is how a matrix starts
lying.

| Word | Meaning |
|---|---|
| **VALIDATED** | Executed on that operating system and recorded: the test suites and the adapter smoke run on a real runner of that OS (`platform-ci`), or on real media where the cell says so. |
| **PARTIAL** | Validated, with a material gap named in the cell. |
| **UNVERIFIED** | Code exists and is type-checked and fixture-tested, but that environment has not executed it. The app shows *Unverified*, never *Supported*. |
| **UNSUPPORTED** | Deliberately unavailable there. The app refuses with a reason and performs nothing. |
| **HARDWARE-UNVERIFIED** | The software path is validated, but no physical device of that kind has ever been through it. See [`validation/hardware-platform-matrix.md`](validation/hardware-platform-matrix.md). |

CI runs on virtual machines with virtual disks. **CI-validated is not
hardware-validated**, and no row here claims a physical device unless
`docs/validation/hardware.md` records one.

## Matrix

Evidence: `platform-ci` run on `release/cross-platform-validation`, three
runners - Ubuntu 24.04.5 (x86_64), Windows 11 build 10.0.26100 (AMD64),
macOS 14.8.9 (arm64). Suite results are in
`core/platform/validation_record.json`; the adapter and package evidence are
the `platform-smoke-*.json` and `package-smoke-*.json` artifacts of that run.

| Feature | Linux | Windows | macOS |
|---|---|---|---|
| Device discovery | **VALIDATED** - `lsblk -J -O -b` with a `/sys/block` fallback and by-id identity; run on the host and on the CI runner | **VALIDATED** - Storage module (`Get-Disk`, `Get-PhysicalDisk`, `Get-Partition`, `Get-Volume`, `Win32_PageFileUsage`) through one encoded PowerShell script; on the runner it found both disks and normalised them | **VALIDATED** - `diskutil list/apfs list/info -plist`; on the runner it found the internal disk and traced the boot APFS container to it |
| System/boot/mounted refusal | **VALIDATED** - root, `/boot`, swap and mounts; the runner's own disk was refused | **VALIDATED** - `IsBoot`, `IsSystem`, `%SystemDrive%`, page file, `hiberfil.sys`; the runner's boot disk **and** its page-file disk were both refused, each with its reason | **VALIDATED** - "the running macOS boots from an APFS container on this disk"; System/Data/VM/Preboot/Recovery roles also protect |
| Normalized device + assessment | **VALIDATED** | **VALIDATED** - every discovered device carried an assessment; protected ones read NOT AVAILABLE | **VALIDATED** |
| File erase | **VALIDATED** - `PosixBackend`: FIEMAP extents, xattrs, chattr flags, snapshot listing | **VALIDATED** - `WindowsBackend` on the runner's NTFS: retrieval pointers, resident-MFT detection, alternate data streams, read-only attribute | **VALIDATED** - `PosixBackend` darwin paths on APFS; the erase runs and the verification is refused rather than claimed |
| Folder / recursive erase | **VALIDATED** | **VALIDATED** - a real NTFS junction is created on the runner and proved unable to redirect the erase out of the named root | **VALIDATED** |
| Batch erase, progress, cancellation | **VALIDATED** | **VALIDATED** | **VALIDATED** |
| Document metadata cleanse | **VALIDATED** | **VALIDATED** (pure Python, run in the Windows suite) | **VALIDATED** |
| Filesystem metadata (names, size) | **PARTIAL** - rename chain and stepped truncation; journal and index copies are reported, not removed | **PARTIAL** - as Linux, and no unprivileged directory flush exists on Windows | **PARTIAL** |
| Free-space wipe | **PARTIAL** - FAT32, exFAT, ext4 only | **UNSUPPORTED** | **UNSUPPORTED** |
| Whole-drive Clear (overwrite) | **VALIDATED** - `O_DIRECT` overwrite, physically run on one USB flash stick on 2026-09-05 with an earlier build (`validation/hardware.md`); not re-run for this release. HPA/DCO unlock is part of the path but **HARDWARE-UNVERIFIED**: no drive with a hidden area has been through it, and behind that stick's USB bridge the probe was skipped | **UNSUPPORTED** - refused with the reason; no engine exists here | **UNSUPPORTED** - refused with the reason |
| Hardware Purge (ATA SANITIZE, SECURITY ERASE, NVMe sanitize/format, Opal) | **HARDWARE-UNVERIFIED** - selected from probed capability and dispatched; no drive has executed it in a recorded run. The Platform row and each device's Purge option read *Unverified*, and the Devices badge reads PURGE · UNVERIFIED, until a hardware PASS is recorded; the option is still offered, under that word | **UNSUPPORTED** | **UNSUPPORTED** - macOS purges internal storage through *Erase All Content and Settings*, which the app names and does not perform |
| Whole-drive verification | **VALIDATED** for overwrite (full read to 64 GiB, seeded sampling above); **HARDWARE-UNVERIFIED** for drive attestation | **UNSUPPORTED** | **UNSUPPORTED** |
| File-erase verification | **PARTIAL** - physical read-back of pre-captured extents; needs raw read access, impossible on tmpfs | **PARTIAL** - extents come from `FSCTL_GET_RETRIEVAL_POINTERS` (whole runs, fixed in this work); the read-back itself needs elevation, and unelevated it is reported *not verified* | **NOT VERIFIABLE on APFS** - copy-on-write; reported with its reason, never as a pass |
| Resume | **PARTIAL** - overwrite from the last ledgered checkpoint; firmware methods restart | **UNSUPPORTED** | **UNSUPPORTED** |
| Signed certificate, hash-chained ledger | **VALIDATED** | **VALIDATED** - the packaged app issued and verified one on the runner | **VALIDATED** - same |
| Privileged helper | **VALIDATED** - root daemon, 0600 Unix socket, `SO_PEERCRED`, static allowlist, path confinement | not needed, and none ships: no implemented Windows operation requires elevation | not needed, as Windows |
| Desktop package | **VALIDATED** - AppImage and `.deb`; installed and run on Debian 12 and Ubuntu 22.04 with 23 of 23 packaged checks in CI (`platform-ci` run 35706589476, commit `ba13a9a`, 2026-09-22, an earlier build). This release's build: isolated smoke 22 PASS, 2 NOT RUN (the two need a real device), [`validation/package-2026-09-25/`](validation/package-2026-09-25/README.md) | **VALIDATED in CI** - `SanctumSetup.exe` built, installed silently, driven and uninstalled on the runner. Unsigned. | **VALIDATED in CI** - `Sanctum.dmg` built and mounted, `Sanctum.app` driven through erase and certificate, 24 of 24 checks. Unsigned, not notarized. |

**Nothing above was performed on physical media on Windows or macOS.** CI
runners have virtual disks and no removable device; see
[`validation/hardware-platform-matrix.md`](validation/hardware-platform-matrix.md).

## Filesystems

Detecting a filesystem is not supporting it. The app's *Filesystems* table
keeps six rows per filesystem — detect, recover from image, erase files,
filesystem metadata, free-space wipe, whole drive — per platform, computed by
`core/platform/filesystems.py` from the code that implements each operation.
The summary:

| Filesystem | Linux | Windows | macOS |
|---|---|---|---|
| NTFS | erase files, undelete from image | erase files (UNVERIFIED) | detect only when mounted read-only; erase UNSUPPORTED |
| FAT32 / exFAT | erase files, free-space wipe, undelete | erase files (UNVERIFIED) | erase files (UNVERIFIED) |
| ext4 | erase files, free-space wipe, undelete (recovers little by design) | UNSUPPORTED (not mountable) | UNSUPPORTED |
| XFS | erase files | UNSUPPORTED | UNSUPPORTED |
| Btrfs, F2FS | erase *runs*, NOT VERIFIABLE (copy-on-write) | UNSUPPORTED | UNSUPPORTED |
| APFS | UNSUPPORTED (not mountable read-write) | UNSUPPORTED | erase *runs*, NOT VERIFIABLE (copy-on-write) |
| HFS+ | erase files | UNSUPPORTED | erase files (UNVERIFIED) |
| ReFS | UNSUPPORTED | erase *runs*, NOT VERIFIABLE (copy-on-write) | UNSUPPORTED |

Whole-drive sanitization is filesystem-independent and follows the platform
row above.

## SSD and flash, on every platform

The same sentence is shown on Linux, Windows and macOS
(`core/platform/base.py: FLASH_LIMITATION`): an overwrite cannot address
blocks the flash controller has remapped or held in over-provisioned space.
The application therefore probes for a purge-capable mechanism, selects the
strongest supported one, refuses rather than silently downgrading, and reports
the limitation. A USB, NVMe, SD or eMMC device is treated as flash unless the
OS positively reports a spinning disk, so the limitation is never left out.

## What is Linux-only, and why

- **Whole-drive sanitization.** The engine (`core/erase/drive.py`) needs
  `O_DIRECT` alignment, `BLKGETSIZE64`, sysfs queue attributes and ATA/NVMe
  pass-through through `hdparm`, `nvme-cli` and `sedutil-cli`. It refuses to
  import elsewhere. Windows could reach the same commands through
  `\\.\PhysicalDriveN` and `IOCTL_STORAGE_PROTOCOL_COMMAND`, and macOS
  through `/dev/rdiskN`; neither has been written and validated, and an
  unvalidated raw-disk writer is exactly where being wrong destroys the wrong
  disk. The recommended path on those machines is the Linux AppImage on the
  same hardware, booted from a Linux live USB if the target is internal.
- **HPA/DCO detection and unlock, checkpoint resume** — part of that engine.
- **Free-space wipe** — its fill behaviour was measured on FAT32, exFAT and
  ext4 on Linux only.
- **The privileged helper** — it authenticates peers with `SO_PEERCRED`.

## Privilege

| | Linux | Windows | macOS |
|---|---|---|---|
| UI and API | unprivileged, loopback only | unprivileged, loopback only | unprivileged, loopback only |
| Privileged operations | separate root helper over an authenticated socket | none exist | none exist |
| What elevation would add | whole-drive work, raw read-back | raw volume read-back for file verification, VSS listing | raw read-back (not possible on APFS anyway) |

The app never asks to run as Administrator or root. The Windows installer is
per-user by default and has no elevation manifest.
