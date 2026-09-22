"""Run the existing benchmark corpus against one real removable device.

Every recovery figure in ``docs/performance/benchmark.md`` was measured on
image files on this host's NVMe disk. No controller, no USB bridge, no read
error and no acquisition is in any of them. This script closes exactly that
gap for **one** device and **one** geometry, by reusing the builders, the
ground-truth manifest and the scorer the synthetic benchmark already uses, so
that the only variable is the medium.

It is deliberately separate subcommands rather than one run, because exactly
one of them writes to a device and the rest do not:

    preflight   read-only. Identity, safety and declared-configuration match.
    build       read-only with respect to the device. Builds the corpus image
                on host storage, from testkit.benchmark's own builder.
    backup      read-only with respect to the device. Copies the region the
                write would overwrite onto host storage, sized from the built
                image, with a provenance record naming device and extent.
    verify-backup
                read-only. Measures the range the write can modify from the
                built image, and reports whether the backup covers it.
    plan        read-only. The whole pre-write picture - identity, mounts,
                offsets, backup coverage, and the command that would follow -
                for a human to approve or refuse. It never writes.
    write       **DESTRUCTIVE.** Copies that image onto the device. Requires
                --i-understand-this-destroys-data, the typed serial, and a
                backup that covers the measured write extent; it re-runs the
                whole of verify-backup itself rather than trusting a report.
    acquire     read-only. Reads the device back into an image, with a write
                block applied, and records throughput and read errors.
    score       read-only. Ground truth from the acquired image, one carve run,
                the scorer, and the comparison against the pre-registered
                synthetic baseline.

Nothing here decides its own target. ``--device`` names it, ``--expect-serial``
declares what that device must report, and a mismatch is a refusal rather than
a prompt. The baseline is read from the checked-in CSV before the run, never
chosen afterwards.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO))

MIB = 1024 * 1024

#: The geometry the synthetic baseline was measured on. Changing this without
#: changing ``BASELINE_IMAGE`` would compare two different experiments.
MEDIA_SIZE = 255 * MIB
BASELINE_IMAGE = "media-fat32-255m.img"
BASELINE_TOOL = "sanctum-carve"
BASELINE_CSV = REPO / "docs" / "performance" / "benchmark.csv"

#: Pre-registered decision rule, from the experiment proposal. Stated here so
#: it cannot be chosen after the numbers are in.
PASS_WITHIN_POINTS = 5.0
FAIL_BELOW_POINTS = 10.0

#: A device larger than this is not a test stick, and reading it whole into
#: memory to compute ground truth would not fit either.
MAX_SANE_BYTES = 128 * 1024 * 1024 * 1024

#: Where the kernel publishes block devices. A name, not a literal inside the
#: probe, so the tests can point it at a tree they built.
SYSFS_BLOCK = Path("/sys/block")


class Refused(RuntimeError):
    """A safety gate said no. Nothing was written."""

    #: Which kind of no. A safety refusal is about the target - wrong device,
    #: mounted, wrong serial - and no amount of privilege changes it.
    kind = "safety"


class PrivilegeRefused(Refused):
    """The right target, but this account may not open it. Nothing was written.

    Kept apart from a safety refusal because the remedy is different: a safety
    refusal says *do not use this device*, this one says only that the
    operating system has not granted access to it. The script never obtains
    that access itself.
    """

    kind = "privilege"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 * MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lsblk(device: str) -> dict[str, Any]:
    out = subprocess.run(
        [
            "lsblk",
            "--json",
            "-b",
            "-o",
            "NAME,PATH,SIZE,TYPE,TRAN,RM,RO,FSTYPE,LABEL,MOUNTPOINTS,MODEL,SERIAL,PKNAME",
            device,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload: dict[str, Any] = json.loads(out.stdout)
    devices: list[dict[str, Any]] = payload.get("blockdevices", [])
    if not devices:
        raise Refused(f"{device}: lsblk reported no such device")
    return devices[0]


def _root_disk() -> str:
    source = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not source:
        return ""
    return subprocess.run(
        ["lsblk", "-no", "PKNAME", source],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip().splitlines()[0:1][0] if source else ""


def _kernel_serial(name: str, transport: str) -> tuple[str | None, str | None]:
    """The device's serial read straight from sysfs, and the file it came from.

    ``lsblk`` reports the serial udev stored in its database, so checking the
    expected serial against ``lsblk`` alone checks one source against itself.
    This reads the kernel's own attribute instead: for a USB device, the
    ``serial`` string of the USB device the disk hangs off, which is what udev
    derives its value from; otherwise the disk's own ``serial`` attribute or
    its SCSI unit serial page (VPD 0x80).

    Returns ``(None, None)`` when no such attribute exists. Nothing is guessed
    and nothing is normalised beyond the surrounding whitespace sysfs appends.
    Both sources ultimately report what the device's firmware says, so their
    agreement proves the identity was read consistently - not that the
    firmware is honest. That is what the physical inspection is for.
    """
    try:
        here = (SYSFS_BLOCK / name / "device").resolve(strict=True)
    except OSError:
        return None, None
    candidates: list[Path] = []
    if transport == "usb":
        for parent in (here, *here.parents):
            if (parent / "idVendor").is_file() and (parent / "serial").is_file():
                candidates.append(parent / "serial")
                break
    else:
        candidates += [here / "serial", here / "vpd_pg80"]
    for path in candidates:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if path.name == "vpd_pg80":
            # Page header: 4 bytes, the payload length in bytes 2-3.
            length = int.from_bytes(raw[2:4], "big") if len(raw) >= 4 else 0
            raw = raw[4 : 4 + length]
        try:
            value = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            continue
        if value:
            return value, str(path)
    return None, None


def serial_cross_check(node: dict[str, Any]) -> dict[str, Any]:
    """Two machine sources for the serial, and whether they agree.

    ``AGREE`` and ``DISAGREE`` are the two answers; ``UNVERIFIED`` means the
    independent source could not be read, and it is never promoted to
    agreement. None of the three is a human confirmation, which is still the
    operator typing the serial into ``--confirm-serial``.
    """
    lsblk_value = str(node.get("serial") or "").strip() or None
    kernel_value, kernel_path = _kernel_serial(
        str(node.get("name") or ""), str(node.get("tran") or "")
    )
    if kernel_value is None or lsblk_value is None:
        status = "UNVERIFIED"
    elif kernel_value == lsblk_value:
        status = "AGREE"
    else:
        status = "DISAGREE"
    return {
        "source_a": {"name": "lsblk SERIAL (udev database)", "value": lsblk_value},
        "source_b": {
            "name": kernel_path or "sysfs (unavailable)",
            "value": kernel_value,
        },
        "status": status,
    }


def device_privilege(device: str) -> dict[str, Any]:
    """Whether this account may open the device, asked without opening it."""
    readable = os.access(device, os.R_OK)
    writable = os.access(device, os.W_OK)
    return {
        "account_uid": os.geteuid() if hasattr(os, "geteuid") else None,
        "read": readable,
        "write": writable,
        "status": (
            "sufficient to read"
            if readable
            else "INSUFFICIENT - this account cannot read the raw device"
        ),
    }


def _mountpoints(node: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for point in node.get("mountpoints") or []:
        if point:
            found.append(str(point))
    for child in node.get("children") or []:
        found.extend(_mountpoints(child))
    return found


def preflight(
    device: str,
    *,
    expect_serial: str,
    expect_min_bytes: int = MEDIA_SIZE,
    allow_fixed: bool = False,
) -> dict[str, Any]:
    """Identity and safety, before anything is built or written.

    Refuses on: a non-block target, a partition given where a disk is meant,
    the disk holding the running root filesystem, any mounted filesystem on the
    device or its partitions, a non-removable device without an explicit
    override, an absent or mismatched serial, an unexpectedly read-only device,
    a device too small for the corpus, and anything over the sanity limit.
    """
    path = Path(device)
    if not path.exists():
        raise Refused(f"{device} does not exist")
    if not path.is_block_device():
        raise Refused(f"{device} is not a block device")

    node = _lsblk(device)
    if node.get("type") != "disk":
        raise Refused(
            f"{device} is a {node.get('type')}, not a whole disk. "
            "Point this at the disk, not a partition."
        )

    name = str(node.get("name") or "")
    root = _root_disk()
    if root and root == name:
        raise Refused(f"{device} holds the running root filesystem")

    mounted = _mountpoints(node)
    if mounted:
        raise Refused(
            f"{device} has mounted filesystems: {', '.join(mounted)}. "
            "Unmount them yourself - if you did not know it was mounted, you "
            "do not yet know what is on it."
        )

    if not node.get("rm") and not allow_fixed:
        raise Refused(
            f"{device} is not removable. Pass --allow-fixed only if certain."
        )
    if node.get("ro"):
        raise Refused(
            f"{device} reports read-only. That is unexpected for a test stick "
            "and may mean a failing controller; refusing rather than guessing."
        )

    serial = str(node.get("serial") or "").strip()
    if not serial:
        raise Refused(
            f"{device} reports no serial, so it cannot be identified. "
            "Refusing to use a device whose identity is ambiguous."
        )
    if serial != expect_serial:
        raise Refused(
            f"{device} reports serial {serial!r}, the declared test "
            f"configuration says {expect_serial!r}. Nothing was written."
        )
    identity = serial_cross_check(node)
    if identity["status"] == "DISAGREE":
        raise Refused(
            f"{device}: lsblk reports serial {identity['source_a']['value']!r} but "
            f"{identity['source_b']['name']} reports "
            f"{identity['source_b']['value']!r}. Two sources for one device's "
            "identity disagree, so neither is trusted. Nothing was written."
        )

    size = int(node.get("size") or 0)
    if size > MAX_SANE_BYTES:
        raise Refused(f"{device} is {size} bytes, over the sanity limit")
    if size < expect_min_bytes:
        raise Refused(
            f"{device} is {size} bytes, smaller than the {expect_min_bytes}-byte corpus"
        )

    return {
        "device": device,
        "kernel_name": name,
        "model": str(node.get("model") or "").strip(),
        "serial": serial,
        "serial_check": identity,
        "size_bytes": size,
        "transport": node.get("tran"),
        "removable": bool(node.get("rm")),
        "read_only": bool(node.get("ro")),
        "mountpoints": mounted,
        "partitions": [
            {
                "name": child.get("name"),
                "fstype": child.get("fstype"),
                "label": child.get("label"),
                "size_bytes": child.get("size"),
            }
            for child in (node.get("children") or [])
        ],
        "root_disk": root,
        "verdict": "SAFE",
    }


def _host_facts() -> dict[str, Any]:
    def _run(*argv: str) -> str:
        return subprocess.run(
            argv, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "commit": _run("git", "-C", str(REPO), "rev-parse", "HEAD"),
        "branch": _run("git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_run("git", "-C", str(REPO), "status", "--porcelain")),
        "kernel": _run("uname", "-sr"),
        "python": sys.version.split()[0],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_corpus(work: Path, *, seed: int = 0) -> dict[str, Any]:
    """Build the 255 MiB FAT32 corpus image with testkit's own builder."""
    from testkit.benchmark import (
        TRUTH_SUFFIX,
        Plant,
        _build_fat32_media,
        _truth_from_plants,
        media_plants,
    )
    from testkit.damage import write_truth

    images = work / "images"
    payloads = work / "payloads"
    stage = work / "stage"
    images.mkdir(parents=True, exist_ok=True)
    payloads.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    plants: list[Plant] = list(media_plants(rng))
    image = images / BASELINE_IMAGE
    every, notes = _build_fat32_media(image, MEDIA_SIZE, plants, rng, stage)
    truth = _truth_from_plants(
        image,
        corpus="media",
        model="delete",
        description=f"FAT32 {MEDIA_SIZE // MIB} MiB built for the physical run",
        filesystem="fat32",
        plants=every,
        payloads=payloads,
        unformatted=frozenset({"pad-a.bin", "pad-b.bin"}),
        notes=notes,
    )
    write_truth(truth, images / f"{BASELINE_IMAGE.removesuffix('.img')}{TRUTH_SUFFIX}")

    counts: dict[str, int] = {}
    for obj in truth.objects:
        counts[obj.status] = counts.get(obj.status, 0) + 1
    return {
        "image": str(image),
        "image_sha256": sha256_file(image),
        "image_bytes": image.stat().st_size,
        "truth_counts": counts,
        "objects": len(truth.objects),
        "seed": seed,
        "host": _host_facts(),
    }


