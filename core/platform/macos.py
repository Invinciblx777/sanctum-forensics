"""macOS adapter: ``diskutil`` discovery, APFS-aware protection, honest refusal.

Discovery
---------
``/usr/sbin/diskutil`` by absolute path, always with ``-plist``, parsed with
:mod:`plistlib`. Four calls, none of which needs root:

* ``diskutil list -plist`` - every disk, including the synthesized APFS
  container disks and their mounted volumes;
* ``diskutil apfs list -plist`` - containers, their physical stores, and the
  role of each volume (System, Data, VM, Preboot, Recovery);
* ``diskutil info -plist <disk>`` per physical whole disk - bus, internal,
  removable, solid-state, size;
* ``diskutil info -plist /`` - which container the running system boots from.

Parsing is pure (:func:`parse_inventory`) and tested from fixtures on every
host.

APFS, and why it matters for protection
---------------------------------------
On modern macOS the boot volume is not on a partition of a physical disk. It
is a volume in a *synthesized* container disk (``disk3``) whose *physical
store* is a partition (``disk0s2``) of the real disk. A check that looked only
at the physical disk's partitions would find nothing mounted on ``disk0`` and
call the internal SSD a free target. So a physical disk is protected when it
is a physical store of **any** container that holds the booted volume, or a
volume with the System, Data, VM (swap), Preboot or Recovery role, and it is
mounted when any volume of any container it backs is mounted.

Whole-drive sanitization
------------------------
**Not offered in this build.** Internal Apple storage on Apple silicon and T2
Macs is always encrypted by the Secure Enclave, and the purge-capable path is
macOS's own *Erase All Content and Settings*, which destroys those keys; this
app cannot perform it and cannot verify it, so it is named as the recommended
action rather than claimed. For external media ``diskutil`` can overwrite a
disk, but that path has not been validated here, so it is not offered either.
"""

from __future__ import annotations

import plistlib
import re
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

__all__ = ["MacOSAdapter", "parse_inventory", "DISKUTIL", "PROTECTED_ROLES"]

logger = structlog.get_logger(__name__)

#: Absolute, never looked up on PATH.
DISKUTIL = "/usr/sbin/diskutil"

#: APFS volume roles that belong to an installed macOS.
PROTECTED_ROLES = frozenset({"System", "Data", "VM", "Preboot", "Recovery", "Update"})

_BUS_TO_INTERFACE: dict[str, Interface] = {
    "usb": "usb",
    "pci-express": "nvme",
    "pci": "nvme",
    "nvme": "nvme",
    "apple fabric": "nvme",
    "sata": "sata",
    "ata": "sata",
    "sas": "sas",
    "thunderbolt": "thunderbolt",
    "secure digital": "mmc",
    "sd": "mmc",
    "disk image": "virtual",
    "virtual interface": "virtual",
}

_CONTENT_FS = {
    "apple_apfs": "APFS",
    "apple_hfs": "HFS+",
    "apple_hfsx": "HFS+",
    "microsoft basic data": "FAT/exFAT/NTFS",
    "windows_ntfs": "NTFS",
    "dos_fat_32": "FAT32",
    "dos_fat_16": "FAT16",
    "windows_fat_32": "FAT32",
    "linux filesystem": "Linux",
    "linux": "Linux",
    "efi": "EFI",
}

_WHOLE = re.compile(r"^(disk\d+)")


def _whole(identifier: str) -> str:
    match = _WHOLE.match(identifier or "")
    return match.group(1) if match else ""


def _load(blob: bytes | str | None) -> dict[str, Any]:
    if not blob:
        return {}
    data = blob.encode("utf-8") if isinstance(blob, str) else blob
    try:
        loaded = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _media(info: dict[str, Any], interface: Interface) -> tuple[MediaType, str]:
    solid = info.get("SolidState")
    if interface == "mmc":
        return "flash", "The disk is on an SD card reader, which carries only flash."
    if solid is True:
        return "ssd", "diskutil reports SolidState: true."
    if interface == "usb":
        return "flash", (
            "The disk is USB-attached and diskutil does not report it as "
            "solid-state. It is treated as flash, so the flash limitation is "
            "never left out."
        )
    if solid is False:
        return "hdd", "diskutil reports SolidState: false."
    return "unknown", "diskutil did not report whether the disk is solid-state."


