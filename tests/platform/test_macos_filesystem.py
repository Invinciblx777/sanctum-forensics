"""APFS behaviour that can only be tested on macOS. Skipped elsewhere.

Runs on the `macos-14` CI runner against its real APFS volume. The claim
being pinned is the honest one: on a copy-on-write filesystem the file goes,
and the old blocks are *not* confirmed destroyed, so the verification is
``not_possible`` with a reason and never a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="APFS/macOS behaviour; this host is not macOS"
)


def _erase_options(**over: object) -> object:
    from core.models import FileEraseOptions

    base: dict[str, object] = {"dry_run": False, "confirm": True}
    base.update(over)
    return FileEraseOptions.model_validate(base)


def test_the_filesystem_is_identified_through_statfs(tmp_path: Path) -> None:
    from core.erase._platform import backend

    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 4096)

    fs_type, limits = backend().fs_type(target)

    assert fs_type, f"no filesystem type was determined: {limits}"
    assert fs_type.lower() in {"apfs", "hfs"}, fs_type


def test_a_file_erase_on_apfs_is_never_reported_as_verified(tmp_path: Path) -> None:
    from core.erase._platform import backend
    from core.erase.files import erase_one

    target = tmp_path / "secret.bin"
    target.write_bytes(b"s" * 65536)
    fs_type, _ = backend().fs_type(target)
    if fs_type.lower() != "apfs":  # pragma: no cover - runner is APFS
        pytest.skip(f"this volume is {fs_type}, not APFS")

    record = erase_one(target, _erase_options())

    assert record.ok, record.error
    assert record.unlinked
    assert not target.exists()
    assert record.verification is not None
    assert record.verification.passed is not True, (
        "a copy-on-write filesystem cannot confirm the old blocks are gone"
    )
    assert record.verification.reason, "an unverifiable erase must say why"


def test_the_copy_on_write_residual_is_reported(tmp_path: Path) -> None:
    from core.erase._platform import backend
    from core.erase.files import erase_one
    from core.models import ResidualKind

    target = tmp_path / "doc.bin"
    target.write_bytes(b"d" * 32768)
    fs_type, _ = backend().fs_type(target)
    if fs_type.lower() not in {"apfs"}:  # pragma: no cover
        pytest.skip(f"this volume is {fs_type}")

    record = erase_one(target, _erase_options())

    kinds = {finding.kind for finding in record.findings}
    assert kinds, "an APFS erase with no findings would be claiming too much"
    assert any(
        kind
        in {ResidualKind.COW_SNAPSHOT, ResidualKind.TRIM_REMAP, ResidualKind.FS_JOURNAL}
        for kind in kinds
    ), f"expected a copy-on-write or unverifiable finding, got {kinds}"


def test_a_folder_erase_removes_its_contents_and_nothing_else(tmp_path: Path) -> None:
    from core.erase.files import erase_paths
    from core.erase.sink import LedgerSink

    root = tmp_path / "case"
    (root / "nested").mkdir(parents=True)
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "other.txt").write_text("not a target")
    for index in range(3):
        (root / f"f{index}.bin").write_bytes(b"x" * 1024)
    (root / "nested" / "deep.bin").write_bytes(b"y" * 1024)

    class _Sink(LedgerSink):
        def record(self, phase: object, event: str, detail: dict[str, object]) -> None:
            return None

    generator = erase_paths([root], _erase_options(), job_id="mac", ledger=_Sink())
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        result = stop.value

    assert not root.exists()
    assert (keep / "other.txt").read_text() == "not a target"
    assert len(result.records) == 6
    assert all(record.ok for record in result.records)


def test_a_symlink_out_of_the_tree_is_not_followed(tmp_path: Path) -> None:
    from core.erase.files import expand_targets

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    targets = expand_targets([root])

    assert outside / "keep.txt" not in targets
    assert (outside / "keep.txt").is_file()


@pytest.mark.parametrize(
    "path", ["/System/Library", "/usr/lib/dyld", "/private/var/vm"]
)
def test_protected_macos_locations_are_refused(path: str) -> None:
    from core.erase.files import erase_one
    from core.errors import SystemDiskRefused
    from core.models import FileEraseOptions

    if not Path(path).exists():  # pragma: no cover
        pytest.skip(f"{path} does not exist on this runner")

    with pytest.raises(SystemDiskRefused):
        erase_one(Path(path), FileEraseOptions())


def test_discovery_protects_the_disk_the_system_boots_from() -> None:
    from core.platform import current_adapter

    adapter = current_adapter()
    devices = adapter.enumerate_devices()

    assert devices, "diskutil returned no physical disks"
    system = [device for device in devices if device.system_device]
    assert system, "the disk backing the boot APFS container was not protected"
    for device in system:
        assert device.system_reasons
        assessment = adapter.assess_device(device)
        assert assessment.headline == "NOT AVAILABLE"
    assert any("APFS" in device.filesystems for device in devices), (
        "no APFS volume was seen on a Mac"
    )


def test_whole_drive_is_refused_on_macos() -> None:
    from core.errors import PlatformUnsupported
    from core.platform import current_adapter

    adapter = current_adapter()

    with pytest.raises(PlatformUnsupported, match="No operation was performed"):
        next(adapter.execute_drive_sanitization({"path": "disk0", "dry_run": False}))
