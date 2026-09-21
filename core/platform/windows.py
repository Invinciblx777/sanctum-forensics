"""Windows adapter: Storage-module discovery, safety facts, honest refusal.

Discovery
---------
One PowerShell invocation of the in-box Storage module (``Get-Disk``,
``Get-PhysicalDisk``, ``Get-Partition``, ``Get-Volume``) plus
``Win32_PageFileUsage``, emitted as a single JSON document. The script is a
constant: **no value from a request, a path or the environment is ever
interpolated into it**, and it runs through an argv list with ``shell=False``
from the absolute path of ``powershell.exe`` in the system directory, so a
``powershell.exe`` planted earlier on ``PATH`` is never the one executed.

All of these cmdlets run unelevated. Discovery therefore works for a standard
user, which is the point: the UI stays unprivileged.

Parsing is a pure function of that JSON (:func:`parse_inventory`), tested on
every host from captured fixtures.

Safety facts
------------
A disk is a **system device** when ``Get-Disk`` says ``IsBoot`` or
``IsSystem``, when it holds the ``%SystemDrive%`` volume, a page file, or the
hibernation file. It is **mounted** when any partition has a drive letter or a
folder mount path. Both refuse a whole-drive operation, through the same
:meth:`BaseAdapter.assess_device` every platform uses.

Whole-drive sanitization
------------------------
**Not implemented in this build, and reported as such.** Windows can reach a
disk through ``\\\\.\\PhysicalDriveN`` and can pass ATA and NVMe sanitize
commands with ``IOCTL_STORAGE_PROTOCOL_COMMAND`` /
``IOCTL_ATA_PASS_THROUGH``. None of those paths has been written and validated
here, and an unvalidated raw-disk writer is exactly the component where being
wrong destroys the wrong disk. So every device gets UNSUPPORTED with that
reason, and the recommended action is the validated Linux engine.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import structlog

from core.errors import PlatformUnsupported
from core.platform.base import FLASH_LIMITATION, BaseAdapter
from core.platform.model import (
    Interface,
    MediaType,
    NormalizedDevice,
    PartitionInfo,
)

__all__ = [
    "WindowsAdapter",
    "INVENTORY_SCRIPT",
    "parse_inventory",
    "powershell_path",
    "encoded_script",
    "BUS_TYPES",
]

logger = structlog.get_logger(__name__)

#: The whole discovery script. A constant: nothing is ever formatted into it.
#: Enum-typed properties are cast to strings so the JSON carries names where
#: PowerShell has them; the parser also accepts the numeric codes.
INVENTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$disks = @(Get-Disk | ForEach-Object { [pscustomobject]@{
  Number = $_.Number; FriendlyName = $_.FriendlyName;
  SerialNumber = $_.SerialNumber; Size = $_.Size; BusType = [string]$_.BusType;
  IsBoot = $_.IsBoot; IsSystem = $_.IsSystem; IsOffline = $_.IsOffline;
  IsReadOnly = $_.IsReadOnly; PartitionStyle = [string]$_.PartitionStyle;
  UniqueId = $_.UniqueId; Manufacturer = $_.Manufacturer; Model = $_.Model;
  Location = $_.Location } })
$physical = @(Get-PhysicalDisk | ForEach-Object { [pscustomobject]@{
  DeviceId = [string]$_.DeviceId; MediaType = [string]$_.MediaType;
  BusType = [string]$_.BusType; SpindleSpeed = $_.SpindleSpeed;
  SerialNumber = $_.SerialNumber } })
$partitions = @(Get-Partition | ForEach-Object { [pscustomobject]@{
  DiskNumber = $_.DiskNumber; PartitionNumber = $_.PartitionNumber;
  DriveLetter = [string]$_.DriveLetter; Size = $_.Size; Type = [string]$_.Type;
  IsBoot = $_.IsBoot; IsSystem = $_.IsSystem; AccessPaths = @($_.AccessPaths) } })
$volumes = @(Get-Volume | ForEach-Object { [pscustomobject]@{
  DriveLetter = [string]$_.DriveLetter; FileSystem = $_.FileSystem;
  FileSystemLabel = $_.FileSystemLabel; DriveType = [string]$_.DriveType;
  Path = $_.Path } })
$pagefiles = @(Get-CimInstance -ClassName Win32_PageFileUsage |
  ForEach-Object { [string]$_.Name })
$sysdrive = $env:SystemDrive
[pscustomobject]@{
  disks = $disks; physical = $physical; partitions = $partitions;
  volumes = $volumes; pagefiles = $pagefiles; system_drive = $sysdrive;
  hiberfil = (Test-Path -LiteralPath ($sysdrive + '\hiberfil.sys'))
} | ConvertTo-Json -Depth 6 -Compress
"""

