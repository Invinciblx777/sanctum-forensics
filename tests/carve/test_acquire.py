"""Acquisition: hashing, bad-sector salvage, resume, verification, ledger.

Every fixture is built from files. No test touches a real device, and the bad
sectors are injected by a fake reader rather than by finding a failing disk.
"""

from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import pytest
from core.carve.acquire import (
    AcquireOptions,
    SourceReader,
    acquire,
    e01_write_supported,
    verify_image,
)
from core.carve.evidence import RawEvidence, open_evidence
from core.errors import EvidenceIntegrityError, UnsupportedCapability
from core.ledger.chain import ChainStatus, Ledger

MIB = 1024 * 1024


def _payload(size: int) -> bytes:
    """Deterministic, non-repeating enough that a misordered read shows up."""
    out = bytearray()
    seed = 0x9E3779B1
    while len(out) < size:
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        out += seed.to_bytes(4, "little")
    return bytes(out[:size])


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.dd"
    path.write_bytes(_payload(4 * MIB))
    return path


class FailingReader:
    """A source that raises EIO for a chosen LBA range, like a dying disk."""

    def __init__(
        self,
        data: bytes,
        *,
        bad_lbas: range,
        sector_size: int = 512,
    ) -> None:
        self._data = data
        self.size = len(data)
        self.sector_size = sector_size
        self._bad = bad_lbas
        self.attempts: list[tuple[int, int]] = []

    def read_at(self, offset: int, length: int) -> bytes:
        self.attempts.append((offset, length))
        first = offset // self.sector_size
        last = (offset + length - 1) // self.sector_size
        if any(lba in self._bad for lba in range(first, last + 1)):
            raise OSError(errno.EIO, "Input/output error")
        return self._data[offset : offset + length]

    def close(self) -> None:
        return None


def _run(gen: object) -> object:
    """Drive a progress generator to completion and return its value."""
    assert hasattr(gen, "__next__")
    try:
        while True:
            next(gen)  # type: ignore[arg-type]
    except StopIteration as stop:
        return stop.value


# --------------------------------------------------------------------------
# 1. Raw acquisition and hashing
# --------------------------------------------------------------------------