def parse_inventory(
    listing: dict[str, Any],
    apfs: dict[str, Any],
    infos: dict[str, dict[str, Any]],
    root_info: dict[str, Any],
) -> list[NormalizedDevice]:
    """Normalize ``diskutil`` plists. Pure; no I/O.

    Args:
        listing: ``diskutil list -plist``.
        apfs: ``diskutil apfs list -plist``.
        infos: ``diskutil info -plist <disk>`` per physical whole disk.
        root_info: ``diskutil info -plist /``.
    """
    entries = [
        item
        for item in listing.get("AllDisksAndPartitions") or []
        if isinstance(item, dict)
    ]

    # Container disk -> physical whole disks backing it.
    backing: dict[str, set[str]] = {}
    for entry in entries:
        stores = entry.get("APFSPhysicalStores") or []
        if stores:
            backing[str(entry.get("DeviceIdentifier"))] = {
                _whole(str(store.get("DeviceIdentifier", ""))) for store in stores
            }
    roles_by_container: dict[str, set[str]] = {}
    for container in apfs.get("Containers") or []:
        ref = str(container.get("ContainerReference") or "")
        stores = {
            _whole(str(store.get("DeviceIdentifier", "")))
            for store in container.get("PhysicalStores") or []
        }
        if ref and stores:
            backing.setdefault(ref, set()).update(stores)
        roles: set[str] = set()
        for volume in container.get("Volumes") or []:
            roles.update(str(role) for role in volume.get("Roles") or [])
        roles_by_container[ref] = roles

    # Where the running system boots from.
    boot_physical: set[str] = set()
    for store in root_info.get("APFSPhysicalStores") or []:
        boot_physical.add(
            _whole(
                str(
                    store.get("APFSPhysicalStore")
                    or store.get("DeviceIdentifier")
                    or ""
                )
            )
        )
    boot_container = str(root_info.get("APFSContainerReference") or "")
    if boot_container:
        boot_physical |= backing.get(boot_container, set())
    parent = str(root_info.get("ParentWholeDisk") or "")
    if parent:
        boot_physical |= backing.get(parent, {parent})
    boot_physical.discard("")

    # Mount points per physical disk, through containers.
    mounts: dict[str, list[str]] = {}
    fs_by_disk: dict[str, set[str]] = {}
    parts_by_disk: dict[str, list[PartitionInfo]] = {}
    role_reasons: dict[str, set[str]] = {}
    for entry in entries:
        ident = str(entry.get("DeviceIdentifier") or "")
        physical = backing.get(ident, {ident})
        volumes = list(entry.get("APFSVolumes") or []) + list(
            entry.get("Partitions") or []
        )
        if entry.get("MountPoint"):
            volumes.append(entry)
        for volume in volumes:
            point = str(volume.get("MountPoint") or "")
            content = str(volume.get("Content") or "")
            fs = _CONTENT_FS.get(content.lower(), "APFS" if ident in backing else "")
            for disk in physical:
                if point:
                    mounts.setdefault(disk, []).append(point)
                if fs and fs != "EFI":
                    fs_by_disk.setdefault(disk, set()).add(fs)
        if ident not in backing:
            parts_by_disk[ident] = [
                PartitionInfo(
                    id=str(part.get("DeviceIdentifier") or ""),
                    size_bytes=int(part.get("Size") or 0),
                    filesystem=_CONTENT_FS.get(
                        str(part.get("Content") or "").lower(),
                        str(part.get("Content") or ""),
                    ),
                    label=str(part.get("VolumeName") or ""),
                    mount_points=[str(part["MountPoint"])]
                    if part.get("MountPoint")
                    else [],
                )
                for part in entry.get("Partitions") or []
            ]
        roles = roles_by_container.get(ident, set()) & PROTECTED_ROLES
        if roles:
            for disk in physical:
                role_reasons.setdefault(disk, set()).update(roles)

    devices: list[NormalizedDevice] = []
    for ident, info in sorted(infos.items()):
        bus = str(info.get("BusProtocol") or "")
        interface = _BUS_TO_INTERFACE.get(bus.lower(), "unknown")
        if info.get("VirtualOrPhysical") == "Virtual":
            interface = "virtual"
        media_type, basis = _media(info, interface)
        reasons: list[str] = []
        if ident in boot_physical:
            reasons.append(
                "The running macOS boots from an APFS container on this disk."
            )
        roles = role_reasons.get(ident, set())
        if roles:
            reasons.append(
                "Backs APFS volumes with the "
                + ", ".join(sorted(roles))
                + " role(s), which belong to a macOS installation."
            )
        points = sorted(set(mounts.get(ident, [])))
        removable = info.get("RemovableMedia", info.get("Removable"))
        if removable is None and info.get("Internal") is not None:
            removable = not bool(info.get("Internal"))
        devices.append(
            NormalizedDevice(
                id=ident,
                platform="macos",
                path=f"/dev/{ident}",
                model=str(
                    info.get("MediaName") or info.get("IORegistryEntryName") or ""
                ).strip(),
                serial="",
                capacity_bytes=int(info.get("TotalSize") or info.get("Size") or 0),
                interface=interface,
                media_type=media_type,
                media_basis=basis,
                removable=bool(removable) if removable is not None else None,
                mounted=bool(points),
                mount_points=points,
                system_device=bool(reasons),
                system_reasons=reasons,
                filesystems=sorted(fs_by_disk.get(ident, set())),
                partitions=parts_by_disk.get(ident, []),
                stable_id=str(info.get("DiskUUID") or info.get("MediaUUID") or ""),
                limitations=[
                    "diskutil does not report a serial number; the disk is "
                    "identified by its BSD name and size."
                ],
            )
        )
    return devices