#: ``MSFT_Disk.BusType`` / ``STORAGE_BUS_TYPE``, by numeric code.
BUS_TYPES: dict[int, str] = {
    0: "Unknown",
    1: "SCSI",
    2: "ATAPI",
    3: "ATA",
    4: "1394",
    5: "SSA",
    6: "Fibre Channel",
    7: "USB",
    8: "RAID",
    9: "iSCSI",
    10: "SAS",
    11: "SATA",
    12: "SD",
    13: "MMC",
    14: "Virtual",
    15: "File Backed Virtual",
    16: "Storage Spaces",
    17: "NVMe",
    18: "SCM",
    19: "UFS",
}

_BUS_TO_INTERFACE: dict[str, Interface] = {
    "usb": "usb",
    "nvme": "nvme",
    "sata": "sata",
    "ata": "sata",
    "atapi": "sata",
    "sas": "sas",
    "scsi": "scsi",
    "raid": "scsi",
    "iscsi": "scsi",
    "fibre channel": "scsi",
    "sd": "mmc",
    "mmc": "mmc",
    "ufs": "mmc",
    "virtual": "virtual",
    "file backed virtual": "virtual",
    "storage spaces": "virtual",
    "spaces": "virtual",
}

#: ``MSFT_PhysicalDisk.MediaType``.
_MEDIA_CODES = {0: "Unspecified", 3: "HDD", 4: "SSD", 5: "SCM"}


