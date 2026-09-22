"""What the write can reach, and whether the backup reaches as far.

The gates in ``test_media_benchmark_preflight`` answer *which device* may be
written to. These answer the other half: *which bytes*, and whether the copy
taken beforehand covers them. The difference matters because the two numbers
used to come from different places - the write sized from the built image file,
the backup sized from a command-line default - and nothing forced them to agree.

Every test drives a **file standing in for a device**, never a real one, and
every refusal test reads the stand-in back afterwards to prove its bytes are
untouched. ``_lsblk`` and the two storage-location probes are replaced, so the
only host the suite needs is a temporary directory.
"""

from __future__ import annotations

import builtins
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.media_benchmark import (
    BASELINE_IMAGE,
    MEDIA_SIZE,
    PrivilegeRefused,
    Refused,
    backup_paths,
    backup_region,
    logical_sector_bytes,
    main,
    prewrite_plan,
    read_backup_record,
    render_plan,
    verify_backup,
    write_extent,
    write_image,
)

SERIAL = "B103B9C19DE1CCC1BD535ACB"
SYSFS_SERIAL = "/sys/devices/pci0000:00/usb8/8-1/serial"
#: The stand-in device is filled with this, so "nothing was written" is a
#: statement about bytes rather than about the absence of an exception.
PATTERN = b"ORIGINAL"
DEVICE_BYTES = 256 * 1024


def _disk(**overrides: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": "sdb",
        "path": "/dev/sdb",
        "type": "disk",
        "size": 7759462400,
        "tran": "usb",
        "rm": True,
        "ro": False,
        "serial": SERIAL,
        "model": "TransMemory",
        "mountpoints": [None],
        "children": [],
    }
    node.update(overrides)
    return node