def backup_paths(work: Path) -> tuple[Path, Path]:
    """The backup image and its provenance record, which always travel together."""
    out = work / "backup"
    return out / BACKUP_NAME, out / BACKUP_META


def backup_region(
    device: str,
    work: Path,
    *,
    expect_serial: str,
    allow_fixed: bool = False,
) -> dict[str, Any]:
    """Copy the region ``write_image`` will overwrite, before it does.

    Only the region that will be modified is copied, because that is exactly
    what a byte-for-byte restore needs: nothing else on the device is touched,
    so nothing else can be lost. Restoring is the same copy in reverse.

    The length is **measured from the image the write will copy**, through the
    same :func:`write_extent` the write gate uses. It is not a default, not a
    constant and not a command-line number: a backup sized from one source of
    truth while the write is sized from another is the failure this function
    exists to make impossible.

    Alongside the copy it writes a provenance record naming the device, the
    serial, the byte count and the extent it was taken for. The write gate reads
    that record back, so a backup of the wrong device, or of the right device
    before the image grew, is refused rather than believed.
    """
    facts = preflight(device, expect_serial=expect_serial, allow_fixed=allow_fixed)
    image = work / "images" / BASELINE_IMAGE
    extent = write_extent(image, sector_bytes=logical_sector_bytes(device))
    if not extent["image_present"]:
        raise Refused(
            f"{image} has not been built, so there is no write extent to back up"
        )
    length = int(extent["modified_end_bytes"])
    if length > facts["size_bytes"]:
        raise Refused(
            f"the {length}-byte write extent runs past the end of "
            f"{device} ({facts['size_bytes']} bytes)"
        )

    target, meta = backup_paths(work)
    # The device is opened before anything is created on host storage, so a
    # refusal here leaves no empty backup file behind to be mistaken for one.
    try:
        source = open(device, "rb")
    except PermissionError as denied:
        raise PrivilegeRefused(
            f"{device} passed preflight, but this account cannot open it for "
            f"reading ({denied.strerror}). Reading a raw block device needs "
            "operating-system privilege this process does not hold, and this "
            "script does not obtain it. Nothing was read and nothing was written."
        ) from denied
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    read = 0
    digest = hashlib.sha256()
    with source, open(target, "wb") as sink:
        while read < length:
            chunk = source.read(min(4 * MIB, length - read))
            if not chunk:
                break
            sink.write(chunk)
            digest.update(chunk)
            read += len(chunk)
        sink.flush()
        os.fsync(sink.fileno())
    elapsed = time.monotonic() - started
    if read < length:
        raise Refused(
            f"{device} ended after {read} bytes, short of the {length}-byte "
            "write extent. The partial copy was kept but no provenance record "
            "was written, so the write gate will refuse it."
        )

    record = {
        "device": device,
        "kernel_name": facts["kernel_name"],
        "serial": facts["serial"],
        "serial_check": facts["serial_check"],
        "bytes": read,
        "sha256": digest.hexdigest(),
        "image": str(image),
        "image_sha256": sha256_file(image),
        "write_extent": extent,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": _host_facts(),
    }
    meta.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {
        "backup": str(target),
        "metadata": str(meta),
        "bytes": read,
        "sha256": digest.hexdigest(),
        "serial": facts["serial"],
        "write_extent": extent,
        "seconds": round(elapsed, 2),
        "mib_per_sec": round(read / MIB / elapsed, 2) if elapsed else None,
        "restore_command": (
            f"sudo dd if={target} of={device} bs=4M conv=fsync status=progress"
        ),
    }


