"""The overwrite loop and its verification, driven against a regular file.

Linux only (core.erase.drive is), but no root needed: ``_overwrite`` writes to
whatever path it is given, so a file stands in for the medium. That keeps the
most safety-critical loop under test on any Linux box.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from core.erase.verify import VerifyConfig, verify
from core.models import EraseMethod

from .conftest import make_device

if sys.platform != "linux":  # pragma: no cover - platform gate
    pytest.skip("core.erase.drive is Linux-only", allow_module_level=True)

from core.erase.drive import Geometry, _overwrite  # noqa: E402
from core.models import EraseCheckpoint, ErasePhase  # noqa: E402


class RecordingLedger:
    """An in-memory :class:`~core.erase.drive.LedgerSink` for these tests.

    It lives here rather than in ``core/`` deliberately. A ledger that keeps
    entries in memory is neither durable nor hash-chained, and while ``core``
    shipped one it was possible for a real erase to default to it and produce a
    result nobody could audit - which is the limitation
    ``docs/limitations.md`` records. Test scaffolding is the right home for it:
    what these tests need is to see the phase records the overwrite loop emits,
    not to prove anything about the chain, which ``tests/ledger`` covers.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[ErasePhase, str, dict[str, Any]]] = []

    def record(
        self, phase: ErasePhase, operation: str, payload: dict[str, Any]
    ) -> None:
        self.entries.append((phase, operation, dict(payload)))

    def last_checkpoint(self, job_id: str) -> EraseCheckpoint | None:
        for _phase, operation, payload in reversed(self.entries):
            if operation != "checkpoint" or payload.get("job_id") != job_id:
                continue
            return EraseCheckpoint.model_validate(payload)
        return None

MIB = 1024 * 1024
KIB = 1024
BLOCK = 4096

FULL_READ = VerifyConfig(full_read_max_bytes=1024 * MIB)


def drain(generator: Any) -> Any:
    """Run a generator to completion and return its value."""
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


def geometry_for(path: Path) -> Geometry:
    return Geometry(
        size_bytes=path.stat().st_size,
        logical_block_size=BLOCK,
        physical_block_size=BLOCK,
    )


def run_overwrite(
    path: Path,
    method: EraseMethod = EraseMethod.SINGLE_PASS_OVERWRITE,
    **kwargs: Any,
) -> Any:
    ledger = kwargs.pop("ledger", None) or RecordingLedger()
    outcome = drain(
        _overwrite(
            make_device(path=str(path), size_bytes=path.stat().st_size),
            geometry_for(path),
            method,
            job_id="job-0001",
            ledger=ledger,
            **kwargs,
        )
    )
    return outcome, ledger


# --------------------------------------------------------------------------
# Spec test 1: wipe leaves zeros and verification passes
# --------------------------------------------------------------------------


def test_single_pass_zeroes_every_byte(backing_file: Path) -> None:
    assert backing_file.read_bytes()[:16] == b"\xaa" * 16

    outcome, _ = run_overwrite(backing_file)

    data = backing_file.read_bytes()
    assert set(data) == {0}
    assert outcome.bytes_written >= backing_file.stat().st_size
    assert outcome.unwritable == []


def test_verification_passes_with_full_read_after_a_clean_wipe(
    backing_file: Path,
) -> None:
    run_overwrite(backing_file)
    result = verify(
        make_device(path=str(backing_file)),
        EraseMethod.SINGLE_PASS_OVERWRITE,
        source_path=backing_file,
        config=FULL_READ,
    )
    assert result.strategy == "full_read"
    assert result.passed is True
    assert result.failed_offsets == []


def test_dod_three_passes_also_end_in_zeros(backing_file: Path) -> None:
    outcome, _ = run_overwrite(backing_file, EraseMethod.DOD_5220_22_M_3PASS)
    assert outcome.passes == 3
    assert set(backing_file.read_bytes()) == {0}


# --------------------------------------------------------------------------
# Spec test 2: verification is real
# --------------------------------------------------------------------------