class MacOSAdapter(BaseAdapter):
    """macOS 12 and later."""

    name = "macos"
    family = "macos"

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

    def _diskutil(self, *args: str, required: bool = True) -> dict[str, Any]:
        result = self._runner.run([DISKUTIL, *args])
        if not result.ok:
            if not required:
                return {}
            raise PlatformUnsupported(
                f"diskutil {' '.join(args)} failed: "
                + (
                    (result.stderr or result.stdout).strip()[:300]
                    or f"exit {result.returncode}"
                ),
                remediation="Confirm /usr/sbin/diskutil runs from Terminal.",
            )
        return _load(result.stdout)

    def inventory(self) -> list[NormalizedDevice]:
        listing = self._diskutil("list", "-plist")
        apfs = self._diskutil("apfs", "list", "-plist", required=False)
        root = self._diskutil("info", "-plist", "/", required=False)
        wholes = [str(name) for name in listing.get("WholeDisks") or []]
        backed = {
            str(entry.get("DeviceIdentifier"))
            for entry in listing.get("AllDisksAndPartitions") or []
            if entry.get("APFSPhysicalStores")
        }
        infos: dict[str, dict[str, Any]] = {}
        for ident in wholes:
            if ident in backed:
                continue  # synthesized container, reported through its stores
            if not re.fullmatch(r"disk\d+", ident):
                continue  # never pass anything but a BSD whole-disk name
            # A failed info call still lists the disk, with its unknowns
            # unknown, rather than hiding a device the listing showed.
            infos[ident] = self._diskutil("info", "-plist", ident, required=False)
        return parse_inventory(listing, apfs, infos, root)

    def enumerate_devices(
        self, *, include_virtual: bool = False
    ) -> list[NormalizedDevice]:
        try:
            devices = self.inventory()
        except PlatformUnsupported as exc:
            self.discovery.record(
                ok=False, tool="diskutil", detail=exc.message, devices=[]
            )
            raise
        if not include_virtual:
            devices = [item for item in devices if item.interface != "virtual"]
        self.discovery.record(
            ok=True,
            tool="diskutil list / apfs list / info (-plist)",
            detail="disks read from diskutil",
            devices=devices,
        )
        return devices

    def whole_drive_unavailable_reason(self) -> str:
        return (
            "Whole-drive sanitization is not offered on macOS in this build. "
            "Internal Apple storage is purged by macOS's own Erase All Content "
            "and Settings, which this app cannot perform or verify, and an "
            "overwrite path for external disks has not been validated."
        )

    def whole_drive_recommended_action(self) -> str:
        return (
            "Internal Mac storage: use System Settings > General > Transfer or "
            "Reset > Erase All Content and Settings (Apple silicon / T2), which "
            "destroys the storage encryption keys. External disks: sanitize "
            "with the Sanctum Linux build (AppImage) on a Linux host."
        )

    def _file_limitations(self) -> list[str]:
        return [
            "APFS is copy-on-write: an overwrite is written to new blocks and "
            "the original blocks are only released, so the old content is not "
            "guaranteed destroyed and physical verification is not possible. "
            "The report records this for every file on APFS.",
            "Local Time Machine snapshots can keep a deleted file's content; "
            "they are listed only when tmutil can be queried.",
            FLASH_LIMITATION,
        ]

    def restrictions(self) -> list[str]:
        return [
            "Whole-drive sanitization is not offered on macOS in this build.",
            "APFS copy-on-write means file overwrite cannot destroy the "
            "original blocks; file erase on APFS is removal plus a residual "
            "report, never a verified destruction.",
            "diskutil reports no drive serial numbers.",
            "Free-space wipe is Linux-only.",
            "No privileged helper runs on macOS because no implemented "
            "operation needs one.",
        ]