#: The floor the kernel writes at. A buffered write to a block device dirties
#: whole blocks, so writing N bytes at offset 0 rewrites every block the range
#: [0, N) touches, including the trailing one when N is not a multiple. 4096 is
#: used as the conservative floor even on a 512-byte device, because the block
#: device page cache never dirties less than the buffer granularity and this
#: side of the estimate can only be too safe.
SECTOR_FALLBACK = 512
WRITE_GRANULARITY_FLOOR = 4096

#: Where the backup lands. ``backup_region`` writes exactly this name, and the
#: verification below must look in exactly the same place rather than be told.
BACKUP_NAME = "sdb-head-backup.img"
BACKUP_META = "sdb-head-backup.meta.json"


def logical_sector_bytes(device: str) -> int:
    """The device's logical block size, from sysfs, or 512 if it cannot be read.

    Read from sysfs rather than ``lsblk`` so this adds nothing to the preflight
    probe, and falls back rather than refusing: a wrong guess here only makes
    the modified range estimate rounder, and 512 is the smallest granularity
    any of this can happen at, so the fallback never under-states the range
    once the 4096-byte floor below is applied.
    """
    name = Path(device).name
    try:
        return int(Path(f"/sys/block/{name}/queue/logical_block_size").read_text())
    except (OSError, ValueError):
        return SECTOR_FALLBACK


def write_extent(image: Path, *, sector_bytes: int) -> dict[str, Any]:
    """Every byte of the device ``write`` can modify, measured, not declared.

    ``write_image`` copies the image file from offset 0 until the file ends, so
    the extent is the image's **actual size on disk**, not :data:`MEDIA_SIZE`.
    The two are expected to be equal - the builder truncates to that size before
    mkfs - but a backup sized from the constant while the write is sized from
    the file is exactly the class of mistake this function exists to remove.

    The reported end is rounded up to the write granularity, because the last
    partial block is read, modified and written back whole. Those trailing bytes
    come back unchanged, but they are rewritten, so a backup that stops short of
    the block boundary cannot restore the block.
    """
    granularity = max(sector_bytes, WRITE_GRANULARITY_FLOOR)
    if not image.is_file():
        return {
            "image": str(image),
            "image_present": False,
            "offset": 0,
            "length_bytes": None,
            "granularity_bytes": granularity,
            "sector_bytes": sector_bytes,
            "modified_end_bytes": None,
            "declared_media_size": MEDIA_SIZE,
            "matches_declared_size": None,
        }
    length = image.stat().st_size
    blocks = (length + granularity - 1) // granularity
    return {
        "image": str(image),
        "image_present": True,
        "offset": 0,
        "length_bytes": length,
        "granularity_bytes": granularity,
        "sector_bytes": sector_bytes,
        "modified_end_bytes": blocks * granularity,
        "declared_media_size": MEDIA_SIZE,
        "matches_declared_size": length == MEDIA_SIZE,
    }