def test_residual_data_written_after_the_wipe_fails_verification(
    backing_file: Path,
) -> None:
    run_overwrite(backing_file)
    dirty_at = 12 * MIB
    with backing_file.open("r+b") as handle:
        handle.seek(dirty_at)
        handle.write(b"\xaa" * (4 * KIB))

    result = verify(
        make_device(path=str(backing_file)),
        EraseMethod.SINGLE_PASS_OVERWRITE,
        source_path=backing_file,
        config=FULL_READ,
    )
    assert result.passed is False
    assert dirty_at in result.failed_offsets


# --------------------------------------------------------------------------
# Spec test 5: checkpoint and resume
# --------------------------------------------------------------------------


def test_checkpoints_are_recorded_at_the_configured_interval(
    backing_file: Path,
) -> None:
    _, ledger = run_overwrite(
        backing_file, buffer_bytes=1 * MIB, checkpoint_bytes=8 * MIB
    )
    checkpoints = [e for e in ledger.entries if e[1] == "checkpoint"]
    assert len(checkpoints) >= 8


def test_interrupted_wipe_resumes_and_finishes_the_whole_device(
    backing_file: Path,
) -> None:
    ledger = RecordingLedger()
    device = make_device(
        path=str(backing_file), size_bytes=backing_file.stat().st_size
    )
    generator = _overwrite(
        device,
        geometry_for(backing_file),
        EraseMethod.SINGLE_PASS_OVERWRITE,
        job_id="job-0001",
        ledger=ledger,
        buffer_bytes=1 * MIB,
        checkpoint_bytes=4 * MIB,
    )
    for _ in range(12):  # stop part way through, as a crash would
        next(generator)
    generator.close()

    assert set(backing_file.read_bytes()) == {0, 0xAA}, "expected a partial wipe"

    checkpoint = ledger.last_checkpoint("job-0001")
    assert checkpoint is not None
    assert 0 < checkpoint.offset < backing_file.stat().st_size

    drain(
        _overwrite(
            device,
            geometry_for(backing_file),
            EraseMethod.SINGLE_PASS_OVERWRITE,
            job_id="job-0001",
            ledger=ledger,
            buffer_bytes=1 * MIB,
            checkpoint_bytes=4 * MIB,
            start_offset=checkpoint.offset,
            start_pass=checkpoint.pass_index,
        )
    )
    assert set(backing_file.read_bytes()) == {0}


# --------------------------------------------------------------------------
# Spec test 6: EIO does not abort the wipe
# --------------------------------------------------------------------------


def test_eio_is_recorded_and_the_wipe_continues(
    backing_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_offset = 8 * MIB
    real_write = os.write

    def flaky_write(fd: int, data: Any) -> int:
        position = os.lseek(fd, 0, os.SEEK_CUR)
        if position <= bad_offset < position + len(data):
            raise OSError(errno.EIO, "injected I/O error")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", flaky_write)
    outcome, _ = run_overwrite(backing_file, buffer_bytes=1 * MIB)

    assert len(outcome.unwritable) == 1
    bad = outcome.unwritable[0]
    assert bad.offset == bad_offset
    assert bad.length == BLOCK
    assert bad.errno == errno.EIO

    data = backing_file.read_bytes()
    assert data[bad_offset : bad_offset + BLOCK] == b"\xaa" * BLOCK
    assert set(data[:bad_offset]) == {0}
    assert set(data[bad_offset + BLOCK :]) == {0}


def test_eio_region_makes_verification_fail(
    backing_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_offset = 8 * MIB
    real_write = os.write

    def flaky_write(fd: int, data: Any) -> int:
        position = os.lseek(fd, 0, os.SEEK_CUR)
        if position <= bad_offset < position + len(data):
            raise OSError(errno.EIO, "injected I/O error")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", flaky_write)
    run_overwrite(backing_file, buffer_bytes=1 * MIB)
    monkeypatch.undo()

    result = verify(
        make_device(path=str(backing_file)),
        EraseMethod.SINGLE_PASS_OVERWRITE,
        source_path=backing_file,
        config=FULL_READ,
    )
    assert result.passed is False
    assert bad_offset in result.failed_offsets