class Bench:
    """A work directory, and a file that every probe treats as the device."""

    def __init__(self, root: Path, device: Path) -> None:
        self.work = root
        self.device = device

    @property
    def path(self) -> str:
        return str(self.device)

    @property
    def image(self) -> Path:
        return self.work / "images" / BASELINE_IMAGE

    def build(self, size: int) -> Path:
        self.image.parent.mkdir(parents=True, exist_ok=True)
        with open(self.image, "wb") as handle:
            handle.truncate(size)
        return self.image

    def untouched(self) -> bool:
        expected = (PATTERN * (DEVICE_BYTES // len(PATTERN) + 1))[:DEVICE_BYTES]
        return self.device.read_bytes() == expected

    def capture(self) -> dict[str, Any]:
        """A real backup, taken the way the operator would take one."""
        return backup_region(self.path, self.work, expect_serial=SERIAL)

    def plant(self, *, backup_bytes: int, extent_end: int, **record: Any) -> None:
        """A backup of a stated size, with a record that matches it."""
        backup, meta = backup_paths(self.work)
        backup.parent.mkdir(parents=True, exist_ok=True)
        with open(backup, "wb") as handle:
            handle.truncate(backup_bytes)
        entry: dict[str, Any] = {
            "device": self.path,
            "kernel_name": "sdb",
            "serial": SERIAL,
            "bytes": backup_bytes,
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "image": str(self.image),
            "write_extent": {"offset": 0, "modified_end_bytes": extent_end},
        }
        entry.update(record)
        meta.write_text(json.dumps(entry), encoding="utf-8")

    def amend(self, **changes: Any) -> None:
        """Change the record without changing the backup it describes."""
        _, meta = backup_paths(self.work)
        entry = json.loads(meta.read_text(encoding="utf-8"))
        entry.update(changes)
        meta.write_text(json.dumps(entry), encoding="utf-8")


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace every probe that would otherwise read the real host."""
    real_exists = Path.exists
    real_block = Path.is_block_device
    device = tmp_path / "stand-in-device"
    device.write_bytes((PATTERN * (DEVICE_BYTES // len(PATTERN) + 1))[:DEVICE_BYTES])

    def install(
        node: dict[str, Any] | None = None,
        *,
        sector: int = 512,
        backup_disk: str = "nvme0n1",
        kernel: tuple[str | None, str | None] = (SERIAL, SYSFS_SERIAL),
    ) -> Bench:
        monkeypatch.setattr(
            "scripts.media_benchmark._lsblk", lambda dev: node or _disk()
        )
        monkeypatch.setattr(
            "scripts.media_benchmark._kernel_serial", lambda name, tran: kernel
        )
        monkeypatch.setattr("scripts.media_benchmark._root_disk", lambda: "nvme0n1")
        monkeypatch.setattr(
            "scripts.media_benchmark.logical_sector_bytes", lambda dev: sector
        )
        monkeypatch.setattr(
            "scripts.media_benchmark._backing_source",
            lambda path: f"/dev/{backup_disk}p2",
        )
        monkeypatch.setattr(
            "scripts.media_benchmark._disk_of", lambda source: backup_disk
        )
        monkeypatch.setattr(
            Path,
            "is_block_device",
            lambda self: str(self) == str(device) or real_block(self),
        )
        monkeypatch.setattr(
            Path, "exists", lambda self, *a, **k: real_exists(self, *a, **k)
        )
        return Bench(tmp_path / "work", device)

    return install


def _write(bench: Bench, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "expect_serial": SERIAL,
        "confirm_serial": SERIAL,
        "acknowledged": True,
    }
    kwargs.update(overrides)
    return write_image(bench.path, bench.image, bench.work, **kwargs)


# --------------------------------------------------------------- the extent


def test_the_extent_is_measured_from_the_image_not_the_constant(
    tmp_path: Path,
) -> None:
    """A 255 MiB constant must never stand in for the file that gets copied."""
    images = tmp_path / "images"
    images.mkdir()
    with open(images / BASELINE_IMAGE, "wb") as handle:
        handle.truncate(MEDIA_SIZE + 8 * 1024)
    extent = write_extent(images / BASELINE_IMAGE, sector_bytes=512)
    assert extent["length_bytes"] == MEDIA_SIZE + 8 * 1024
    assert extent["declared_media_size"] == MEDIA_SIZE
    assert extent["matches_declared_size"] is False


@pytest.mark.parametrize(
    ("image_bytes", "expected_end"),
    [
        (4096, 4096),  # exact boundary
        (4097, 8192),  # boundary + 1
        (1, 4096),  # a 4095-byte tail
        (8192, 8192),  # the next whole boundary
        (12288 + 4095, 16384),  # a 1-byte tail
    ],
)
def test_the_modified_range_rounds_to_whole_blocks(
    tmp_path: Path, image_bytes: int, expected_end: int
) -> None:
    """The tail block is rewritten whole, so the range ends on a boundary."""
    images = tmp_path / "images"
    images.mkdir()
    with open(images / BASELINE_IMAGE, "wb") as handle:
        handle.truncate(image_bytes)
    extent = write_extent(images / BASELINE_IMAGE, sector_bytes=512)
    assert extent["granularity_bytes"] == 4096
    assert extent["modified_end_bytes"] == expected_end


def test_a_4096_byte_device_rounds_to_its_own_blocks(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    with open(images / BASELINE_IMAGE, "wb") as handle:
        handle.truncate(4097)
    extent = write_extent(images / BASELINE_IMAGE, sector_bytes=4096)
    assert extent["granularity_bytes"] == 4096
    assert extent["modified_end_bytes"] == 8192


def test_an_unreadable_sector_size_falls_back_to_512() -> None:
    assert logical_sector_bytes("/dev/no-such-device-here") == 512


# --------------------------------------------------------------- the backup


def test_the_backup_length_is_the_measured_extent_not_the_constant(
    bench: Any,
) -> None:
    """The backup and the write read the same number from the same file."""
    kit = bench()
    kit.build(12288 + 1)
    result = kit.capture()
    assert result["bytes"] == 16384
    assert result["bytes"] != MEDIA_SIZE
    assert result["write_extent"]["length_bytes"] == 12289
    record = read_backup_record(kit.work)
    assert record is not None
    assert record["write_extent"]["modified_end_bytes"] == 16384
    assert record["serial"] == SERIAL
    backup, _ = backup_paths(kit.work)
    assert backup.stat().st_size == 16384


def test_the_backup_cannot_fall_back_to_the_constant(bench: Any) -> None:
    """An image unlike the declared geometry still gets a matching backup.

    If anything in the chain reached for ``MEDIA_SIZE`` the numbers below would
    be 255 MiB, and the write of a larger image would be under-covered.
    """
    kit = bench()
    kit.build(MEDIA_SIZE + 4096)
    extent = write_extent(kit.image, sector_bytes=512)
    assert extent["modified_end_bytes"] == MEDIA_SIZE + 4096
    kit.plant(backup_bytes=MEDIA_SIZE, extent_end=MEDIA_SIZE)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["expected_write_range"] == [0, MEDIA_SIZE + 4096]
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "4096 bytes short" in " ".join(report["blocking"])


def test_a_backup_of_a_device_shorter_than_the_extent_is_refused(
    bench: Any,
) -> None:
    """A short read leaves no record, so the write gate cannot accept it."""
    kit = bench()
    kit.build(DEVICE_BYTES + 4096)
    with pytest.raises(Refused, match="short of the"):
        kit.capture()
    assert read_backup_record(kit.work) is None


def test_an_unbuilt_image_has_nothing_to_back_up(bench: Any) -> None:
    kit = bench()
    with pytest.raises(Refused, match="has not been built"):
        kit.capture()


# ------------------------------------------------------- coverage boundaries


@pytest.mark.parametrize(
    ("backup_bytes", "sufficient"),
    [
        (16384, True),  # exactly sufficient
        (16383, False),  # undersized by one byte
        (16385, True),  # larger than required
        (16384 + 4096, True),  # a whole block larger
        (12288, False),  # a block short
        (0, False),  # nothing at all
    ],
)
def test_coverage_is_decided_against_the_measured_extent(
    bench: Any, backup_bytes: int, sufficient: bool
) -> None:
    kit = bench()
    kit.build(12288 + 1)  # a 4095-byte tail: the extent ends at 16384
    kit.plant(backup_bytes=backup_bytes, extent_end=16384)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["expected_write_range"] == [0, 16384]
    assert report["sufficient_for_restoring_the_modified_region"] is sufficient


def test_a_missing_backup_is_insufficient(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["backup_present"] is False
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "does not exist" in " ".join(report["blocking"])


def test_an_unbuilt_image_leaves_the_extent_unknown(bench: Any) -> None:
    kit = bench()
    kit.plant(backup_bytes=8192, extent_end=8192)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["expected_write_range"] is None
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "has not been built" in " ".join(report["blocking"])


def test_a_backup_stored_on_the_target_device_is_refused(bench: Any) -> None:
    """A backup on the disk it protects is destroyed by the write it undoes."""
    kit = bench(backup_disk="sdb")
    kit.build(8192)
    kit.plant(backup_bytes=8192, extent_end=8192)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["location"]["on_host_storage"] is False
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "is the target device" in " ".join(report["blocking"])


def test_a_backup_on_a_partition_of_the_target_is_refused(
    bench: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = bench(_disk(children=[{"name": "sdb1", "mountpoints": [None]}]))
    monkeypatch.setattr(
        "scripts.media_benchmark._backing_source", lambda path: "/dev/sdb1"
    )
    monkeypatch.setattr("scripts.media_benchmark._disk_of", lambda source: "")
    kit.build(8192)
    kit.plant(backup_bytes=8192, extent_end=8192)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["location"]["on_host_storage"] is False


def test_a_refused_preflight_blocks_the_backup_verdict(bench: Any) -> None:
    kit = bench(_disk(serial="SOMEOTHERSERIAL"))
    kit.build(8192)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["serial_matches"] is False
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "preflight refused" in " ".join(report["blocking"])


# --------------------------------------------------------------- provenance


def test_a_backup_without_a_record_is_refused(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    _, meta = backup_paths(kit.work)
    meta.unlink()
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "origin is unknown" in " ".join(report["blocking"])


def test_a_backup_taken_from_another_serial_is_refused(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    kit.amend(serial="SOMEOTHERSERIAL")
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["backup_serial"] == "SOMEOTHERSERIAL"
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "the device now reports" in " ".join(report["blocking"])


def test_a_backup_taken_for_another_extent_is_refused(bench: Any) -> None:
    """The image grew after the backup was taken; the copy is now short."""
    kit = bench()
    kit.build(8192)
    kit.capture()
    kit.build(16384)
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["sufficient_for_restoring_the_modified_region"] is False
    joined = " ".join(report["blocking"])
    assert "8192-byte write extent" in joined
    assert "16384 bytes" in joined


def test_a_backup_whose_bytes_changed_after_capture_is_refused(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    backup, _ = backup_paths(kit.work)
    data = bytearray(backup.read_bytes())
    data[0] ^= 0xFF
    backup.write_bytes(bytes(data))
    report = verify_backup(kit.path, kit.work, expect_serial=SERIAL)
    assert report["sufficient_for_restoring_the_modified_region"] is False
    assert "no longer match the hash" in " ".join(report["blocking"])


# ------------------------------------------------------------- the write gate


def test_the_write_refuses_when_the_backup_is_missing(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    with pytest.raises(Refused, match="does not exist"):
        _write(kit)
    assert kit.untouched()


def test_the_write_refuses_when_the_backup_is_undersized(bench: Any) -> None:
    kit = bench()
    kit.build(12288 + 1)
    kit.plant(backup_bytes=16383, extent_end=16384)
    with pytest.raises(Refused, match="1 bytes short"):
        _write(kit)
    assert kit.untouched()


def test_the_write_refuses_when_the_backup_is_on_the_target(bench: Any) -> None:
    kit = bench(backup_disk="sdb")
    kit.build(8192)
    kit.plant(backup_bytes=8192, extent_end=8192)
    with pytest.raises(Refused, match="is the target device"):
        _write(kit)
    assert kit.untouched()


def test_the_write_refuses_when_the_backup_serial_differs(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    kit.amend(serial="SOMEOTHERSERIAL")
    with pytest.raises(Refused, match="the device now reports"):
        _write(kit)
    assert kit.untouched()


def test_the_write_refuses_when_the_backup_extent_differs(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    kit.build(16384)
    with pytest.raises(Refused, match="write extent"):
        _write(kit)
    assert kit.untouched()


def test_the_write_refuses_when_the_backup_covers_another_image(bench: Any) -> None:
    """The verification must be of the image being copied, not another file."""
    kit = bench()
    kit.build(8192)
    kit.capture()
    other = kit.work / "images" / "some-other.img"
    with open(other, "wb") as handle:
        handle.truncate(8192)
    with pytest.raises(Refused, match="the backup was verified against"):
        write_image(
            kit.path,
            other,
            kit.work,
            expect_serial=SERIAL,
            confirm_serial=SERIAL,
            acknowledged=True,
        )
    assert kit.untouched()


def test_the_write_still_refuses_an_image_larger_than_the_device(bench: Any) -> None:
    """The out-of-range case: the copy would run off the end of the medium."""
    kit = bench(_disk(size=MEDIA_SIZE))
    kit.build(MEDIA_SIZE + 1)
    with pytest.raises(Refused, match="larger than the device"):
        _write(kit)
    assert kit.untouched()


def test_the_write_still_refuses_a_mounted_target(bench: Any) -> None:
    kit = bench(_disk(mountpoints=["/run/media/someone/STICK"]))
    kit.build(8192)
    with pytest.raises(Refused, match="mounted filesystems"):
        _write(kit)
    assert kit.untouched()


def test_the_write_still_refuses_without_the_acknowledgement(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    with pytest.raises(Refused, match="acknowledgement flag"):
        _write(kit, acknowledged=False)
    assert kit.untouched()


def test_the_write_still_refuses_a_mistyped_serial(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    with pytest.raises(Refused, match="does not match the device"):
        _write(kit, confirm_serial="B103B9C19DE1CCC1BD535ACC")
    assert kit.untouched()


def test_the_write_proceeds_once_the_backup_covers_the_extent(bench: Any) -> None:
    """The only test here that gets past the gate, and it states what it took."""
    kit = bench()
    image = kit.build(8192)
    image.write_bytes(b"CORPUS42" * 1024)
    captured = kit.capture()
    assert captured["bytes"] == 8192

    result = _write(kit)
    assert result["bytes_written"] == 8192
    assert result["readback_matches"] is True
    assert result["backup_bytes"] == 8192
    assert result["write_extent"]["modified_end_bytes"] == 8192
    assert kit.device.read_bytes()[:8192] == b"CORPUS42" * 1024
    # Nothing is asserted about the bytes past the extent: the stand-in is a
    # regular file, and ``open(..., "wb")`` truncates one. On a block device
    # O_TRUNC is a no-op, which is why the extent, not the file length, is what
    # the gate above is built on.


# ------------------------------------------------------------------ the plan


def test_the_plan_is_review_only_and_writes_nothing(bench: Any) -> None:
    kit = bench()
    kit.build(8192)
    kit.capture()
    plan = prewrite_plan(kit.path, kit.work, expect_serial=SERIAL)
    assert plan["approved"] is False
    assert plan["verdict"].startswith("REVIEW ONLY")
    assert plan["write_offset"] == 0
    assert plan["write_modified_end_bytes"] == 8192
    assert plan["backup_sufficient"] is True
    assert plan["backup_serial"] == SERIAL
    assert kit.untouched()

    text = render_plan(plan)
    for heading in (
        "DEVICE",
        "expected serial",
        "source image",
        "image sha256",
        "length",
        "blocks modified",
        "BACKUP",
        "backup device",
        "backup serial",
        "coverage status",
        "preflight",
        "APPROVED  false",
    ):
        assert heading in text
    assert "NOTHING HAS BEEN WRITTEN" in text
    assert "--i-understand-this-destroys-data" in text
    # The typed-serial gate is the operator's, so the plan must not hand them a
    # command they can paste without typing it.
    assert "--confirm-serial <type the device serial yourself>" in text


# ------------------------------------------------ identity and privilege


def test_the_write_refuses_when_the_serial_sources_disagree(bench: Any) -> None:
    kit = bench(kernel=("B103B9C19DE1CCC1BD535ACC", SYSFS_SERIAL))
    kit.build(8192)
    with pytest.raises(Refused, match="disagree"):
        _write(kit)
    assert kit.untouched()


def test_the_write_refuses_an_unverified_serial(bench: Any) -> None:
    """A covering backup and a correctly typed serial do not stand in for it."""
    kit = bench()
    kit.build(8192)
    kit.capture()
    bench(kernel=(None, None))
    with pytest.raises(Refused, match="UNVERIFIED"):
        _write(kit)
    assert kit.untouched()


def _deny(kit: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make opening the stand-in fail the way a non-root open of /dev/sdb does."""

    def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == kit.path:
            raise PermissionError(13, "Permission denied", str(file))
        return builtins.open(file, *args, **kwargs)

    monkeypatch.setattr("scripts.media_benchmark.open", guarded, raising=False)


def test_a_permission_denied_backup_is_a_privilege_refusal(
    bench: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = bench()
    kit.build(8192)
    _deny(kit, monkeypatch)
    with pytest.raises(PrivilegeRefused, match="privilege") as refused:
        kit.capture()
    assert refused.value.kind == "privilege"
    assert "sudo" not in str(refused.value)
    backup, meta = backup_paths(kit.work)
    assert not backup.exists()
    assert not meta.exists()
    assert kit.untouched()


def test_the_cli_reports_a_privilege_refusal_without_a_traceback(
    bench: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    kit = bench()
    kit.build(8192)
    _deny(kit, monkeypatch)
    code = main(
        ["backup", "--device", kit.path, "--work", str(kit.work),
         "--expect-serial", SERIAL]
    )
    out = capsys.readouterr()
    assert code == 2
    assert json.loads(out.out)["kind"] == "privilege"
    assert "Traceback" not in out.out + out.err
    assert kit.untouched()


def test_the_cli_still_reports_a_mounted_target_as_a_safety_refusal(
    bench: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    kit = bench(_disk(mountpoints=["/run/media/someone/STICK"]))
    kit.build(8192)
    code = main(
        ["backup", "--device", kit.path, "--work", str(kit.work),
         "--expect-serial", SERIAL]
    )
    reply = json.loads(capsys.readouterr().out)
    assert code == 2
    assert reply["kind"] == "safety"
    assert "mounted filesystems" in reply["refused"]
    assert not backup_paths(kit.work)[0].exists()
    assert kit.untouched()


def test_the_plan_shows_both_serial_sources_and_claims_no_human_check(
    bench: Any,
) -> None:
    kit = bench(_disk(mountpoints=["/run/media/someone/STICK"]), kernel=(None, None))
    kit.build(8192)
    plan = prewrite_plan(kit.path, kit.work, expect_serial=SERIAL)
    text = render_plan(plan)
    for heading in (
        "CURRENT SERIAL SOURCE A",
        "CURRENT SERIAL SOURCE B",
        "SERIAL AGREES           UNVERIFIED",
        "EXPECTED SERIAL",
        "HUMAN CONFIRMATION      required",
        "MOUNT STATE             /run/media/someone/STICK",
        "PREFLIGHT               REFUSED",
        "WRITE EXTENT",
        "BACKUP EXTENT           none",
        "BACKUP STATUS           absent",
        "BACKUP LOCATION",
        "PRIVILEGE STATUS",
        "APPROVED: false",
        "VERDICT: REVIEW ONLY",
    ):
        assert heading in text
    assert "human confirmed" not in text.lower()
    assert any("UNVERIFIED" in reason for reason in plan["blocking"])
    assert kit.untouched()
