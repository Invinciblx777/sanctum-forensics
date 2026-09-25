"""A dry run never reaches a write path, and opens the device read-only at most.

Precise claim, because the loose one is false: ``execute`` in dry-run mode *does*
open the device node, ``O_RDONLY``, for the ``BLKGETSIZE64`` geometry ioctl. It
never opens it for writing, never calibrates, never dispatches an erase and never
verifies. Every write-capable seam is replaced with a tripwire that fails the
test if it is called, and ``os.open`` is recorded so the flags are asserted.
"""

from __future__ import annotations

import fcntl
import os
import struct
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from core.ledger.chain import Ledger

from .conftest import make_caps, make_device, make_job
from .test_hidden_area_phases import DEVICE_BYTES, hidden_report

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="core.erase.drive is Linux-only by design"
)

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _tripwire(name: str) -> Any:
    def hit(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(f"a dry run reached {name}")

    return hit


def test_a_dry_run_opens_the_device_read_only_and_never_writes(
    tmp_path: Path,
) -> None:
    from core.erase import drive
    from core.erase.drive import ChainLedgerSink, execute

    device = make_device(path="/dev/loop-fake", serial="SYN-0001", by_id_path=None)
    device = device.model_copy(update={"size_bytes": DEVICE_BYTES})
    job = make_job(device, dry_run=True)
    chain = Ledger(
        tmp_path / "ledger", tool_version="0.0.0-test", pubkey_fingerprint="A"
    )
    opened: list[tuple[str, int]] = []
    real_open = os.open

    def recording_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        opened.append((str(path), flags))
        if str(path) == "/dev/loop-fake":
            return real_open("/dev/null", os.O_RDONLY)
        return real_open(path, flags, *args, **kwargs)

    with (
        mock.patch.object(drive.os, "open", recording_open),
        mock.patch.object(fcntl, "ioctl", return_value=struct.pack("Q", DEVICE_BYTES)),
        mock.patch.object(drive.guard, "assert_erasable"),
        mock.patch.object(drive, "_reread_serial"),
        mock.patch.object(
            drive.hidden_areas, "detect_hidden_areas", return_value=hidden_report(0)
        ),
        mock.patch.object(drive, "_open_for_write", _tripwire("_open_for_write")),
        mock.patch.object(drive, "_dispatch", _tripwire("_dispatch")),
        mock.patch.object(
            drive.calibrate_mod, "calibrate_write", _tripwire("calibrate_write")
        ),
        mock.patch.object(drive.verify_mod, "verify", _tripwire("verify")),
    ):
        generator = execute(job, make_caps(), io=None, ledger=ChainLedgerSink(chain))
        try:
            while True:
                next(generator)
        except StopIteration as stop:
            result = stop.value

    assert result is not None and result.plan is not None
    device_opens = [flags for path, flags in opened if path == "/dev/loop-fake"]
    assert device_opens, "geometry is read through a read-only open"
    assert all(flags & WRITE_FLAGS == 0 for flags in device_opens)
