"""A recovery is ledgered, or it is not evidence.

The carve pipeline used to run without writing a single chain entry. Every other
destructive or evidential operation in the tool records itself, and `CLAUDE.md`
names that as a non-negotiable, so a recovery that produced candidates out of an
image and left no trace of having read it was the one hole in the audit trail.

These tests pin the two properties an examiner actually needs: that the run is in
the chain at all, and that the entries bind *these* findings to *that* image
rather than merely asserting a count somebody could edit.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest
from api.carve_job import IDENTITY_SAMPLE_BYTES, carve_generator
from core.ledger.canon import canonical_bytes
from core.ledger.chain import ChainStatus, Ledger
from PIL import Image

MIB = 1024 * 1024


def _jpeg(size: int, colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _image_with_two_jpegs(path: Path, *, total: int = 2 * MIB) -> Path:
    """A small raw image holding two real JPEGs at known offsets."""
    canvas = bytearray()
    seed = 0x12345678
    while len(canvas) < total:
        seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
        canvas += seed.to_bytes(4, "little")
    canvas = canvas[:total]
    for index, colour in enumerate([(200, 30, 30), (30, 200, 90)]):
        payload = _jpeg(64, colour)
        at = index * (total // 2) + 1337
        canvas[at : at + len(payload)] = payload
    path.write_bytes(bytes(canvas))
    return path


def _ledger(root: Path) -> Ledger:
    return Ledger(root, tool_version="sanctum-forensics/test", pubkey_fingerprint="")


def _carve(image: Path, ledger: Ledger | None, job_id: str) -> dict[str, Any]:
    """Run the pipeline to completion and return its result."""
    generator = carve_generator(
        image,
        undelete=False,
        carve_signatures=True,
        job_id=job_id,
        ledger=ledger,
    )
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        result: dict[str, Any] = stop.value
        return result


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    return _image_with_two_jpegs(tmp_path / "case.dd")


def test_a_carve_writes_a_start_and_a_complete_entry(
    evidence: Path, tmp_path: Path
) -> None:
    ledger = _ledger(tmp_path / "ledger")
    _carve(evidence, ledger, "carve-0001")

    operations = [entry.operation for entry in ledger.entries()]
    assert "carve.start" in operations
    assert "carve.complete" in operations


def test_the_chain_verifies_after_a_carve(evidence: Path, tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    _carve(evidence, ledger, "carve-0001")

    outcome = ledger.verify()
    assert outcome.status is ChainStatus.VALID, outcome.explanation


def test_both_entries_carry_the_job_id_the_report_filters_on(
    evidence: Path, tmp_path: Path
) -> None:
    """The report excerpt is built by matching ``params["job_id"]``.

    An entry without it is in the chain and absent from every report, which is
    the failure mode that looks like working software right up to the moment
    somebody asks for the audit trail.
    """
    ledger = _ledger(tmp_path / "ledger")
    _carve(evidence, ledger, "carve-abc123")

    matched = [
        entry
        for entry in ledger.entries()
        if ledger.params_of(entry).get("job_id") == "carve-abc123"
    ]
    assert {entry.operation for entry in matched} == {"carve.start", "carve.complete"}


def test_the_complete_entry_binds_the_candidate_list_by_digest(
    evidence: Path, tmp_path: Path
) -> None:
    """Counts can be edited without breaking anything. A digest cannot."""
    ledger = _ledger(tmp_path / "ledger")
    result = _carve(evidence, ledger, "carve-0001")

    complete = next(
        entry for entry in ledger.entries() if entry.operation == "carve.complete"
    )
    recorded = ledger.result_of(complete)["findings_sha256"]
    recomputed = hashlib.sha256(
        canonical_bytes({"candidates": result["candidates"]})
    ).hexdigest()
    assert recorded == recomputed


def test_the_entries_identify_the_evidence_that_was_read(
    evidence: Path, tmp_path: Path
) -> None:
    ledger = _ledger(tmp_path / "ledger")
    _carve(evidence, ledger, "carve-0001")

    start = next(
        entry for entry in ledger.entries() if entry.operation == "carve.start"
    )
    recorded = ledger.params_of(start)["evidence"]
    assert recorded["path"] == str(evidence)
    assert recorded["size_bytes"] == evidence.stat().st_size
    # Stated, not implied: this is not a whole-image hash and must never be read
    # as one. Acquisition is where the whole image is hashed.
    assert recorded["identity"]["is_whole_image_hash"] is False
    assert len(recorded["identity"]["sha256"]) == 64


def test_a_different_image_gets_a_different_identity(tmp_path: Path) -> None:
    """Otherwise the digest establishes nothing about which image was read."""
    first = _image_with_two_jpegs(tmp_path / "first.dd")
    second = tmp_path / "second.dd"
    second.write_bytes(b"\xa5" * (2 * MIB))

    ledger = _ledger(tmp_path / "ledger")
    _carve(first, ledger, "carve-0001")
    _carve(second, ledger, "carve-0002")

    identities = [
        ledger.params_of(entry)["evidence"]["identity"]["sha256"]
        for entry in ledger.entries()
        if entry.operation == "carve.start"
    ]
    assert len(identities) == 2
    assert identities[0] != identities[1]


def test_an_image_smaller_than_the_sample_window_still_identifies(
    tmp_path: Path,
) -> None:
    """The head and the tail overlap below 2 MiB. That must not read past the end."""
    small = tmp_path / "small.dd"
    small.write_bytes(_jpeg(64, (10, 10, 220)) + b"\x00" * 4096)
    assert small.stat().st_size < IDENTITY_SAMPLE_BYTES

    ledger = _ledger(tmp_path / "ledger")
    _carve(small, ledger, "carve-0001")

    start = next(
        entry for entry in ledger.entries() if entry.operation == "carve.start"
    )
    assert len(ledger.params_of(start)["evidence"]["identity"]["sha256"]) == 64


def test_a_carve_without_a_ledger_still_runs(evidence: Path) -> None:
    """The library path stays usable; only the API insists on a chain."""
    result = _carve(evidence, None, "carve-0001")
    assert "candidates" in result
    assert result["evidence"]["path"] == str(evidence)
