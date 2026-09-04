"""The hidden-area phases, on a host with no HPA to find.

No loopback device will ever report an HPA or a DCO - they are ATA features of
real spinning and SATA flash media - so the branch that matters most cannot be
reached by pointing the erase engine at one. These tests fake the hidden-area
report instead, which is the only way to cover the case where sectors really are
hidden and really do have to be unlocked before the wipe reaches them.

Faking the *probe* rather than the device is deliberate. Everything downstream
of ``detect_hidden_areas`` - the unlock decision, the geometry widening, the
ledger entries, the restore - is the code under test and runs unaltered.

Needs no root: ``device_geometry`` is stubbed because ``BLKGETSIZE64`` only
answers for a block device, and creating one needs privileges these tests
deliberately do not require.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from core.models import ErasePhase, HiddenAreaReport, SanitizationLevel

from .conftest import make_caps, make_device, make_job

if sys.platform != "linux":  # pragma: no cover - platform gate
    pytest.skip("core.erase.drive is Linux-only", allow_module_level=True)

from core.erase import drive  # noqa: E402
from core.erase.drive import ChainLedgerSink, Geometry, execute  # noqa: E402
from core.ledger.chain import ChainStatus, Ledger  # noqa: E402

MIB = 1024 * 1024
SECTOR = 512
DEVICE_BYTES = 64 * MIB

EXPECTED_PHASES = [
    ErasePhase.PREFLIGHT,
    ErasePhase.HIDDEN_AREA_UNLOCK,
    ErasePhase.ERASE,
    ErasePhase.HIDDEN_AREA_RESTORE,
    ErasePhase.VERIFY,
    ErasePhase.REPORT,
]


def hidden_report(hidden_bytes: int) -> HiddenAreaReport:
    """A report with ``hidden_bytes`` beyond the accessible max."""
    accessible = DEVICE_BYTES // SECTOR
    return HiddenAreaReport(
        hpa_present=hidden_bytes > 0,
        dco_present=False,
        native_max_sectors=accessible + hidden_bytes // SECTOR,
        accessible_sectors=accessible,
        hidden_bytes=hidden_bytes,
    )


def run_execute(
    tmp_path: Path,
    *,
    hidden_bytes: int,
    dry_run: bool = True,
    **job_overrides: Any,
) -> tuple[Ledger, list[Any]]:
    """Drive ``execute`` with a faked hidden-area probe. Returns (ledger, progress)."""
    device = make_device(path="/dev/loop-fake", serial="SYN-0001", by_id_path=None)
    job = make_job(device, dry_run=dry_run, **job_overrides)
    chain = Ledger(
        tmp_path / "ledger", tool_version="0.0.0-test", pubkey_fingerprint="AA:BB"
    )
    sink = ChainLedgerSink(chain)
    geometry = Geometry(
        size_bytes=DEVICE_BYTES, logical_block_size=SECTOR, physical_block_size=SECTOR
    )

    progress: list[Any] = []
    with (
        mock.patch.object(drive, "device_geometry", return_value=geometry),
        mock.patch.object(
            drive.hidden_areas,
            "detect_hidden_areas",
            return_value=hidden_report(hidden_bytes),
        ),
        mock.patch.object(drive.guard, "assert_erasable"),
        mock.patch.object(drive, "_reread_serial"),
    ):
        generator = execute(job, make_caps(), io=None, ledger=sink)
        try:
            while True:
                progress.append(next(generator))
        except StopIteration:
            pass
    return chain, progress


def ledgered_phases(chain: Ledger) -> list[str]:
    """The phase of each erase entry, collapsed to a run-length sequence."""
    seen: list[str] = []
    for entry in chain.entries():
        if not entry.operation.startswith("erase."):
            continue
        phase = entry.operation.split(".")[1].upper()
        if not seen or seen[-1] != phase:
            seen.append(phase)
    return seen


def operations(chain: Ledger) -> list[str]:
    return [
        entry.operation
        for entry in chain.entries()
        if entry.operation.startswith("erase.")
    ]


# --------------------------------------------------------------------------
# Both branches record the same six phases
# --------------------------------------------------------------------------


def test_a_drive_with_no_hidden_area_still_ledgers_the_unlock_phase(
    tmp_path: Path,
) -> None:
    """"Probed and found none" must be distinguishable from "never probed".

    This is the case a loopback device produces, and the one that used to
    record nothing at all: the phase yielded progress and wrote no entry, so a
    chain read afterwards could not tell that the tool had looked. Its sibling
    HIDDEN_AREA_RESTORE has always recorded ``not_required`` in exactly the
    analogous situation, which is what makes a fixed phase list the right
    invariant rather than a conditional one.
    """
    chain, _ = run_execute(tmp_path, hidden_bytes=0)

    assert ledgered_phases(chain) == [phase.value for phase in EXPECTED_PHASES]
    entry = next(
        item
        for item in operations(chain)
        if item.startswith("erase.hidden_area_unlock.")
    )
    assert entry == "erase.hidden_area_unlock.not_required"


def test_a_drive_with_a_hidden_area_ledgers_the_unlock_before_erasing(
    tmp_path: Path,
) -> None:
    """The case no loop device can reach, and the one that matters.

    The entry has to be written *before* the unlock is attempted, so an
    interrupted job leaves a record of the accessible sector count to restore
    to. Without it a crash mid-unlock leaves a drive reporting its native max
    with nothing on disk saying what it used to report.
    """
    chain, _ = run_execute(tmp_path, hidden_bytes=8 * MIB)

    assert ledgered_phases(chain) == [phase.value for phase in EXPECTED_PHASES]
    unlock = next(
        item
        for item in operations(chain)
        if item.startswith("erase.hidden_area_unlock.")
    )
    assert unlock == "erase.hidden_area_unlock.before"

    params = next(
        chain.params_of(entry)
        for entry in chain.entries()
        if entry.operation == "erase.hidden_area_unlock.before"
    )
    assert params["hidden_bytes"] == 8 * MIB
    assert params["accessible_sectors"] == DEVICE_BYTES // SECTOR
    assert params["native_max_sectors"] > params["accessible_sectors"]
    assert "restore" in params["note"], (
        "the entry must say what to restore to, or it is not a recovery record"
    )


def test_the_not_required_entry_records_what_was_probed(tmp_path: Path) -> None:
    """An entry saying only "skipped" would be no better than no entry."""
    chain, _ = run_execute(tmp_path, hidden_bytes=0)

    params = next(
        chain.params_of(entry)
        for entry in chain.entries()
        if entry.operation == "erase.hidden_area_unlock.not_required"
    )
    assert params["hidden_bytes"] == 0
    assert params["accessible_sectors"] == DEVICE_BYTES // SECTOR
    assert params["native_max_sectors"] == params["accessible_sectors"]
    assert "no HPA or DCO" in params["reason"]


@pytest.mark.parametrize("hidden_bytes", [0, 8 * MIB])
def test_the_chain_verifies_either_way(tmp_path: Path, hidden_bytes: int) -> None:
    chain, _ = run_execute(tmp_path, hidden_bytes=hidden_bytes)
    verification = chain.verify()
    assert verification.status is ChainStatus.VALID, verification.explanation


@pytest.mark.parametrize("hidden_bytes", [0, 8 * MIB])
def test_progress_reports_the_same_six_phases_the_ledger_does(
    tmp_path: Path, hidden_bytes: int
) -> None:
    """The operator's view and the audit record must not disagree.

    A phase visible in the UI and absent from the chain, or the reverse, would
    mean the thing the operator watched happen is not the thing a third party
    can later check.
    """
    chain, progress = run_execute(tmp_path, hidden_bytes=hidden_bytes)

    seen: list[str] = []
    for item in progress:
        if not seen or seen[-1] != item.phase:
            seen.append(item.phase)

    assert seen == [phase.value for phase in EXPECTED_PHASES]
    assert seen == ledgered_phases(chain)


# --------------------------------------------------------------------------
# A firmware method covers the hidden area itself
# --------------------------------------------------------------------------


def test_a_firmware_method_records_that_it_covers_the_hidden_area(
    tmp_path: Path,
) -> None:
    """Not an omission: SANITIZE reaches the full media, HPA included.

    So there is no host-side unlock to perform, and the ledger has to say *why*
    rather than leave a reader to assume the phase was forgotten.
    """
    from core.models import EraseMethod

    device = make_device(path="/dev/loop-fake", serial="SYN-0001", by_id_path=None)
    job = make_job(
        device,
        dry_run=True,
        level=SanitizationLevel.PURGE,
        method=None,
    )
    chain = Ledger(
        tmp_path / "ledger", tool_version="0.0.0-test", pubkey_fingerprint="AA:BB"
    )
    capabilities = make_caps(
        ata_sanitize_ops=["BLOCK_ERASE_EXT"],
        achievable_levels={SanitizationLevel.CLEAR, SanitizationLevel.PURGE},
    )
    geometry = Geometry(
        size_bytes=DEVICE_BYTES, logical_block_size=SECTOR, physical_block_size=SECTOR
    )

    with (
        mock.patch.object(drive, "device_geometry", return_value=geometry),
        mock.patch.object(
            drive.hidden_areas,
            "detect_hidden_areas",
            return_value=hidden_report(8 * MIB),
        ),
        mock.patch.object(drive.guard, "assert_erasable"),
        mock.patch.object(drive, "_reread_serial"),
    ):
        generator = execute(job, capabilities, io=None, ledger=ChainLedgerSink(chain))
        try:
            while True:
                next(generator)
        except StopIteration:
            pass

    assert ledgered_phases(chain) == [phase.value for phase in EXPECTED_PHASES]
    unlock = next(
        item
        for item in operations(chain)
        if item.startswith("erase.hidden_area_unlock.")
    )
    assert unlock == "erase.hidden_area_unlock.skipped"

    params = next(
        chain.params_of(entry)
        for entry in chain.entries()
        if entry.operation == "erase.hidden_area_unlock.skipped"
    )
    assert "firmware" in params["reason"]
    assert "HPA/DCO" in params["reason"]
    assert EraseMethod.ATA_SANITIZE_BLOCK_ERASE.value in params["reason"]


def test_every_phase_appears_exactly_once_in_the_collapsed_sequence(
    tmp_path: Path,
) -> None:
    """No phase may be recorded twice in two separate runs of entries.

    Two runs of the same phase would mean the erase re-entered a phase it had
    left, and the chain would no longer describe a single forward pass.
    """
    chain, _ = run_execute(tmp_path, hidden_bytes=8 * MIB)
    phases = ledgered_phases(chain)
    assert len(phases) == len(set(phases))
