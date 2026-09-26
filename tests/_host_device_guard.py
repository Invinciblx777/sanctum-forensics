"""No test reaches a host block device. Enforced here, not left to convention.

``tests/conftest.py`` has always said "no real device access anywhere in the
suite". Until 2026-09-25 that was a convention, and five tests broke it: they
built the real Linux adapter, whose capability matrix runs device discovery
(``lsblk`` over sysfs) against whatever happens to be plugged into the machine
running the suite. Read-only, but it enumerated real disks, including removable
media a person had attached for a different purpose.

:func:`install` adds a :func:`sys.addaudithook` hook. An audit event is raised
*before* the operation it describes, so a refused call never reaches the kernel.
The hook refuses:

* launching a block-device tool (``lsblk``, ``blkid``, ``hdparm``, ...) unless
  the command names a loop device or a regular file - with no such operand these
  tools mean "every device", which is exactly discovery - including as a simple
  command inside a shell's ``-c`` script;
* pointing a block-device tool or a raw read/write tool (``dd``, ``cat``, ...)
  at a physical block-device node or a removable-media mount;
* opening or listing a physical block-device node, ``/dev/disk``, the kernel's
  block-device trees in ``/sys`` and ``/proc``, udev's device database, and
  anything under ``/run/media`` or ``/media`` that is not a loop volume.

Allowed, deliberately:

* the sysfs attributes of the disk that holds the suite's own files (the
  repository and the temporary directory). The file-eraser tests erase files on
  a real block-backed filesystem beside the repository on purpose - tmpfs cannot
  answer their questions - and the eraser reads that disk's queue attributes to
  judge whether TRIM may have remapped a page. The disk is worked out once, from
  the mount table and sysfs, *before* the hook exists, and the terminal summary
  names it on every run;
* loop devices and files on loop volumes (the udisks loop tests), and the mount
  table itself, which names mounts but touches no device.

A refusal raises :class:`HostDeviceAccessBlocked`, a ``PermissionError``: the
code under test sees what an unprivileged process would, so a refusal inside a
worker thread fails that job instead of hanging the suite. It is also recorded
in :data:`BLOCKED`, and ``tests/conftest.py`` fails any session with a refusal
in it - including one the code under test caught and handled.

Scope, stated plainly: this process. A child process is outside the hook. The
suite's children are bash snippets of the harness gate library (which never
call its device probes) and Python scripts on synthetic inputs.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

__all__ = [
    "ALLOWED_DISKS",
    "BLOCKED",
    "HostDeviceAccessBlocked",
    "command_reason",
    "install",
    "media_reason",
    "path_reason",
]


class HostDeviceAccessBlocked(PermissionError):
    """A test tried to reach a host block device. See the module docstring."""


#: Every refusal made in this process, ``"<test id>: <reason>"``.
BLOCKED: list[str] = []
#: Kernel names of the disk holding the suite's files, whose sysfs may be read.
ALLOWED_DISKS: frozenset[str] = frozenset()
_ALLOWED_DEVNUMS: frozenset[str] = frozenset()

_NAME = (
    r"(?:sd[a-z]+\d*|hd[a-z]+\d*|vd[a-z]+\d*|xvd[a-z]+\d*"
    r"|nvme\d+(?:n\d+(?:p\d+)?)?|ng\d+n\d+|mmcblk\d+(?:p\d+|boot\d+|rpmb)?"
    r"|sr\d+|sg\d+|dm-\d+|md\d+(?:p\d+)?)"
)
_NODE = re.compile(rf"^/dev/(?:{_NAME}|(?:disk|mapper|block|bsg)(?:/.*)?)$")
_KERNEL_NAME = re.compile(rf"^{_NAME}$")
_MEDIA = ("/run/media", "/media")
_ROOTS = (
    "/run/udev/data",
    "/dev/disk",
    "/sys/class/scsi_disk",
    "/sys/class/nvme",
    "/sys/bus/scsi",
    "/sys/bus/usb",
)
_TREES = ("/sys/block", "/sys/class/block")
_DEVNUMS = "/sys/dev/block"
_TABLES = frozenset({"/proc/partitions", "/proc/diskstats", "/proc/scsi/scsi"})
_TOOLS = frozenset(
    {
        "lsblk", "blkid", "findfs", "hdparm", "smartctl", "nvme", "sdparm",
        "sedutil-cli", "blockdev", "wipefs", "parted", "sfdisk", "fdisk",
        "sgdisk", "gdisk", "partprobe", "udevadm", "udisksctl", "mount",
        "umount", "eject", "cryptsetup", "dmsetup", "badblocks", "lsscsi",
        "lsusb",
    }
)  # fmt: skip
#: Tools that read or write whatever path they are given. Naming a physical
#: node to one of these is refused; to any other program a path is data (the
#: harness tests pass "/dev/sda" as a label to a banner function).
_RAW_IO = frozenset(
    {
        "dd", "cat", "head", "tail", "od", "xxd", "hexdump", "shred", "cmp",
        "cp", "pv", "sha256sum", "sha1sum", "md5sum", "b2sum",
    }
)  # fmt: skip
_SHELLS = frozenset({"sh", "bash", "dash", "zsh"})
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "\n"})


# --------------------------------------------------------------------------
# The mount table
# --------------------------------------------------------------------------


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), field)


def _mounts() -> list[tuple[str, str]]:
    """``(mount point, source)`` for every mount, longest mount point first."""
    rows: list[tuple[str, str]] = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                rows.append((_unescape(fields[4]), fields[fields.index("-") + 2]))
    except (OSError, ValueError, IndexError):
        return []
    return sorted(rows, key=lambda row: len(row[0]), reverse=True)


def _mount_of(path: str, mounts: list[tuple[str, str]]) -> tuple[str, str] | None:
    for point, source in mounts:
        if path == point or path.startswith(point.rstrip("/") + "/"):
            return point, source
    return None


def media_reason(path: str, mounts: list[tuple[str, str]]) -> str | None:
    """Refuse a removable-media path unless it lies on a loop volume."""
    hit = _mount_of(path, mounts)
    if hit and hit[1].startswith("/dev/loop") and hit[0] != "/":
        return None
    return f"{path} (removable media, not a loop volume)"


# --------------------------------------------------------------------------
# Paths and commands
# --------------------------------------------------------------------------


def path_reason(raw: Any) -> str | None:
    """Why opening ``raw`` is refused, or ``None`` if it is allowed."""
    if isinstance(raw, int):
        return None  # an already-open descriptor
    try:
        path = os.path.abspath(os.fsdecode(raw))
    except (TypeError, ValueError):
        return None
    if _NODE.match(path):
        return f"block-device node {path}"
    if path in _TABLES:
        return f"kernel block-device table {path}"
    for root in _MEDIA:
        if path == root or path.startswith(root + "/"):
            return media_reason(path, _mounts())
    for root in _ROOTS:
        if path == root or path.startswith(root + "/"):
            return f"{path} (under {root})"
    for tree in (*_TREES, _DEVNUMS):
        if path == tree:
            return f"block-device tree {tree} (discovery)"
        if path.startswith(tree + "/"):
            name = path[len(tree) + 1 :].split("/", 1)[0]
            if tree == _DEVNUMS:
                allowed = name in _ALLOWED_DEVNUMS
            else:
                allowed = name in ALLOWED_DISKS or not _KERNEL_NAME.match(name)
            if not allowed:
                return f"sysfs entry of a block device other than the suite's {path}"
    return None


def _listing_reason(raw: Any) -> str | None:
    reason = path_reason(raw)
    if reason:
        return reason
    try:
        path = os.path.abspath(os.fsdecode(raw))
    except (TypeError, ValueError):
        return None
    return "a listing of /dev (discovery)" if path == "/dev" else None


def _glob_reason(pattern: Any) -> str | None:
    try:
        text = os.fsdecode(pattern)
    except (TypeError, ValueError):
        return None
    head = text.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
    if head.startswith("/dev/") and not head.startswith(("/dev/shm", "/dev/fd")):
        return f"a glob over device nodes {text}"
    return _listing_reason(os.path.dirname(head) or head) if head else None


def _is_virtual(token: str) -> bool:
    return token.startswith("/dev/loop") or os.path.isfile(token)


def _operands(argv: list[str]) -> list[str]:
    return [part for token in argv[1:] for part in {token, token.split("=", 1)[-1]}]


def _simple_command_reason(argv: list[str]) -> str | None:
    tool = PurePosixPath(argv[0]).name
    operands = _operands(argv)
    if tool in _TOOLS or tool.startswith("sg_") or tool in _RAW_IO:
        for part in operands:
            reason = path_reason(part) if part.startswith("/") else None
            if reason:
                return f"{tool} naming {reason}: {shlex.join(argv)}"
    if (tool in _TOOLS or tool.startswith("sg_")) and not any(
        _is_virtual(part) for part in operands
    ):
        return (
            f"{tool} with no loop-device or regular-file operand, which "
            f"addresses every device: {shlex.join(argv)}"
        )
    return None


def _script_commands(script: str) -> list[list[str]]:
    lexer = shlex.shlex(
        script.replace("\n", "\n ; "), posix=True, punctuation_chars=True
    )
    lexer.whitespace_split = True
    commands: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token in _SEPARATORS or set(token) <= set(";&|()"):
                commands.append([])
            else:
                commands[-1].append(token)
    except ValueError:
        return []
    return [command for command in commands if command]


def command_reason(argv: list[str]) -> str | None:
    """Why launching ``argv`` is refused, or ``None`` if it is allowed.

    A shell's ``-c`` script is split into its simple commands and each is
    judged; a script ``source``-d from a file is outside this view.
    """
    if not argv:
        return None
    if PurePosixPath(argv[0]).name in _SHELLS and "-c" in argv[1:-1]:
        script = argv[argv.index("-c", 1) + 1]
        for command in _script_commands(script):
            reason = _simple_command_reason(command)
            if reason:
                return reason
        return None
    return _simple_command_reason(argv)


def _argv(value: Any) -> list[str]:
    if isinstance(value, (str, bytes)):
        text = os.fsdecode(value)
        try:
            return shlex.split(text)
        except ValueError:
            return [text]
    try:
        return [os.fsdecode(item) for item in value]
    except TypeError:
        return []


# --------------------------------------------------------------------------
# The hook
# --------------------------------------------------------------------------


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if event == "open":
        reason = path_reason(args[0])
    elif event in ("os.listdir", "os.scandir"):
        reason = _listing_reason(args[0] if args[0] is not None else ".")
    elif event in ("glob.glob", "glob.glob/2"):
        reason = _glob_reason(args[0])
    elif event in ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn"):
        reason = command_reason(_argv(args[1]))
    elif event == "os.system":
        reason = command_reason(_argv(args[0]))
    else:
        return
    if reason:
        test = os.environ.get("PYTEST_CURRENT_TEST", "outside a test").split(" ")[0]
        BLOCKED.append(f"{test}: {reason}")
        raise HostDeviceAccessBlocked(
            f"refused {reason}. The suite never reaches a host block device; "
            "give this test a fake probe or fixture instead."
        )


def _lineage(name: str, depth: int = 0) -> set[str]:
    """``name``, the disk it is a partition of, and the devices it is built on."""
    found = {name}
    real = os.path.realpath(f"/sys/class/block/{name}")
    if os.path.exists(os.path.join(real, "partition")):
        found.add(os.path.basename(os.path.dirname(real)))
    slaves = os.path.join(real, "slaves")
    if depth < 8 and os.path.isdir(slaves):
        for slave in os.listdir(slaves):
            found |= _lineage(slave, depth + 1)
    return found


def _disks_holding(roots: Iterable[str]) -> set[str]:
    """Kernel names of the block devices that hold ``roots``, and their disks."""
    mounts = _mounts()
    names: set[str] = set()
    for root in roots:
        real = os.path.realpath(root)
        hit = _mount_of(real, mounts)
        if hit and hit[1].startswith("/dev/") and os.path.exists(hit[1]):
            names |= _lineage(os.path.basename(os.path.realpath(hit[1])))
        try:
            dev = os.stat(real).st_dev
        except OSError:
            continue
        if os.major(dev):  # 0 is tmpfs, btrfs subvolumes, overlays
            link = f"{_DEVNUMS}/{os.major(dev)}:{os.minor(dev)}"
            names |= _lineage(os.path.basename(os.path.realpath(link)))
    return {name for name in names if _KERNEL_NAME.match(name)}


_installed = False


def install(roots: Iterable[str]) -> None:
    """Work out the suite's own disk from ``roots``, then add the hook, once.

    The disk is computed before the hook exists, so working it out is not
    itself refused. The hook cannot be removed afterwards.
    """
    global _installed, ALLOWED_DISKS, _ALLOWED_DEVNUMS
    if _installed:
        return
    if sys.platform.startswith("linux"):
        ALLOWED_DISKS = frozenset(_disks_holding(roots))
        numbers = set()
        for name in ALLOWED_DISKS:
            try:
                with open(f"/sys/class/block/{name}/dev", encoding="ascii") as handle:
                    numbers.add(handle.read().strip())
            except OSError:
                continue
        _ALLOWED_DEVNUMS = frozenset(numbers)
    sys.addaudithook(_hook)
    _installed = True