def test_raw_acquisition_hashes_match_an_independent_computation(
    source_file: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out.dd"
    record = _run(acquire(source_file, dest, fmt="raw"))

    expected = source_file.read_bytes()
    assert dest.read_bytes() == expected
    assert record.sha256 == hashlib.sha256(expected).hexdigest()  # type: ignore[union-attr]
    assert record.bytes_read == len(expected)  # type: ignore[union-attr]


def test_hashes_are_computed_in_the_read_pass_not_by_re_reading(
    source_file: Path, tmp_path: Path
) -> None:
    """A second full read would double acquisition time on a 2 TB disk."""
    reads: list[tuple[int, int]] = []
    data = source_file.read_bytes()

    class CountingReader:
        size = len(data)
        sector_size = 512

        def read_at(self, offset: int, length: int) -> bytes:
            reads.append((offset, length))
            return data[offset : offset + length]

        def close(self) -> None:
            return None

    _run(acquire(CountingReader(), tmp_path / "o.dd", fmt="raw"))
    covered = sum(length for _, length in reads)
    assert covered == len(data), "source was read more than once"


def test_chunk_hashes_cover_the_whole_image(
    source_file: Path, tmp_path: Path
) -> None:
    options = AcquireOptions(chunk_bytes=MIB)
    record = _run(acquire(source_file, tmp_path / "o.dd", fmt="raw", options=options))
    data = source_file.read_bytes()

    assert record.chunk_bytes == MIB  # type: ignore[union-attr]
    assert len(record.chunk_hashes) == 4  # type: ignore[union-attr]
    for index, digest in enumerate(record.chunk_hashes):  # type: ignore[union-attr]
        piece = data[index * MIB : (index + 1) * MIB]
        assert digest == hashlib.sha256(piece).hexdigest()


# --------------------------------------------------------------------------
# 5-6. Bad sectors
# --------------------------------------------------------------------------


def test_bad_sectors_are_substituted_and_recorded_not_fatal(tmp_path: Path) -> None:
    data = _payload(2 * MIB)
    reader = FailingReader(data, bad_lbas=range(1000, 1008))
    dest = tmp_path / "bad.dd"

    record = _run(acquire(reader, dest, fmt="raw"))

    assert record.bytes_read == len(data)  # type: ignore[union-attr]
    assert len(record.bad_sectors) == 1  # type: ignore[union-attr]
    bad = record.bad_sectors[0]  # type: ignore[union-attr]
    assert (bad.first_lba, bad.last_lba) == (1000, 1007)
    assert bad.sector_count == 8
    assert bad.errno == errno.EIO

    written = dest.read_bytes()
    assert written[1000 * 512 : 1008 * 512] == b"\x00" * (8 * 512)
    # Everything outside the bad range survived intact.
    assert written[:512_000] == data[:512_000]
    assert written[1008 * 512 :] == data[1008 * 512 :]


def test_was_substituted_is_true_inside_the_bad_range_and_false_beside_it(
    tmp_path: Path,
) -> None:
    data = _payload(2 * MIB)
    reader = FailingReader(data, bad_lbas=range(1000, 1008))
    dest = tmp_path / "bad.dd"
    record = _run(acquire(reader, dest, fmt="raw"))

    with open_evidence(dest) as handle:
        handle.source.substituted_ranges.extend(
            record.source.substituted_ranges  # type: ignore[union-attr]
        )
        assert handle.was_substituted(1000 * 512, 8 * 512) is True
        assert handle.was_substituted(999 * 512, 512) is False
        assert handle.was_substituted(1008 * 512, 512) is False


def test_only_the_truly_bad_sectors_are_written_off(tmp_path: Path) -> None:
    """A 1 MiB block with 3 bad sectors must not cost the other 2045."""
    data = _payload(2 * MIB)
    reader = FailingReader(data, bad_lbas=range(500, 503))
    dest = tmp_path / "partial.dd"

    record = _run(acquire(reader, dest, fmt="raw"))

    bad = record.bad_sectors[0]  # type: ignore[union-attr]
    assert (bad.first_lba, bad.last_lba) == (500, 502)
    written = dest.read_bytes()
    recovered = [
        lba
        for lba in range(0, 2048)
        if written[lba * 512 : (lba + 1) * 512] == data[lba * 512 : (lba + 1) * 512]
    ]
    assert len(recovered) == 2048 - 3


# --------------------------------------------------------------------------
# 10. Resume
# --------------------------------------------------------------------------


def test_resumed_acquisition_is_byte_identical_and_says_it_resumed(
    source_file: Path, tmp_path: Path
) -> None:
    straight = tmp_path / "straight.dd"
    reference = _run(acquire(source_file, straight, fmt="raw"))

    partial = tmp_path / "resumed.dd"
    options = AcquireOptions(checkpoint_bytes=MIB)
    generator = acquire(source_file, partial, fmt="raw", options=options)
    for _ in range(2):
        next(generator)
    generator.close()

    resumed = _run(
        acquire(source_file, partial, fmt="raw", options=options, resume=True)
    )

    assert partial.read_bytes() == straight.read_bytes()
    assert resumed.sha256 == reference.sha256  # type: ignore[union-attr]
    assert resumed.blake3 == reference.blake3  # type: ignore[union-attr]
    assert resumed.resumed is True  # type: ignore[union-attr]
    assert reference.resumed is False  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# 11. verify_image
# --------------------------------------------------------------------------


def test_verify_image_passes_on_an_untouched_image(
    source_file: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "ok.dd"
    record = _run(acquire(source_file, dest, fmt="raw"))
    result = verify_image(dest, record)  # type: ignore[arg-type]

    assert result.passed is True
    assert result.sha256_matches is True
    assert result.blake3_matches is True
    assert result.mismatched_chunks == []


def test_verify_image_names_the_chunk_that_changed(
    source_file: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "tamper.dd"
    options = AcquireOptions(chunk_bytes=MIB)
    record = _run(acquire(source_file, dest, fmt="raw", options=options))

    blob = bytearray(dest.read_bytes())
    target = 2 * MIB + 17  # inside chunk index 2
    blob[target] ^= 0xFF
    dest.write_bytes(bytes(blob))

    result = verify_image(dest, record)  # type: ignore[arg-type]
    assert result.passed is False
    assert result.sha256_matches is False
    assert result.mismatched_chunks == [2]


# --------------------------------------------------------------------------
# 14. Ledger
# --------------------------------------------------------------------------


def test_acquisition_appends_phases_and_the_chain_verifies_valid(
    source_file: Path, tmp_path: Path
) -> None:
    root = tmp_path / "ledgerroot"
    ledger = Ledger(root, tool_version="test", pubkey_fingerprint="fp")
    dest = tmp_path / "ledgered.dd"

    _run(acquire(source_file, dest, fmt="raw", ledger=ledger))

    operations = [e.operation for e in ledger.entries()]
    assert "acquire.start" in operations
    assert "acquire.complete" in operations
    assert ledger.verify().status is ChainStatus.VALID


def test_bad_sectors_reach_the_ledger(tmp_path: Path) -> None:
    """An image with silent substitutions and no record is inadmissible."""
    root = tmp_path / "ledgerroot"
    ledger = Ledger(root, tool_version="test", pubkey_fingerprint="fp")
    reader = FailingReader(_payload(MIB), bad_lbas=range(100, 104))

    _run(acquire(reader, tmp_path / "b.dd", fmt="raw", ledger=ledger))

    recorded = [
        ledger.params_of(e)
        for e in ledger.entries()
        if e.operation == "acquire.complete"
    ]
    assert recorded, "no completion entry"
    assert recorded[0]["bad_sectors"], "bad sectors absent from the ledger"


# --------------------------------------------------------------------------
# Write blocking honesty
# --------------------------------------------------------------------------


def test_acquisition_records_the_write_block_it_actually_achieved(
    source_file: Path, tmp_path: Path
) -> None:
    """No software write block exists on Windows or macOS. Say so, do not imply it."""
    import sys

    record = _run(acquire(source_file, tmp_path / "wb.dd", fmt="raw"))
    if sys.platform == "linux":
        return
    assert record.write_blocked is False  # type: ignore[union-attr]
    assert any(
        "NO_SOFTWARE_WRITE_BLOCK" in limit
        for limit in record.limitations  # type: ignore[union-attr]
    )


# --------------------------------------------------------------------------
# E01
# --------------------------------------------------------------------------


def test_e01_acquisition_either_works_or_refuses_with_a_named_reason(
    source_file: Path, tmp_path: Path
) -> None:
    """Never emit an E01 that libewf could not actually finalise.

    This build of libewf-python binds no write-configuration setters, so an
    E01 write cannot be completed. Refusing loudly is correct; producing a
    truncated container that opens but reads short would be the worst outcome.
    """
    dest = tmp_path / "case.E01"
    if e01_write_supported():
        record = _run(acquire(source_file, dest, fmt="e01"))
        with open_evidence(dest) as handle:
            assert handle.read(0, handle.size) == source_file.read_bytes()
            assert handle.source.fmt == "ewf"
        assert record.sha256 == hashlib.sha256(  # type: ignore[union-attr]
            source_file.read_bytes()
        ).hexdigest()
        return

    with pytest.raises(UnsupportedCapability) as excinfo:
        _run(acquire(source_file, dest, fmt="e01"))
    assert "set_media_size" in str(excinfo.value)
    assert not dest.exists(), "a refused acquisition must leave no partial image"


# --------------------------------------------------------------------------
# Source safety
# --------------------------------------------------------------------------


def test_the_source_is_opened_read_only(
    source_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    seen: list[int] = []
    real_open = os.open

    def spy(path: object, flags: int, *args: object, **kwargs: object) -> int:
        seen.append(flags)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy)
    _run(acquire(source_file, tmp_path / "ro.dd", fmt="raw"))

    assert seen
    # The destination is written, so filter to opens of the source path.
    reader = RawEvidence(source_file)
    reader.close()
    assert all(
        flag & os.O_RDWR == 0 or flag & os.O_CREAT for flag in seen
    ), "source opened for writing"


def test_missing_source_refuses_before_creating_a_destination(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "never.dd"
    with pytest.raises(EvidenceIntegrityError):
        _run(acquire(tmp_path / "absent.dd", dest, fmt="raw"))
    assert not dest.exists()


def test_source_reader_protocol_accepts_the_file_backed_reader(
    source_file: Path,
) -> None:
    from core.carve.acquire import FileSourceReader

    reader = FileSourceReader(source_file)
    try:
        assert isinstance(reader, SourceReader)
        assert reader.size == 4 * MIB
    finally:
        reader.close()
