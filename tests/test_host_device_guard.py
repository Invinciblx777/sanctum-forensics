"""The suite's host-device guard refuses what it says it refuses.

Every refused case below is harmless if the guard were broken: the targets do
not exist (so the call would fail with ENOENT) and ``lsblk --version`` only
prints a version. Nothing here can reach a real device even on a guard failure.
Each case removes its own record from ``BLOCKED``, so the session-end verdict
counts only unexpected refusals.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests._host_device_guard import (
    ALLOWED_DISKS,
    BLOCKED,
    HostDeviceAccessBlocked,
    command_reason,
    media_reason,
    path_reason,
)


@contextmanager
def refused() -> Iterator[None]:
    before = len(BLOCKED)
    with pytest.raises(HostDeviceAccessBlocked):
        yield
    assert len(BLOCKED) == before + 1, "the refusal was recorded"
    del BLOCKED[before:]


@pytest.mark.parametrize(
    "attempt",
    [
        lambda: subprocess.run(["lsblk", "--version"], check=False),
        lambda: subprocess.run("lsblk --version", shell=True, check=False),
        lambda: subprocess.run(["cat", "/dev/sdzz9"], check=False),
        lambda: subprocess.run(["dd", "if=/dev/sdzz9", "of=/dev/null"], check=False),
        lambda: open("/dev/sdzz9", "rb"),  # noqa: SIM115
        lambda: os.open("/sys/block/sdzz9/size", os.O_RDONLY),
        lambda: os.open("/sys/class/block/nvme9n9/removable", os.O_RDONLY),
        lambda: os.listdir("/run/media/sanctum-guard-selftest"),
        lambda: os.scandir("/dev/disk/by-id-sanctum-guard-selftest"),
        lambda: open("/dev/disk/by-id/usb-SANCTUM_GUARD_SELFTEST", "rb"),  # noqa: SIM115
    ],
    ids=[
        "lsblk-no-operand",
        "shell-lsblk",
        "argv-names-node",
        "dd-operand",
        "open-node",
        "sysfs-physical-name",
        "sysfs-class-block",
        "list-removable-media",
        "list-dev-disk",
        "open-by-id",
    ],
)
def test_each_route_to_a_host_device_is_refused(attempt: Callable[[], Any]) -> None:
    with refused():
        attempt()


def test_virtual_and_ordinary_targets_stay_allowed(tmp_path: Path) -> None:
    image = tmp_path / "disk.img"
    image.write_bytes(b"\0" * 512)
    assert command_reason(["losetup", "--find", "--show", str(image)]) is None
    assert command_reason(["lsblk", "-no", "SERIAL", "/dev/loop7"]) is None
    assert command_reason(["blkid", str(image)]) is None
    assert command_reason(["git", "status"]) is None
    # A path handed to a program as data is not an access: the harness tests
    # pass "/dev/sda" to a banner function. A device tool inside -c still is.
    banner = 'source gate.sh\ngate_identity "stage usb" "/dev/sda" Model S 1'
    assert command_reason(["bash", "-c", banner]) is None
    assert command_reason(["bash", "-c", "set -e; lsblk -J -O -b"]) is not None
    assert command_reason(["sh", "-c", "hdparm -I /dev/sdzz9 && true"]) is not None
    for path in ("/dev/null", "/dev/urandom", "/dev/loop7", "/proc/self/mountinfo"):
        assert path_reason(path) is None, path
    assert path_reason("/sys/block/loop7/queue/rotational") is None
    assert path_reason(str(image)) is None


def test_only_the_disk_holding_the_suite_is_readable_in_sysfs() -> None:
    for name in ALLOWED_DISKS:
        assert path_reason(f"/sys/block/{name}/queue/rotational") is None
        assert path_reason(f"/dev/{name}") is not None, "its node is still refused"
    assert "sdzz9" not in ALLOWED_DISKS
    assert path_reason("/sys/block/sdzz9/queue/rotational") is not None
    assert path_reason("/sys/block") is not None, "listing the tree is discovery"


def test_removable_media_is_refused_unless_it_is_a_loop_volume() -> None:
    mounts = [
        ("/run/media/someone/LOOPVOL", "/dev/loop7"),
        ("/run/media/someone/STICK", "/dev/sdzz9"),
        ("/run", "tmpfs"),
        ("/", "/dev/nvme9n9p1"),
    ]
    assert media_reason("/run/media/someone/LOOPVOL/file.bin", mounts) is None
    assert media_reason("/run/media/someone/STICK/img01.jpg", mounts) is not None
    assert media_reason("/run/media/someone", mounts) is not None, "the listing"
