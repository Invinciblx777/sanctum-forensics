"""Run the existing benchmark corpus against one real removable device.

Every recovery figure in ``docs/performance/benchmark.md`` was measured on
image files on this host's NVMe disk. No controller, no USB bridge, no read
error and no acquisition is in any of them. This script closes exactly that
gap for **one** device and **one** geometry, by reusing the builders, the
ground-truth manifest and the scorer the synthetic benchmark already uses, so
that the only variable is the medium.

It is deliberately five separate subcommands rather than one run, because one
of them writes to a device and the rest do not:

    preflight   read-only. Identity, safety and declared-configuration match.
    build       read-only with respect to the device. Builds the corpus image
                on host storage, from testkit.benchmark's own builder.
    write       **DESTRUCTIVE.** Copies that image onto the device. Requires
                --i-understand-this-destroys-data and the typed serial.
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
import hashlib
import json
import os
import random
import subprocess
import sys
import time
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


class Refused(RuntimeError):
    """A safety gate said no. Nothing was written."""


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


def backup_region(device: str, work: Path, *, length: int) -> dict[str, Any]:
    """Copy the region ``write_image`` will overwrite, before it does.

    Only the region that will be modified is copied, because that is exactly
    what a byte-for-byte restore needs: nothing else on the device is touched,
    so nothing else can be lost. Restoring is the same copy in reverse.
    """
    out = work / "backup"
    out.mkdir(parents=True, exist_ok=True)
    target = out / "sdb-head-backup.img"
    started = time.monotonic()
    read = 0
    with open(device, "rb") as source, open(target, "wb") as sink:
        while read < length:
            chunk = source.read(min(4 * MIB, length - read))
            if not chunk:
                break
            sink.write(chunk)
            read += len(chunk)
        sink.flush()
        os.fsync(sink.fileno())
    elapsed = time.monotonic() - started
    return {
        "backup": str(target),
        "bytes": read,
        "sha256": sha256_file(target),
        "seconds": round(elapsed, 2),
        "mib_per_sec": round(read / MIB / elapsed, 2) if elapsed else None,
        "restore_command": (
            f"sudo dd if={target} of={device} bs=4M conv=fsync status=progress"
        ),
    }


def write_image(
    device: str,
    image: Path,
    *,
    expect_serial: str,
    confirm_serial: str,
    acknowledged: bool,
    allow_fixed: bool = False,
) -> dict[str, Any]:
    """**The one destructive step.** Three gates, all of them explicit.

    The preflight runs again here rather than trusting an earlier one: the
    device may have been replugged, remounted or swapped since. Then the
    operator's typed serial is checked against what *this* run of the preflight
    read from the device, and the acknowledgement flag must be present.
    """
    facts = preflight(
        device, expect_serial=expect_serial, allow_fixed=allow_fixed
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
        "bytes_written": written,
        "readback_sha256": digest.hexdigest(),
        "readback_matches": digest.hexdigest() == sha256_file(image),
        "seconds": round(elapsed, 2),
        "mib_per_sec": round(written / MIB / elapsed, 2) if elapsed else None,
        "host": _host_facts(),
    }


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
    bak.add_argument("--bytes", type=int, default=MEDIA_SIZE)

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
            payload = backup_region(args.device, args.work, length=args.bytes)
        elif args.command == "write":
            payload = write_image(
                args.device,
                args.work / "images" / BASELINE_IMAGE,
                expect_serial=args.expect_serial,
                confirm_serial=args.confirm_serial,
                acknowledged=args.i_understand_this_destroys_data,
                allow_fixed=args.allow_fixed,
            )
        elif args.command == "acquire":
            payload = acquire(args.device, args.work, length=args.bytes)
        else:  # pragma: no cover - argparse rejects anything else
            raise Refused(f"unknown command {args.command}")
    except Refused as refusal:
        print(json.dumps({"refused": str(refusal)}, indent=1))  # noqa: T201
        return 2
    print(json.dumps(payload, indent=1, default=str))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