def _as_list(value: Any) -> list[Any]:
    """ConvertTo-Json collapses a one-element array to a scalar."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _enum_name(value: Any, table: dict[int, str]) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return table.get(value, str(value))
    text = str(value).strip()
    if text.isdigit():
        return table.get(int(text), text)
    return text


def _letter(value: Any) -> str:
    text = str(value or "").strip().strip("\x00").strip(":").strip()
    return text[:1].upper() if text and text[:1].isalpha() else ""


def _media(bus: str, media_name: str) -> tuple[MediaType, str]:
    upper = media_name.upper()
    if upper == "HDD":
        return "hdd", "Get-PhysicalDisk reports MediaType HDD."
    if upper in {"SSD", "SCM"}:
        return "ssd", f"Get-PhysicalDisk reports MediaType {upper}."
    lowered = bus.lower()
    if lowered == "nvme":
        return "ssd", "The disk is on the NVMe bus, which carries only flash."
    if lowered in {"sd", "mmc", "ufs"}:
        return "flash", f"The disk is on the {bus} bus, which carries only flash."
    if lowered == "usb":
        return "flash", (
            "The disk is USB-attached and Windows reports no media type. It is "
            "treated as flash, so the flash limitation is never left out."
        )
    return "unknown", (
        f"Get-PhysicalDisk reports MediaType {media_name or 'Unspecified'} on "
        f"the {bus or 'unknown'} bus, so the medium was not determined."
    )


def parse_inventory(payload: dict[str, Any]) -> list[NormalizedDevice]:
    """Turn the inventory JSON into normalized devices. Pure; no I/O."""
    system_drive = _letter(payload.get("system_drive") or "C")
    page_letters = {
        _letter(str(name).split(":", 1)[0])
        for name in _as_list(payload.get("pagefiles"))
        if name
    }
    hiberfil = bool(payload.get("hiberfil"))

    physical = {
        str(item.get("DeviceId")): item
        for item in _as_list(payload.get("physical"))
        if isinstance(item, dict)
    }
    volumes = {
        _letter(item.get("DriveLetter")): item
        for item in _as_list(payload.get("volumes"))
        if isinstance(item, dict) and _letter(item.get("DriveLetter"))
    }
    by_disk: dict[int, list[dict[str, Any]]] = {}
    for part in _as_list(payload.get("partitions")):
        if isinstance(part, dict) and part.get("DiskNumber") is not None:
            by_disk.setdefault(int(part["DiskNumber"]), []).append(part)

    devices: list[NormalizedDevice] = []
    for disk in _as_list(payload.get("disks")):
        if not isinstance(disk, dict) or disk.get("Number") is None:
            continue
        number = int(disk["Number"])
        bus = _enum_name(disk.get("BusType"), BUS_TYPES)
        interface = _BUS_TO_INTERFACE.get(bus.lower(), "unknown")
        phys = physical.get(str(number), {})
        media_name = _enum_name(phys.get("MediaType"), _MEDIA_CODES)
        media_type, media_basis = _media(bus, media_name)

        partitions: list[PartitionInfo] = []
        mount_points: list[str] = []
        filesystems: set[str] = set()
        letters: set[str] = set()
        reasons: list[str] = []
        for part in sorted(
            by_disk.get(number, []), key=lambda p: int(p.get("PartitionNumber") or 0)
        ):
            letter = _letter(part.get("DriveLetter"))
            paths = [
                str(item)
                for item in _as_list(part.get("AccessPaths"))
                if item and not str(item).startswith("\\\\?\\")
            ]
            if letter:
                letters.add(letter)
                paths = sorted({f"{letter}:\\", *paths})
            volume = volumes.get(letter, {}) if letter else {}
            fs = str(volume.get("FileSystem") or "")
            if fs:
                filesystems.add(fs)
            mount_points.extend(paths)
            partitions.append(
                PartitionInfo(
                    id=f"Disk {number} Partition {part.get('PartitionNumber')}",
                    size_bytes=int(part.get("Size") or 0),
                    filesystem=fs,
                    label=str(volume.get("FileSystemLabel") or ""),
                    mount_points=paths,
                )
            )

        if disk.get("IsBoot"):
            reasons.append("Windows reports this as the boot disk (IsBoot).")
        if disk.get("IsSystem"):
            reasons.append(
                "Windows reports this disk holds the system partition (IsSystem)."
            )
        if system_drive and system_drive in letters:
            reasons.append(f"Holds the Windows system volume {system_drive}:.")
        paged = sorted(letters & page_letters)
        if paged:
            reasons.append(
                "Holds an active page file on "
                + ", ".join(f"{p}:" for p in paged)
                + "."
            )
        if hiberfil and system_drive in letters:
            reasons.append("Holds the hibernation file (hiberfil.sys).")
        if interface == "virtual" and bus.lower() in {"storage spaces", "spaces"}:
            reasons.append(
                "Is a Storage Spaces virtual disk; its physical members are "
                "not addressable through it."
            )

        serial = str(disk.get("SerialNumber") or phys.get("SerialNumber") or "").strip()
        model = str(disk.get("Model") or disk.get("FriendlyName") or "").strip()
        limitations: list[str] = []
        if not serial:
            limitations.append(
                "Windows reported no serial number for this disk; it is "
                "identified by its UniqueId instead."
            )
        devices.append(
            NormalizedDevice(
                id=f"PhysicalDrive{number}",
                platform="windows",
                path=f"\\\\.\\PhysicalDrive{number}",
                vendor=str(disk.get("Manufacturer") or "").strip(),
                model=model,
                serial=serial,
                capacity_bytes=int(disk.get("Size") or 0),
                interface=interface,
                media_type=media_type,
                media_basis=media_basis,
                removable=(
                    True
                    if interface in {"usb", "mmc"}
                    else False
                    if interface in {"sata", "nvme", "sas", "scsi"}
                    else None
                ),
                mounted=bool(mount_points),
                mount_points=sorted(set(mount_points)),
                system_device=bool(reasons),
                system_reasons=reasons,
                filesystems=sorted(filesystems),
                partitions=partitions,
                stable_id=str(disk.get("UniqueId") or "").strip(),
                limitations=limitations,
            )
        )
    return devices


def encoded_script() -> str:
    """:data:`INVENTORY_SCRIPT` as ``-EncodedCommand`` wants it.

    Base64 of UTF-16LE. Passing the script this way means no character in it
    is ever subject to Windows command-line quoting, which PowerShell 5.1
    parses differently from ``CreateProcess``.
    """
    return base64.b64encode(INVENTORY_SCRIPT.encode("utf-16-le")).decode("ascii")


def powershell_path() -> str:
    """Absolute path of Windows PowerShell 5.1 in the real system directory.

    ``GetSystemDirectoryW`` rather than ``%SystemRoot%``: the environment is
    the caller's to change, the kernel's answer is not.
    """
    system_dir = ""
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            buffer = ctypes.create_unicode_buffer(260)
            if windll.kernel32.GetSystemDirectoryW(buffer, 260):
                system_dir = buffer.value
    except (OSError, AttributeError):
        system_dir = ""
    if not system_dir:
        system_dir = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32"
        )
    return os.path.join(system_dir, "WindowsPowerShell", "v1.0", "powershell.exe")


class WindowsAdapter(BaseAdapter):
    """Windows 10/11."""

    name = "windows"
    family = "windows"

    def __init__(
        self,
        *,
        helper: str = "in-process",
        helper_basis: str = "",
        runner: Any = None,
    ) -> None:
        super().__init__(helper=helper, helper_basis=helper_basis)
        if runner is None:
            from core.device._sysio import SubprocessRunner

            runner = SubprocessRunner(timeout_s=60.0)
        self._runner = runner

    def inventory(self) -> dict[str, Any]:
        """Run the inventory script. Raises :class:`PlatformUnsupported`."""
        argv = [
            powershell_path(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_script(),
        ]
        result = self._runner.run(argv)
        if not result.ok:
            detail = (result.stderr or result.stdout).strip()[:300]
            raise PlatformUnsupported(
                "Windows storage discovery (Get-Disk) failed: "
                + (detail or f"exit code {result.returncode}"),
                remediation=(
                    "Confirm the Windows Storage module is present "
                    "(Get-Command Get-Disk) and that PowerShell is not blocked "
                    "by policy on this machine."
                ),
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PlatformUnsupported(
                f"Windows storage discovery returned unreadable output ({exc})."
            ) from exc
        if not isinstance(payload, dict):
            raise PlatformUnsupported("Windows storage discovery returned no object.")
        return payload

    def enumerate_devices(
        self, *, include_virtual: bool = False
    ) -> list[NormalizedDevice]:
        try:
            devices = parse_inventory(self.inventory())
        except PlatformUnsupported as exc:
            self.discovery.record(
                ok=False,
                tool="PowerShell Storage module",
                detail=exc.message,
                devices=[],
            )
            raise
        if not include_virtual:
            devices = [item for item in devices if item.interface != "virtual"]
        self.discovery.record(
            ok=True,
            tool="PowerShell Get-Disk / Get-PhysicalDisk / Get-Partition / Get-Volume",
            detail="disks read from the Windows Storage module",
            devices=devices,
        )
        return devices

    def whole_drive_unavailable_reason(self) -> str:
        return (
            "Whole-drive sanitization is not implemented for Windows in this "
            "build. Windows can reach a disk through \\\\.\\PhysicalDriveN and "
            "IOCTL_STORAGE_PROTOCOL_COMMAND, but no engine using them has been "
            "written and validated, so none is offered."
        )

    def _file_limitations(self) -> list[str]:
        return [
            "NTFS keeps files smaller than roughly 700 bytes resident inside "
            "their MFT record, where an overwrite through the file handle does "
            "not reach; such files are reported as not destroyed.",
            "Volume Shadow Copies can only be listed from an elevated prompt; "
            "unelevated, whether one holds the old data is reported as unknown.",
            "Windows offers no unprivileged directory flush, so the rename "
            "chain may linger in the directory index until NTFS flushes it.",
            FLASH_LIMITATION,
        ]

    def restrictions(self) -> list[str]:
        return [
            "Whole-drive sanitization is not available on Windows in this "
            "build; use the Linux build for whole-drive work.",
            "Free-space wipe is Linux-only (fill behaviour not measured on NTFS).",
            "Physical read-back of an erased file needs an elevated process "
            "(raw volume read of \\\\.\\C:); unelevated it is reported as not "
            "verified.",
            "No privileged helper runs on Windows because no implemented "
            "operation needs one; the app never asks to run as Administrator.",
        ]
