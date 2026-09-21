# Platform support

One sanitization product, one capability model, three platform adapters.
This page is the capability matrix, and every cell in it is traceable to code
and to evidence. **Nothing here is inferred from the platform name.**

The same matrix is computed live by the app (the *Platform* screen, `GET
/platform`), from the adapter running on that machine, with the probe or code
path behind every row printed underneath it. This document is the static
summary of what those rows can say on each OS, and why.

## Legend

| Word | Meaning |
|---|---|
| **FULL** | Implemented on this platform, run on this platform, and exercised against real media or a real OS where that applies. Documented limits still apply. |
| **PARTIAL** | Implemented and run, with a material gap named in the cell. |
| **UNVERIFIED** | Implemented, tested on other hosts from fixtures or type-checked for this platform, but never executed on this platform in recorded validation. The app shows these as *Unverified*, not as supported. |
| **UNSUPPORTED** | No implementation on this platform. The app refuses with a reason and performs nothing. |

## Matrix

| Feature | Linux | Windows | macOS |
|---|---|---|---|
| Device discovery | **FULL** — `lsblk -J -O -b` with `/sys/block` fallback, by-id identity (`core/device/enumerate.py`) | **UNVERIFIED** — Storage module: `Get-Disk`, `Get-PhysicalDisk`, `Get-Partition`, `Get-Volume`, `Win32_PageFileUsage`, one encoded PowerShell script (`core/platform/windows.py`); parser tested from a captured inventory | **UNVERIFIED** — `diskutil list/apfs list/info -plist` via `plistlib` (`core/platform/macos.py`); parser tested from captured plists |
| System/boot/mounted refusal | **FULL** — root, `/boot`, swap, mounts (`core/device/guard.py`); refused on the real host disks in hardware runs | **UNVERIFIED** — `IsBoot`, `IsSystem`, `%SystemDrive%`, page file, `hiberfil.sys`, drive letters and folder mounts | **UNVERIFIED** — boot APFS container traced to its physical store, System/Data/VM/Preboot/Recovery roles, mounts through containers |
| Normalized device + assessment | **FULL** | **UNVERIFIED** | **UNVERIFIED** |
| File erase | **FULL** — `core/erase/files.py`, `PosixBackend` (FIEMAP extents, xattrs, chattr flags, btrfs/zfs snapshot listing) | **UNVERIFIED** — same engine, `WindowsBackend` (retrieval pointers, resident-MFT detection, ADS, read-only attribute, VSS listing when elevated) | **UNVERIFIED** — same engine, `PosixBackend` darwin paths (`statfs`, `F_LOG2PHYS_EXT`, `tmutil` snapshots). On APFS the overwrite lands in new blocks: removal plus residual report, never verified destruction |
| Folder / recursive erase | **FULL** | **UNVERIFIED** — junctions and all reparse points are never descended (fixed in this change; see *Security*) | **UNVERIFIED** |
| Batch erase, progress, cancellation | **FULL** | **UNVERIFIED** | **UNVERIFIED** |
| Document metadata cleanse | **FULL** — JPEG, PNG, PDF, OOXML, OLE, audio, SVG, HTML (`core/erase/metadata.py`) | **UNVERIFIED** (pure Python) | **UNVERIFIED** (pure Python) |
| Filesystem metadata (names, size) | **PARTIAL** — same-length rename chain, stepped truncation; journal and index copies are reported, not removed | **UNVERIFIED** — as Linux, plus no unprivileged directory flush | **UNVERIFIED** |
| Free-space wipe | **PARTIAL** — FAT32, exFAT, ext4 only (`FREE_SPACE_PLATFORMS`, `SUPPORTED`) | **UNSUPPORTED** | **UNSUPPORTED** |
| Whole-drive Clear (overwrite) | **FULL** — `O_DIRECT` overwrite, HPA/DCO unlock, validated on real USB flash (`docs/validation/hardware.md`) | **UNSUPPORTED** — no validated raw-disk engine; refused with reason | **UNSUPPORTED** — refused with reason |
| Hardware Purge (ATA SANITIZE, SECURITY ERASE, NVMe sanitize/format, Opal) | **UNVERIFIED** — selected from probed capability and dispatched, never executed on hardware in recorded validation | **UNSUPPORTED** | **UNSUPPORTED** — internal Apple storage is purged by macOS *Erase All Content and Settings*, which the app names and does not perform |
| Whole-drive verification | **FULL** for overwrite (full read to 64 GiB, seeded sampling above); **UNVERIFIED** for drive attestation | **UNSUPPORTED** | **UNSUPPORTED** |
| File-erase verification | **PARTIAL** — physical read-back of pre-captured extents; needs raw read access; not possible on tmpfs or copy-on-write | **UNVERIFIED** — raw volume read needs elevation; unelevated it is reported *not verified* | **UNVERIFIED** — not possible on APFS |
| Resume | **PARTIAL** — overwrite from last ledgered checkpoint; firmware methods restart | **UNSUPPORTED** | **UNSUPPORTED** |
| Signed certificate, hash-chained ledger | **FULL** | **UNVERIFIED** (pure Python; `msvcrt` ledger lock) | **UNVERIFIED** (pure Python) |
| Privileged helper | **FULL** — root daemon, Unix socket 0600, `SO_PEERCRED`, static allowlist, path confinement | not needed: no Windows operation needs elevation, and none is requested | not needed: as Windows |
| Desktop package | **FULL** — AppImage and `.deb`, built and smoke-tested on Fedora 44 in this change | **UNVERIFIED** — `SanctumSetup.exe` scripted (PyInstaller + Inno Setup) and wired into CI; not built on a Windows machine in this change | **UNVERIFIED** — `Sanctum.dmg` scripted (PyInstaller + `hdiutil`); not built on a Mac in this change; unsigned, not notarized |

When the Windows and macOS CI jobs (`.github/workflows/platform-ci.yml`) pass,
`scripts/record_platform_validation.py` writes their results into
`core/platform/validation_record.json`, and the app's own matrix lifts the
file-erase rows on that platform from *Unverified* to *Supported with limits*.
This document should then be updated from the recorded run, not before.

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
