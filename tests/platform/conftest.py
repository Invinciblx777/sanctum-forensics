"""Fixtures for the platform-adapter suite.

Everything here runs on every host. The Windows and macOS adapters are driven
from captured command output through a fake runner - their parsing is pure,
and the fake records every argv so the tests can also pin *how* the OS tools
are invoked (absolute paths, no shell, nothing interpolated). Nothing in this
suite opens a device.
"""

from __future__ import annotations

import json
import plistlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from core.device._sysio import CommandResult

FIXTURES = Path(__file__).with_name("fixtures")


class FakeRunner:
    """Answers argv lists from a function and records every call."""

    def __init__(self, answer: Callable[[list[str]], CommandResult]) -> None:
        self.answer = answer
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str]) -> CommandResult:
        args = list(argv)
        self.calls.append(args)
        return self.answer(args)


def ok(argv: list[str], stdout: str | bytes) -> CommandResult:
    text = stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout
    return CommandResult(argv, 0, text, "")


@pytest.fixture
def windows_inventory() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (FIXTURES / "windows_inventory.json").read_text(encoding="utf-8")
    )
    return loaded


def mac_listing() -> dict[str, Any]:
    """``diskutil list -plist`` on an Apple silicon Mac with two externals."""
    return {
        "AllDisks": [
            "disk0",
            "disk0s1",
            "disk0s2",
            "disk0s3",
            "disk3",
            "disk3s1",
            "disk3s5",
            "disk3s6",
            "disk4",
            "disk4s1",
            "disk4s2",
            "disk5",
            "disk5s1",
            "disk6",
            "disk6s1",
        ],
        "WholeDisks": ["disk0", "disk3", "disk4", "disk5", "disk6"],
        "AllDisksAndPartitions": [
            {
                "DeviceIdentifier": "disk0",
                "Size": 500277790720,
                "Content": "GUID_partition_scheme",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk0s1",
                        "Size": 524288000,
                        "Content": "Apple_APFS_ISC",
                    },
                    {
                        "DeviceIdentifier": "disk0s2",
                        "Size": 494384795648,
                        "Content": "Apple_APFS",
                    },
                    {
                        "DeviceIdentifier": "disk0s3",
                        "Size": 5368664064,
                        "Content": "Apple_APFS_Recovery",
                    },
                ],
            },
            {
                "DeviceIdentifier": "disk3",
                "Size": 494384795648,
                "Content": "Apple_APFS_Container",
                "APFSPhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
                "APFSVolumes": [
                    {
                        "DeviceIdentifier": "disk3s1",
                        "VolumeName": "Macintosh HD",
                        "MountPoint": "/",
                    },
                    {
                        "DeviceIdentifier": "disk3s5",
                        "VolumeName": "Data",
                        "MountPoint": "/System/Volumes/Data",
                    },
                    {
                        "DeviceIdentifier": "disk3s6",
                        "VolumeName": "VM",
                        "MountPoint": "/System/Volumes/VM",
                    },
                ],
            },
            {
                "DeviceIdentifier": "disk4",
                "Size": 1000204886016,
                "Content": "GUID_partition_scheme",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk4s1",
                        "Size": 209715200,
                        "Content": "EFI",
                        "VolumeName": "EFI",
                    },
                    {
                        "DeviceIdentifier": "disk4s2",
                        "Size": 999995129856,
                        "Content": "Microsoft Basic Data",
                        "VolumeName": "BACKUP",
                        "MountPoint": "/Volumes/BACKUP",
                    },
                ],
            },
            {
                "DeviceIdentifier": "disk5",
                "Size": 31914983424,
                "Content": "FDisk_partition_scheme",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk5s1",
                        "Size": 31913934848,
                        "Content": "DOS_FAT_32",
                        "VolumeName": "SDCARD",
                    },
                ],
            },
            {
                "DeviceIdentifier": "disk6",
                "Size": 2147483648,
                "Content": "GUID_partition_scheme",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk6s1",
                        "Size": 2147000000,
                        "Content": "Apple_HFS",
                        "VolumeName": "Image",
                        "MountPoint": "/Volumes/Image",
                    },
                ],
            },
        ],
    }


def mac_apfs() -> dict[str, Any]:
    return {
        "Containers": [
            {
                "ContainerReference": "disk3",
                "PhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
                "Volumes": [
                    {
                        "DeviceIdentifier": "disk3s1",
                        "Name": "Macintosh HD",
                        "Roles": ["System"],
                    },
                    {"DeviceIdentifier": "disk3s5", "Name": "Data", "Roles": ["Data"]},
                    {"DeviceIdentifier": "disk3s6", "Name": "VM", "Roles": ["VM"]},
                ],
            }
        ]
    }


def mac_infos() -> dict[str, dict[str, Any]]:
    return {
        "disk0": {
            "DeviceIdentifier": "disk0",
            "MediaName": "APPLE SSD AP0512Z",
            "TotalSize": 500277790720,
            "Internal": True,
            "Removable": False,
            "RemovableMedia": False,
            "SolidState": True,
            "BusProtocol": "Apple Fabric",
            "VirtualOrPhysical": "Physical",
        },
        "disk4": {
            "DeviceIdentifier": "disk4",
            "MediaName": "Samsung PSSD T7",
            "TotalSize": 1000204886016,
            "Internal": False,
            "RemovableMedia": False,
            "SolidState": True,
            "BusProtocol": "USB",
            "VirtualOrPhysical": "Physical",
            "DiskUUID": "T7-UUID",
        },
        "disk5": {
            "DeviceIdentifier": "disk5",
            "MediaName": "SD Card Reader",
            "TotalSize": 31914983424,
            "Internal": False,
            "RemovableMedia": True,
            "BusProtocol": "Secure Digital",
            "VirtualOrPhysical": "Physical",
        },
        "disk6": {
            "DeviceIdentifier": "disk6",
            "MediaName": "Disk Image",
            "TotalSize": 2147483648,
            "Internal": False,
            "RemovableMedia": True,
            "BusProtocol": "Disk Image",
            "VirtualOrPhysical": "Virtual",
        },
    }


def mac_root() -> dict[str, Any]:
    return {
        "DeviceIdentifier": "disk3s1s1",
        "MountPoint": "/",
        "ParentWholeDisk": "disk3",
        "APFSContainerReference": "disk3",
        "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}],
    }


def mac_runner() -> FakeRunner:
    """A runner answering diskutil exactly as the macOS adapter calls it."""
    infos = mac_infos()

    def answer(argv: list[str]) -> CommandResult:
        args = argv[1:]
        if args == ["list", "-plist"]:
            return ok(argv, plistlib.dumps(mac_listing()))
        if args == ["apfs", "list", "-plist"]:
            return ok(argv, plistlib.dumps(mac_apfs()))
        if args == ["info", "-plist", "/"]:
            return ok(argv, plistlib.dumps(mac_root()))
        if args[:2] == ["info", "-plist"] and args[2] in infos:
            return ok(argv, plistlib.dumps(infos[args[2]]))
        return CommandResult(argv, 1, "", f"unexpected {args}")

    return FakeRunner(answer)