def _backing_source(path: Path) -> str:
    """The block device the filesystem holding ``path`` sits on, or "".

    The backup usually does not exist yet when this is asked, and neither does
    the directory it will be created in, so the nearest existing ancestor is
    probed instead: that is the filesystem the file will land on.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        out = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", str(probe)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:  # pragma: no cover - findmnt absent, non-Linux host
        return ""
    return out.splitlines()[0] if out else ""


def _disk_of(source: str) -> str:
    """The whole-disk kernel name behind a filesystem source, e.g. nvme0n1."""
    if not source.startswith("/dev/"):
        return ""
    try:
        parent = subprocess.run(
            ["lsblk", "-no", "PKNAME", source],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:  # pragma: no cover - non-Linux host
        return ""
    lines = [line for line in parent.splitlines() if line.strip()]
    return lines[0].strip() if lines else Path(source).name


def backup_location(backup: Path, device: str, node: dict[str, Any]) -> dict[str, Any]:
    """Refuse the case where the backup is stored on the device it protects.

    A backup written onto the target is destroyed by the write it exists to
    undo. The preflight already refuses a device with any mounted filesystem,
    which makes this hard to reach, but the two commands are separate: a device
    can be mounted after the preflight and before the backup, and nothing else
    in this script would notice.
    """
    if backup.is_block_device():
        return {
            "backup_source": str(backup),
            "backup_disk": Path(backup).name,
            "target_disk": str(node.get("name") or ""),
            "on_host_storage": False,
            "reason": "the backup path is a block device, not a file on host storage",
        }
    source = _backing_source(backup)
    disk = _disk_of(source) if source else ""
    target = str(node.get("name") or "")
    kin = {target, *(str(c.get("name") or "") for c in (node.get("children") or []))}
    kin.discard("")
    on_target = bool(disk and disk in kin) or bool(
        source and Path(source).name in kin
    )
    if not source:
        return {
            "backup_source": "",
            "backup_disk": "",
            "target_disk": target,
            "on_host_storage": None,
            "reason": "the filesystem holding the backup could not be identified",
        }
    return {
        "backup_source": source,
        "backup_disk": disk,
        "target_disk": target,
        "on_host_storage": not on_target,
        "reason": (
            f"the backup is stored on {disk or source}, which is the target device"
            if on_target
            else f"the backup is on {disk or source}, a different disk from {target}"
        ),
    }


def read_backup_record(work: Path) -> dict[str, Any] | None:
    """The provenance record ``backup_region`` left, or None if there is none.

    A record that cannot be parsed is treated as a record that is not there.
    The caller refuses either way, and a half-read JSON file is not evidence of
    anything.
    """
    _, meta = backup_paths(work)
    try:
        loaded: Any = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def verify_backup(
    device: str,
    work: Path,
    *,
    expect_serial: str,
    allow_fixed: bool = False,
) -> dict[str, Any]:
    """Read-only. Does the captured backup cover what the write would modify?

    Nothing here opens the device for writing, and nothing here restores. It
    answers one question - is every byte the write can touch present in the
    backup file - and says plainly that a backup which has never been restored
    is not a proven restore.
    """
    node = _lsblk(device)
    serial = str(node.get("serial") or "").strip()
    try:
        safety: dict[str, Any] = preflight(
            device, expect_serial=expect_serial, allow_fixed=allow_fixed
        )
    except Refused as refusal:
        safety = {"verdict": "REFUSED", "reason": str(refusal)}

    image = work / "images" / BASELINE_IMAGE
    extent = write_extent(image, sector_bytes=logical_sector_bytes(device))
    backup, meta = backup_paths(work)
    present = backup.is_file()
    backup_bytes = backup.stat().st_size if present else 0
    location = backup_location(backup, device, node)
    record = read_backup_record(work)

    end = extent["modified_end_bytes"]
    covers = bool(present and end is not None and backup_bytes >= end)
    shortfall = (
        max(0, end - backup_bytes) if present and end is not None else None
    )
    reasons: list[str] = []
    if not extent["image_present"]:
        reasons.append(
            f"{image} has not been built, so the write extent is unknown"
        )
    if not present:
        reasons.append(f"{backup} does not exist")
    elif not covers:
        reasons.append(
            f"the backup is {backup_bytes} bytes and the write modifies "
            f"{end} bytes: {shortfall} bytes short"
        )
    if location["on_host_storage"] is False:
        reasons.append(location["reason"])
    if location["on_host_storage"] is None:
        reasons.append(location["reason"])
    if safety.get("verdict") != "SAFE":
        reasons.append(f"preflight refused: {safety.get('reason')}")

    # Provenance. A backup is only a backup of *this* device, at *this* extent:
    # the right number of bytes taken from the wrong stick restores nothing.
    digest: str | None = None
    if present:
        try:
            digest = sha256_file(backup)
        except OSError as failure:
            reasons.append(f"the backup could not be hashed: {failure}")
    if present and record is None:
        reasons.append(
            f"{meta} is missing or unreadable, so the backup's origin is unknown"
        )
    elif present and record is not None:
        recorded_extent = record.get("write_extent") or {}
        if str(record.get("serial") or "") != serial:
            reasons.append(
                f"the backup was taken from serial {record.get('serial')!r}, "
                f"the device now reports {serial!r}"
            )
        if str(record.get("device") or "") != device:
            reasons.append(
                f"the backup was taken from {record.get('device')!r}, not {device!r}"
            )
        if recorded_extent.get("modified_end_bytes") != end:
            reasons.append(
                "the backup was taken for a "
                f"{recorded_extent.get('modified_end_bytes')}-byte write extent, "
                f"and the write now modifies {end} bytes"
            )
        if record.get("bytes") != backup_bytes:
            reasons.append(
                f"the backup is {backup_bytes} bytes and its record claims "
                f"{record.get('bytes')}"
            )
        if digest is not None and record.get("sha256") != digest:
            reasons.append(
                "the backup's contents no longer match the hash recorded when "
                "it was taken"
            )

    sufficient = covers and location["on_host_storage"] is True and not reasons
    return {
        "device": device,
        "serial": serial,
        "expect_serial": expect_serial,
        "serial_matches": serial == expect_serial,
        "device_size_bytes": int(node.get("size") or 0),
        "safety": safety,
        "backup": str(backup),
        "backup_metadata": str(meta),
        "backup_record": record,
        "backup_serial": str(record.get("serial") or "") if record else None,
        "backup_device": str(record.get("device") or "") if record else None,
        "backup_present": present,
        "backup_bytes": backup_bytes,
        "backup_sha256": digest,
        "backup_covers_range": [0, backup_bytes] if present else None,
        "expected_write_range": (
            [extent["offset"], end] if end is not None else None
        ),
        "write_extent": extent,
        "location": location,
        "sufficient_for_restoring_the_modified_region": sufficient,
        "blocking": reasons,
        "restoration": (
            "backup captured; restoration not validated"
            if present
            else "no backup captured"
        ),
        "restore_command": (
            f"sudo dd if={backup} of={device} bs=4M conv=fsync status=progress"
            if present
            else None
        ),
        "host": _host_facts(),
    }


def prewrite_plan(
    device: str,
    work: Path,
    *,
    expect_serial: str,
    allow_fixed: bool = False,
) -> dict[str, Any]:
    """Read-only. Everything a human needs to approve or refuse the write.

    This command never writes. It assembles identity, safety, extent, backup
    coverage and the exact command, and its verdict is always ``REVIEW ONLY``:
    approval is a human act performed by running ``write`` afterwards, and
    nothing in this script can perform it on the operator's behalf.
    """
    node = _lsblk(device)
    checked = verify_backup(
        device, work, expect_serial=expect_serial, allow_fixed=allow_fixed
    )
    extent = checked["write_extent"]
    partitions = [
        {
            "name": child.get("name"),
            "fstype": child.get("fstype"),
            "label": child.get("label"),
            "size_bytes": child.get("size"),
            "mountpoints": [m for m in (child.get("mountpoints") or []) if m],
        }
        for child in (node.get("children") or [])
    ]
    mounted = _mountpoints(node)
    identity = serial_cross_check(node)
    blocking = list(checked["blocking"])
    if identity["status"] != "AGREE":
        blocking.append(
            f"the serial is {identity['status']} between lsblk and sysfs, and the "
            "write refuses anything but agreement"
        )
    privilege = device_privilege(device)
    command = [
        "sudo python3 scripts/media_benchmark.py write",
        f"  --device {device}",
        f"  --work {work}",
        f"  --expect-serial {expect_serial}",
        "  --confirm-serial <type the device serial yourself>",
        *(["  --allow-fixed"] if allow_fixed else []),
        "  --i-understand-this-destroys-data",
    ]
    return {
        "verdict": "REVIEW ONLY - no write performed",
        "approved": False,
        "device": device,
        "model": str(node.get("model") or "").strip(),
        "transport": node.get("tran"),
        "removable": bool(node.get("rm")),
        "read_only_flag": bool(node.get("ro")),
        "expect_serial": expect_serial,
        "current_serial": checked["serial"],
        "serial_matches": checked["serial_matches"],
        "serial_check": identity,
        # Two machine sources agreeing is not a person checking the label.
        "human_confirmation": (
            "required - the operator types the serial into --confirm-serial; "
            "this plan has not performed or recorded it"
        ),
        "privilege": privilege,
        "capacity_bytes": checked["device_size_bytes"],
        "partitions": partitions,
        "mountpoints": mounted,
        "unmounted": not mounted,
        "safety": checked["safety"],
        "write_offset": extent["offset"],
        "write_length_bytes": extent["length_bytes"],
        "write_modified_end_bytes": extent["modified_end_bytes"],
        "write_granularity_bytes": extent["granularity_bytes"],
        "image": extent["image"],
        "image_present": extent["image_present"],
        "image_sha256": (
            sha256_file(Path(extent["image"])) if extent["image_present"] else None
        ),
        "backup": checked["backup"],
        "backup_metadata": checked["backup_metadata"],
        "backup_present": checked["backup_present"],
        "backup_bytes": checked["backup_bytes"],
        "backup_sha256": checked["backup_sha256"],
        "backup_device": checked["backup_device"],
        "backup_serial": checked["backup_serial"],
        "backup_serial_matches": (
            checked["backup_serial"] == checked["serial"]
            if checked["backup_serial"]
            else None
        ),
        "backup_sufficient": checked["sufficient_for_restoring_the_modified_region"],
        "backup_location": checked["location"],
        "restoration": checked["restoration"],
        "blocking": blocking,
        "metadata_effect": {
            "partition_table": (
                "yes - the write starts at LBA 0 and replaces the partition "
                "table or boot sector with the image's FAT32 boot sector"
            ),
            "filesystem_metadata": (
                "yes - every filesystem structure inside the written range is "
                "replaced by the corpus image's own"
            ),
            "beyond_the_written_range": (
                "untouched, including a GPT secondary header at the end of the "
                "device, which the image does not reach and the backup does not "
                "cover"
            ),
        },
        "command_after_approval": "\\\n".join(command),
        "host": _host_facts(),
    }


def render_plan(plan: dict[str, Any]) -> str:
    """The plan as text, for a human to read before deciding anything."""
    identity = plan["serial_check"]
    agrees = {"AGREE": "true", "DISAGREE": "false"}.get(
        identity["status"], "UNVERIFIED"
    )
    if not plan["backup_present"]:
        backup_status = "absent"
    elif plan["backup_sufficient"]:
        backup_status = "covers the write extent"
    else:
        backup_status = "INSUFFICIENT"
    lines = [
        "PRE-WRITE PLAN - REVIEW ONLY. NOTHING HAS BEEN WRITTEN.",
        "",
        "SUMMARY",
        f"  DEVICE                  {plan['device']}",
        f"  CURRENT SERIAL SOURCE A {identity['source_a']['value']}"
        f"  ({identity['source_a']['name']})",
        f"  CURRENT SERIAL SOURCE B {identity['source_b']['value']}"
        f"  ({identity['source_b']['name']})",
        f"  SERIAL AGREES           {agrees}",
        f"  EXPECTED SERIAL         {plan['expect_serial']}",
        f"  HUMAN CONFIRMATION      {plan['human_confirmation']}",
        "  MOUNT STATE             "
        + (", ".join(plan["mountpoints"]) if plan["mountpoints"] else "unmounted"),
        f"  PREFLIGHT               {plan['safety'].get('verdict')}",
        f"  IMAGE                   {plan['image']}",
        f"  IMAGE SHA-256           {plan['image_sha256']}",
        f"  WRITE EXTENT            [{plan['write_offset']}, "
        f"{plan['write_modified_end_bytes']}) - {plan['write_length_bytes']} bytes "
        f"at {plan['write_granularity_bytes']}-byte granularity",
        f"  BACKUP PATH             {plan['backup']}",
        "  BACKUP EXTENT           "
        + (f"[0, {plan['backup_bytes']})" if plan["backup_present"] else "none"),
        f"  BACKUP STATUS           {backup_status}",
        f"  BACKUP LOCATION         {plan['backup_location']['reason']}",
        f"  PRIVILEGE STATUS        {plan['privilege']['status']}"
        f" (read={plan['privilege']['read']}, write={plan['privilege']['write']})",
        f"  APPROVED: {str(plan['approved']).lower()}",
        "  VERDICT: REVIEW ONLY",
        "",
        "DEVICE",
        f"  path               {plan['device']}",
        f"  model              {plan['model']}",
        f"  transport          {plan['transport']}",
        f"  removable          {plan['removable']}",
        f"  read-only flag     {plan['read_only_flag']}",
        f"  capacity           {plan['capacity_bytes']} bytes",
        f"  expected serial    {plan['expect_serial']}",
        f"  current serial     {plan['current_serial']}",
        f"  serial matches     {plan['serial_matches']}",
        "",
        "PARTITIONS AND MOUNTS",
    ]
    if plan["partitions"]:
        for part in plan["partitions"]:
            lines.append(
                f"  {part['name']}  {part['fstype']}  {part['size_bytes']} bytes"
                f"  mounts={part['mountpoints'] or 'none'}"
            )
    else:
        lines.append("  no partitions reported")
    lines += [
        f"  mounted            {plan['mountpoints'] or 'nothing mounted'}",
        f"  unmounted          {plan['unmounted']}",
        f"  preflight          {plan['safety'].get('verdict')}"
        + (
            f" - {plan['safety'].get('reason')}"
            if plan["safety"].get("verdict") != "SAFE"
            else ""
        ),
        "",
        "WRITE",
        f"  source image       {plan['image']}",
        f"  image present      {plan['image_present']}",
        f"  image sha256       {plan['image_sha256']}",
        f"  offset             {plan['write_offset']}",
        f"  length             {plan['write_length_bytes']} bytes",
        f"  blocks modified    [0, {plan['write_modified_end_bytes']}) at "
        f"{plan['write_granularity_bytes']}-byte granularity",
        f"  partition table    {plan['metadata_effect']['partition_table']}",
        f"  filesystem meta    {plan['metadata_effect']['filesystem_metadata']}",
        f"  outside the range  {plan['metadata_effect']['beyond_the_written_range']}",
        "",
        "BACKUP",
        f"  path               {plan['backup']}",
        f"  provenance record  {plan['backup_metadata']}",
        "  size               "
        + (f"{plan['backup_bytes']} bytes" if plan["backup_present"] else "absent"),
        f"  sha256             {plan['backup_sha256']}",
        f"  backup device      {plan['backup_device']}",
        f"  backup serial      {plan['backup_serial']}"
        + (
            f"  (device reports {plan['current_serial']})"
            if plan["backup_serial_matches"] is False
            else ""
        ),
        f"  location           {plan['backup_location']['reason']}",
        f"  coverage status    {plan['backup_sufficient']}",
        f"  restore status     {plan['restoration']}",
        "",
        "BLOCKING",
    ]
    if plan["blocking"]:
        lines += [f"  - {reason}" for reason in plan["blocking"]]
    else:
        lines.append("  none - every checked condition is satisfied")
    lines += [
        "",
        "COMMAND, ONLY AFTER A HUMAN APPROVES THIS PLAN",
        *(f"  {line}" for line in plan["command_after_approval"].split("\n")),
        "",
        f"APPROVED  {str(plan['approved']).lower()}",
        f"VERDICT   {plan['verdict']}",
    ]
    return "\n".join(lines)


def write_image(
    device: str,
    image: Path,
    work: Path,
    *,
    expect_serial: str,
    confirm_serial: str,
    acknowledged: bool,
    allow_fixed: bool = False,
) -> dict[str, Any]:
    """**The one destructive step.** Every gate, and none of them optional.

    The preflight runs again here rather than trusting an earlier one: the
    device may have been replugged, remounted or swapped since. Then the
    operator's typed serial is checked against what *this* run of the preflight
    read from the device, and the acknowledgement flag must be present.

    The last gate is the backup, and it is the reason :func:`verify_backup` is
    not a report the operator is trusted to read. This function runs that same
    verification itself, against the device it is about to write to, and
    refuses unless the backup exists, lies on other storage, was taken from
    this serial, was taken for this exact extent, still hashes to what its
    record says, and covers every byte the copy below can reach. The first
    write happens after the last check, never between two of them.
    """
    facts = preflight(
        device, expect_serial=expect_serial, allow_fixed=allow_fixed
    )
    # Preflight already refuses a disagreement. An independent source that
    # could not be read is not a disagreement, but it is not a confirmation
    # either, and the destructive step does not proceed on one source alone.
    if facts["serial_check"]["status"] != "AGREE":
        raise Refused(
            f"the serial is {facts['serial_check']['status']}: only "
            f"{facts['serial_check']['source_a']['name']} could be read, so the "
            "device's identity has no independent confirmation. Nothing was "
            "written."
        )
    if not acknowledged:
        raise Refused(
            "the destructive acknowledgement flag was not passed. Nothing was "
            "written."
        )
    typed = confirm_serial.strip()
    if typed != facts["serial"]:
        raise Refused(
            f"typed serial {typed!r} does not match the device's "
            f"{facts['serial']!r}. Nothing was written."
        )
    if not image.is_file():
        raise Refused(f"{image} is not a file")

    length = image.stat().st_size
    if length > facts["size_bytes"]:
        raise Refused("the image is larger than the device")

    checked = verify_backup(
        device, work, expect_serial=expect_serial, allow_fixed=allow_fixed
    )
    # The verification measures the extent of the image in the work directory.
    # If that is not the image about to be copied, it verified something else.
    if Path(checked["write_extent"]["image"]).resolve() != image.resolve():
        raise Refused(
            f"the backup was verified against {checked['write_extent']['image']}, "
            f"not {image}. Nothing was written."
        )
    if checked["write_extent"]["length_bytes"] != length:
        raise Refused(
            "the image changed size between the backup check and the write. "
            "Nothing was written."
        )
    if not checked["sufficient_for_restoring_the_modified_region"]:
        raise Refused(
            "the backup does not cover this write: "
            + "; ".join(checked["blocking"])
            + ". Nothing was written."
        )

    started = time.monotonic()
    written = 0
    with open(image, "rb") as source, open(device, "wb") as sink:
        while chunk := source.read(4 * MIB):
            sink.write(chunk)
            written += len(chunk)
        sink.flush()
        os.fsync(sink.fileno())
    elapsed = time.monotonic() - started

    # Read the region straight back. This is not a sanitization verification;
    # it establishes only that the bytes the benchmark will score are the bytes
    # the builder produced, so a transport fault cannot be mistaken later for a
    # recovery failure.
    digest = hashlib.sha256()
    with open(device, "rb") as handle:
        remaining = written
        while remaining > 0:
            chunk = handle.read(min(4 * MIB, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)

    return {
        "device": device,
        "serial": facts["serial"],
        "image": str(image),
        "image_sha256": sha256_file(image),
        "backup": checked["backup"],
        "backup_sha256": checked["backup_sha256"],
        "backup_bytes": checked["backup_bytes"],
        "write_extent": checked["write_extent"],
        "bytes_written": written,
        "readback_sha256": digest.hexdigest(),
        "readback_matches": digest.hexdigest() == sha256_file(image),
        "seconds": round(elapsed, 2),
        "mib_per_sec": round(written / MIB / elapsed, 2) if elapsed else None,
        "host": _host_facts(),
    }


def _hand_back(path: Path) -> None:
    """Give a file written under sudo back to the invoking account.

    Everything after acquisition is unprivileged, and a root-owned image would
    make the scoring step need a privilege it has no business holding.
    """
    uid = os.environ.get("SUDO_UID")
    gid = os.environ.get("SUDO_GID")
    if uid and gid:
        try:
            os.chown(path, int(uid), int(gid))
        except OSError:  # pragma: no cover - best effort, never fatal
            pass


def acquire(device: str, work: Path, *, length: int) -> dict[str, Any]:
    """Read the device back, write-blocked, recording throughput and errors."""
    from core.carve.acquire import apply_write_block

    out = work / "acquired"
    out.mkdir(parents=True, exist_ok=True)
    target = out / "acquired.img"

    block = apply_write_block(device)
    started = time.monotonic()
    read = 0
    errors: list[dict[str, Any]] = []
    with open(device, "rb") as source, open(target, "wb") as sink:
        while read < length:
            want = min(4 * MIB, length - read)
            try:
                chunk = source.read(want)
            except OSError as failure:  # pragma: no cover - needs a bad sector
                errors.append({"offset": read, "error": str(failure)})
                sink.write(b"\0" * want)
                read += want
                source.seek(read)
                continue
            if not chunk:
                break
            sink.write(chunk)
            read += len(chunk)
        sink.flush()
        os.fsync(sink.fileno())
    elapsed = time.monotonic() - started
    _hand_back(target)

    return {
        "device": device,
        "acquired": str(target),
        "bytes": read,
        "sha256": sha256_file(target),
        "seconds": round(elapsed, 2),
        "mib_per_sec": round(read / MIB / elapsed, 2) if elapsed else None,
        "read_errors": errors,
        "write_block": {
            "applied": bool(getattr(block, "applied", False)),
            "verified_by": getattr(block, "verified_by", ""),
            "detail": getattr(block, "detail", ""),
        },
    }


def baseline_row() -> dict[str, Any]:
    """The pre-registered synthetic reference, read from the checked-in CSV."""
    with open(BASELINE_CSV, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["image"] == BASELINE_IMAGE and row["tool"] == BASELINE_TOOL:
                full = int(row["full"])
                exact = int(row["exact"])
                return {
                    "image": BASELINE_IMAGE,
                    "tool": BASELINE_TOOL,
                    "full": full,
                    "exact": exact,
                    "recall_pct": round(100.0 * exact / full, 2) if full else None,
                    "corrupt": int(row["corrupt"]),
                    "missed": int(row["missed"]),
                    "fp_total": int(row["fp_total"]),
                    "seconds": float(row["seconds"]),
                }
    raise Refused(f"no baseline row for {BASELINE_IMAGE}/{BASELINE_TOOL}")


def score_physical(work: Path, *, timeout: int = 3600) -> dict[str, Any]:
    """Ground truth from the acquired image, one carve run, and the verdict.

    The truth is recomputed **from the acquired image**, not carried over from
    the build: what the benchmark must score is what came back off the medium,
    and any difference between the two is itself a finding.
    """
    from testkit.benchmark import (
        OUTPUT_INDEX,
        TRUTH_SUFFIX,
        run_tool,
        score_run,
    )
    from testkit.damage import load_truth, write_truth

    acquired = work / "acquired" / "acquired.img"
    if not acquired.is_file():
        raise Refused(f"{acquired} is missing; run the acquire step first")

    built_truth = load_truth(
        work / "images" / f"{BASELINE_IMAGE.removesuffix('.img')}{TRUTH_SUFFIX}"
    )

    # Recompute the truth against the acquired bytes, using the same plant set.
    physical = work / "physical"
    images = physical / "images"
    images.mkdir(parents=True, exist_ok=True)
    stem = "media-fat32-255m-physical"
    target = images / f"{stem}.img"
    if not target.exists():
        target.symlink_to(acquired)

    # replace(), not Truth(**asdict(...)): asdict recurses, so the nested
    # TruthObjects would come back as plain dicts and the scorer would read
    # attributes off them.
    truth = replace(built_truth, image=f"{stem}.img")
    write_truth(truth, images / f"{stem}{TRUTH_SUFFIX}")

    run_dir = physical / "runs" / stem / BASELINE_TOOL
    meta = run_tool(
        BASELINE_TOOL,
        target,
        run_dir,
        foremost="foremost",
        timeout=timeout,
        keep_outputs=True,
    )
    index = run_dir / OUTPUT_INDEX
    result = score_run(
        truth,
        index if index.exists() else run_dir,
        work / "payloads",
        tool=BASELINE_TOOL,
        image_path=target,
    )

    counts: dict[str, int] = {}
    for obj in truth.objects:
        counts[obj.status] = counts.get(obj.status, 0) + 1

    measured = asdict(result)
    full = int(measured.get("full") or 0)
    exact = int(measured.get("exact") or 0)
    recall = round(100.0 * exact / full, 2) if full else None
    base = baseline_row()
    delta = (
        round(recall - base["recall_pct"], 2)
        if recall is not None and base["recall_pct"] is not None
        else None
    )
    return {
        "acquired_sha256": sha256_file(acquired),
        "truth_counts": counts,
        "measured": measured,
        "recall_pct": recall,
        "baseline": base,
        "recall_delta_points": delta,
        "run": meta,
        "host": _host_facts(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight", help="identity and safety; writes nothing")
    pre.add_argument("--device", required=True)
    pre.add_argument("--expect-serial", required=True)
    pre.add_argument("--allow-fixed", action="store_true")

    bld = sub.add_parser("build", help="build the corpus image on host storage")
    bld.add_argument("--work", required=True, type=Path)
    bld.add_argument("--seed", type=int, default=0)

    bak = sub.add_parser("backup", help="copy the region the write will overwrite")
    bak.add_argument("--device", required=True)
    bak.add_argument("--work", required=True, type=Path)
    bak.add_argument("--expect-serial", required=True)
    bak.add_argument("--allow-fixed", action="store_true")

    ver = sub.add_parser(
        "verify-backup", help="read-only: does the backup cover the write?"
    )
    ver.add_argument("--device", required=True)
    ver.add_argument("--work", required=True, type=Path)
    ver.add_argument("--expect-serial", required=True)
    ver.add_argument("--allow-fixed", action="store_true")

    pln = sub.add_parser("plan", help="read-only: the pre-write plan, for review")
    pln.add_argument("--device", required=True)
    pln.add_argument("--work", required=True, type=Path)
    pln.add_argument("--expect-serial", required=True)
    pln.add_argument("--allow-fixed", action="store_true")
    pln.add_argument(
        "--json", action="store_true", help="emit the plan as JSON instead of text"
    )

    wr = sub.add_parser("write", help="DESTRUCTIVE: copy the image onto the device")
    wr.add_argument("--device", required=True)
    wr.add_argument("--work", required=True, type=Path)
    wr.add_argument("--expect-serial", required=True)
    wr.add_argument("--confirm-serial", required=True)
    wr.add_argument("--allow-fixed", action="store_true")
    wr.add_argument("--i-understand-this-destroys-data", action="store_true")

    acq = sub.add_parser("acquire", help="read the device back, write-blocked")
    acq.add_argument("--device", required=True)
    acq.add_argument("--work", required=True, type=Path)
    acq.add_argument("--bytes", type=int, default=MEDIA_SIZE)

    sc = sub.add_parser("score", help="truth, one carve run, and the verdict")
    sc.add_argument("--work", required=True, type=Path)
    sc.add_argument("--timeout", type=int, default=3600)

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            payload = preflight(
                args.device,
                expect_serial=args.expect_serial,
                allow_fixed=args.allow_fixed,
            )
        elif args.command == "build":
            payload = build_corpus(args.work, seed=args.seed)
        elif args.command == "backup":
            payload = backup_region(
                args.device,
                args.work,
                expect_serial=args.expect_serial,
                allow_fixed=args.allow_fixed,
            )
        elif args.command == "verify-backup":
            payload = verify_backup(
                args.device,
                args.work,
                expect_serial=args.expect_serial,
                allow_fixed=args.allow_fixed,
            )
        elif args.command == "plan":
            payload = prewrite_plan(
                args.device,
                args.work,
                expect_serial=args.expect_serial,
                allow_fixed=args.allow_fixed,
            )
            if not args.json:
                print(render_plan(payload))  # noqa: T201
                return 0
        elif args.command == "write":
            payload = write_image(
                args.device,
                args.work / "images" / BASELINE_IMAGE,
                args.work,
                expect_serial=args.expect_serial,
                confirm_serial=args.confirm_serial,
                acknowledged=args.i_understand_this_destroys_data,
                allow_fixed=args.allow_fixed,
            )
        elif args.command == "acquire":
            payload = acquire(args.device, args.work, length=args.bytes)
        elif args.command == "score":
            payload = score_physical(args.work, timeout=args.timeout)
        else:  # pragma: no cover - argparse rejects anything else
            raise Refused(f"unknown command {args.command}")
    except Refused as refusal:
        print(  # noqa: T201
            json.dumps({"refused": str(refusal), "kind": refusal.kind}, indent=1)
        )
        return 2
    print(json.dumps(payload, indent=1, default=str))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
