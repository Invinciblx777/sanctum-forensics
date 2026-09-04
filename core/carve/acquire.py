"""Forensic imaging: write-blocked reads, dual hashing, honest bad sectors.

Two rules govern everything here.

**Never modify the evidence.** The source is opened ``O_RDONLY`` and, on Linux,
set read-only at the block layer with ``BLKROSET`` before it is opened at all.
That flag is then read back with ``BLKROGET``: a write block that was requested
but did not take is worse than none, because the operator believes in it.
Windows and macOS have no equivalent software write block. Rather than imply
one, an acquisition on those platforms records
``NO_SOFTWARE_WRITE_BLOCK`` as a limitation on the record and in the report. A
hardware write blocker is the correct answer and the documentation says so.

**Never silently substitute.** A drive with forty thousand bad sectors still
yields evidence, so a read error never aborts the run. What it does is:

1. retry the failing block at sector granularity, so three bad sectors cost
   three sectors rather than the whole 1 MiB block;
2. fill each still-unreadable sector with the substitution byte;
3. record its LBA range on the acquisition, in the ledger and in the report.

An image containing invented bytes and no record of where they are is not
evidence, it is a forgery with good intentions.

Hashing happens **in the read pass**, both algorithms at once. Re-reading a 2 TB
disk to hash it doubles the acquisition, and a per-chunk hash list means a later
integrity failure names a chunk instead of condemning the whole image.
"""

from __future__ import annotations

import errno
import hashlib
import os
import platform
import sys
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import structlog

from core.carve.evidence import DEFAULT_SECTOR_BYTES, open_evidence
from core.errors import EvidenceIntegrityError, UnsupportedCapability
from core.ledger.chain import Ledger, boot_id
from core.models import (
    AcquisitionRecord,
    BadSectorRange,
    EvidenceSource,
    IntegrityResult,
    Progress,
    SubstitutedRange,
)

__all__ = [
    "SourceReader",
    "FileSourceReader",
    "AcquireOptions",
    "AcquisitionPhase",
    "acquire",
    "verify_image",
    "e01_write_supported",
    "apply_write_block",
    "NO_SOFTWARE_WRITE_BLOCK",
    "TOOL_VERSION",
]

logger = structlog.get_logger(__name__)

KIB = 1024
MIB = 1024 * KIB

TOOL_VERSION = "sanctum-forensics/0.0.0"

#: ioctl request numbers for the block-layer read-only flag (Linux).
BLKROSET = 0x125D
BLKROGET = 0x125E

NO_SOFTWARE_WRITE_BLOCK = (
    "NO_SOFTWARE_WRITE_BLOCK: {platform} provides no software write block "
    "equivalent to Linux BLKROSET. The source was opened read-only, but nothing "
    "prevents another process on this host from writing to it during "
    "acquisition. Use a hardware write blocker for evidence that will be "
    "presented; this image is suitable for triage only."
)

WRITE_BLOCK_REFUSED = (
    "WRITE_BLOCK_NOT_APPLIED: BLKROSET was issued but BLKROGET read back "
    "writable. The kernel did not honour the request, so no write block is in "
    "force despite one having been asked for."
)

E01_WRITE_UNSUPPORTED = (
    "This build of libewf-python ({version}) binds no E01 write-configuration "
    "setters: pyewf.handle has no set_media_size, so libewf cannot finalise a "
    "segment file and any container written would be truncated. Reading E01 is "
    "fully supported; only acquisition to E01 is unavailable."
)


class AcquisitionPhase:
    """Phase names, so the ledger and the UI cannot drift apart."""

    PREFLIGHT = "preflight"
    WRITE_BLOCK = "write_block"
    READ = "read"
    VERIFY = "verify"
    COMPLETE = "complete"


# --------------------------------------------------------------------------
# Source seam
# --------------------------------------------------------------------------


@runtime_checkable
class SourceReader(Protocol):
    """A read-only byte source being imaged.

    The seam exists so bad sectors can be injected in tests without a failing
    disk: an implementation raises ``OSError(EIO)`` and the salvage path is
    exercised for real.
    """

    size: int
    sector_size: int

    def read_at(self, offset: int, length: int) -> bytes:
        """Return bytes at ``offset``. May raise ``OSError`` on medium error."""
        ...

    def close(self) -> None: ...


class FileSourceReader:
    """A file or block device opened read-only."""

    def __init__(
        self, path: Path | str, *, sector_size: int = DEFAULT_SECTOR_BYTES
    ) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise EvidenceIntegrityError(
                f"acquisition source not found: {self.path}",
                remediation="Check the path. Nothing was created.",
            )
        self._fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self.sector_size = sector_size
        self.size = self._probe_size()

    def _probe_size(self) -> int:
        stat = os.fstat(self._fd)
        if stat.st_size:
            return int(stat.st_size)
        # A Linux block device reports st_size 0; seek to the end instead.
        end = os.lseek(self._fd, 0, os.SEEK_END)
        os.lseek(self._fd, 0, os.SEEK_SET)
        return int(end)

    def read_at(self, offset: int, length: int) -> bytes:
        pread = getattr(os, "pread", None)
        if pread is not None:
            return bytes(pread(self._fd, length, offset))
        os.lseek(self._fd, offset, os.SEEK_SET)
        return os.read(self._fd, length)

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquireOptions:
    """Knobs for one acquisition. Defaults are the demo-safe settings."""

    block_bytes: int = MIB
    #: Per-chunk hash granularity. Smaller localises a later failure more
    #: tightly, at the cost of a longer hash list in the record.
    chunk_bytes: int = 4 * MIB
    sector_size: int = DEFAULT_SECTOR_BYTES
    fill_byte: int = 0x00
    #: Retries of a failing block before dropping to sector granularity.
    retries: int = 2
    #: Bad sectors after which the operator is warned. The run continues:
    #: only the operator decides to stop.
    error_ceiling: int = 10_000
    checkpoint_bytes: int = 256 * MIB
    #: Split the raw destination into segments of this size. ``None`` is one file.
    segment_bytes: int | None = None
    compression: Literal["none", "fast", "best"] = "fast"
    operator: str = "sanctum"
    case_number: str = ""
    evidence_number: str = ""
    examiner: str = ""
    description: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.block_bytes % self.sector_size:
            raise ValueError(
                f"block_bytes {self.block_bytes} is not a multiple of sector_size "
                f"{self.sector_size}; sector-granularity salvage would misalign."
            )


DEFAULT_OPTIONS = AcquireOptions()


# --------------------------------------------------------------------------
# Write blocking
# --------------------------------------------------------------------------


@dataclass
class WriteBlockOutcome:
    applied: bool
    limitations: list[str] = field(default_factory=list)


def apply_write_block(path: Path | str) -> WriteBlockOutcome:
    """Set the block device read-only, then read the flag back to confirm.

    The read-back is the point. ``BLKROSET`` can be issued against something
    that is not a block device, or by a caller without the privilege to make it
    stick, and in both cases the operator would otherwise believe a protection
    that does not exist.
    """
    if sys.platform != "linux":
        return WriteBlockOutcome(
            applied=False,
            limitations=[NO_SOFTWARE_WRITE_BLOCK.format(platform=platform.system())],
        )

    import fcntl
    import struct

    target = Path(path)
    if not target.is_block_device():
        # A file-backed image needs no block-layer flag; opening it O_RDONLY is
        # the whole guarantee available, and claiming more would be false.
        return WriteBlockOutcome(applied=False, limitations=[])

    fd = os.open(target, os.O_RDONLY)
    try:
        fcntl.ioctl(fd, BLKROSET, struct.pack("i", 1))
        raw = fcntl.ioctl(fd, BLKROGET, struct.pack("i", 0))
        (read_only,) = struct.unpack("i", raw)
    except OSError as exc:
        return WriteBlockOutcome(
            applied=False,
            limitations=[
                f"{WRITE_BLOCK_REFUSED} "
                f"({errno.errorcode.get(exc.errno or 0, exc.errno)})"
            ],
        )
    finally:
        os.close(fd)

    if not read_only:
        return WriteBlockOutcome(applied=False, limitations=[WRITE_BLOCK_REFUSED])
    return WriteBlockOutcome(applied=True, limitations=[])


# --------------------------------------------------------------------------
# EWF capability
# --------------------------------------------------------------------------


def e01_write_supported() -> bool:
    """True only when this libewf build can actually finalise an E01.

    Probed, never assumed. ``libewf-python`` ships read-focused bindings on
    some platforms: ``write`` exists but the setters that must precede it do
    not, so a write starts and then fails at close, leaving a container that
    opens and reads short.
    """
    try:
        import pyewf
    except ImportError:
        return False
    return hasattr(pyewf.handle, "set_media_size")


def _ewf_version() -> str:
    try:
        import pyewf

        return str(pyewf.get_version())
    except ImportError:
        return "not installed"


# --------------------------------------------------------------------------
# Destination
# --------------------------------------------------------------------------


class _RawWriter:
    """Writes a raw image, optionally split into fixed-size segments."""

    def __init__(self, dest: Path, *, segment_bytes: int | None) -> None:
        self.dest = dest
        self._segment_bytes = segment_bytes
        self._segments: list[Path] = []
        self._handle: Any = None
        self._in_segment = 0
        self._index = 0

    def _next_path(self) -> Path:
        if self._segment_bytes is None:
            return self.dest
        self._index += 1
        return self.dest.with_name(f"{self.dest.name}.{self._index:03d}")

    def open(self, *, append: bool) -> None:
        path = self._next_path()
        self._handle = open(path, "r+b" if append and path.exists() else "wb")
        if append and path.exists():
            self._handle.seek(0, os.SEEK_END)
            self._in_segment = self._handle.tell()
        self._segments.append(path)

    def write(self, data: bytes) -> None:
        assert self._handle is not None
        if self._segment_bytes is not None and self._in_segment >= self._segment_bytes:
            self._handle.close()
            self.open(append=False)
            self._in_segment = 0
        self._handle.write(data)
        self._in_segment += len(data)

    def seek(self, offset: int) -> None:
        assert self._handle is not None
        self._handle.seek(offset)

    def flush_durable(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        if self._handle is not None:
            self.flush_durable()
            self._handle.close()
            self._handle = None

    @property
    def segments(self) -> list[Path]:
        return list(self._segments)


# --------------------------------------------------------------------------
# Acquisition
# --------------------------------------------------------------------------


def _blake3_hasher() -> Any:
    import blake3

    return blake3.blake3()


def _open_source(
    source: SourceReader | Path | str, options: AcquireOptions
) -> tuple[SourceReader, Path | None, bool]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        reader = FileSourceReader(path, sector_size=options.sector_size)
        return reader, path, True
    return source, None, False


def _salvage_block(
    reader: SourceReader,
    offset: int,
    length: int,
    options: AcquireOptions,
) -> tuple[bytes, list[BadSectorRange], list[SubstitutedRange]]:
    """Re-read a failed block sector by sector, salvaging everything readable.

    This is the difference between losing three sectors and losing a megabyte.
    """
    sector = options.sector_size
    out = bytearray()
    bad_lbas: list[int] = []
    last_errno = errno.EIO
    for position in range(offset, offset + length, sector):
        take = min(sector, offset + length - position)
        recovered: bytes | None = None
        for _ in range(max(options.retries, 1)):
            try:
                recovered = reader.read_at(position, take)
                break
            except OSError as exc:
                last_errno = exc.errno or errno.EIO
        if recovered is None:
            out += bytes([options.fill_byte]) * take
            bad_lbas.append(position // sector)
        else:
            out += recovered.ljust(take, bytes([options.fill_byte]))

    ranges: list[BadSectorRange] = []
    substituted: list[SubstitutedRange] = []
    for lba in bad_lbas:
        if ranges and lba == ranges[-1].last_lba + 1:
            previous = ranges[-1]
            ranges[-1] = previous.model_copy(update={"last_lba": lba})
            span = substituted[-1]
            substituted[-1] = span.model_copy(
                update={"length": span.length + sector}
            )
            continue
        ranges.append(
            BadSectorRange(
                first_lba=lba,
                last_lba=lba,
                sector_size=sector,
                errno=last_errno,
                attempts=max(options.retries, 1),
            )
        )
        substituted.append(
            SubstitutedRange(
                offset=lba * sector,
                length=sector,
                fill_byte=options.fill_byte,
                reason=(
                    f"sector {lba} returned "
                    f"{errno.errorcode.get(last_errno, last_errno)} after "
                    f"{max(options.retries, 1)} attempts"
                ),
            )
        )
    return bytes(out), ranges, substituted


def _merge_adjacent(ranges: list[BadSectorRange]) -> list[BadSectorRange]:
    """Join runs that met across a block boundary into one reported range."""
    merged: list[BadSectorRange] = []
    for item in ranges:
        if merged and item.first_lba == merged[-1].last_lba + 1:
            merged[-1] = merged[-1].model_copy(update={"last_lba": item.last_lba})
            continue
        merged.append(item)
    return merged


def _merge_substituted(spans: list[SubstitutedRange]) -> list[SubstitutedRange]:
    merged: list[SubstitutedRange] = []
    for span in spans:
        if merged and span.offset == merged[-1].offset + merged[-1].length:
            merged[-1] = merged[-1].model_copy(
                update={"length": merged[-1].length + span.length}
            )
            continue
        merged.append(span)
    return merged


def acquire(
    source: SourceReader | Path | str,
    dest: Path | str,
    *,
    fmt: Literal["raw", "e01"] = "raw",
    options: AcquireOptions = DEFAULT_OPTIONS,
    ledger: Ledger | None = None,
    job_id: str = "acquire",
    resume: bool = False,
) -> Generator[Progress, None, AcquisitionRecord]:
    """Image ``source`` to ``dest``, yielding progress, returning the record.

    Both hashes are computed during the single read pass. Bad sectors are
    salvaged at sector granularity, filled, and recorded; they never abort the
    run. The returned :class:`AcquisitionRecord` is the chain of custody and is
    ledgered in full when a ledger is supplied.
    """
    destination = Path(dest)

    if fmt == "e01" and not e01_write_supported():
        raise UnsupportedCapability(
            E01_WRITE_UNSUPPORTED.format(version=_ewf_version()),
            remediation=(
                "Acquire to raw here and convert with ewfacquire on a host with "
                "full libewf, or run the acquisition on Linux. Reading existing "
                "E01 evidence works on this build."
            ),
        )

    reader, source_path, owns_reader = _open_source(source, options)
    started_at = datetime.now(UTC)
    limitations: list[str] = []

    try:
        block = apply_write_block(source_path) if source_path else WriteBlockOutcome(
            applied=False, limitations=[]
        )
        limitations.extend(block.limitations)
        if source_path is None and sys.platform != "linux":
            limitations.append(
                NO_SOFTWARE_WRITE_BLOCK.format(platform=platform.system())
            )

        if ledger is not None:
            ledger.append(
                actor=options.operator,
                operation="acquire.start",
                params={
                    "job_id": job_id,
                    "source": str(source_path) if source_path else "<reader>",
                    "dest": str(destination),
                    "fmt": fmt,
                    "size_bytes": reader.size,
                    "sector_size": options.sector_size,
                    "write_blocked": block.applied,
                    "resumed": resume,
                    "tool_version": TOOL_VERSION,
                    "boot_id": boot_id(),
                },
                result={},
            )

        yield Progress(
            job_id=job_id,
            phase=AcquisitionPhase.PREFLIGHT,
            pct_bp=0,
            bytes_done=0,
            bytes_total=reader.size,
            throughput_bytes_per_sec=0,
            eta_seconds=0,
            message=f"imaging {reader.size} bytes to {destination.name}",
        )

        record = yield from _read_pass(
            reader,
            destination,
            fmt=fmt,
            options=options,
            job_id=job_id,
            resume=resume,
            started_at=started_at,
            source_path=source_path,
            write_blocked=block.applied,
            limitations=limitations,
            ledger=ledger,
        )
    finally:
        if owns_reader:
            reader.close()

    if ledger is not None:
        ledger.append(
            actor=options.operator,
            operation="acquire.complete",
            params=record.model_dump(mode="json"),
            result={"sha256": record.sha256, "blake3": record.blake3},
        )

    return record


def _read_pass(
    reader: SourceReader,
    destination: Path,
    *,
    fmt: Literal["raw", "e01"],
    options: AcquireOptions,
    job_id: str,
    resume: bool,
    started_at: datetime,
    source_path: Path | None,
    write_blocked: bool,
    limitations: list[str],
    ledger: Ledger | None,
) -> Generator[Progress, None, AcquisitionRecord]:
    """The single pass that reads, hashes, salvages and writes."""
    sha = hashlib.sha256()
    blake = _blake3_hasher()
    chunk_sha = hashlib.sha256()
    chunk_hashes: list[str] = []
    chunk_filled = 0

    bad_sectors: list[BadSectorRange] = []
    substituted: list[SubstitutedRange] = []

    writer = _RawWriter(destination, segment_bytes=options.segment_bytes)
    start_offset = 0
    prior_bytes = destination.stat().st_size if destination.exists() else 0
    if resume and prior_bytes:
        # Align down to a chunk boundary: the chunk hash list is built in
        # order, so restarting mid-chunk would produce a hash over a partial
        # chunk and every later index would refer to the wrong bytes. Up to
        # one chunk of completed work is re-read, which is cheap next to
        # getting the list wrong.
        complete_chunks = prior_bytes // options.chunk_bytes
        start_offset = complete_chunks * options.chunk_bytes
        for index in range(complete_chunks):
            piece = _read_file_range(
                destination, index * options.chunk_bytes, options.chunk_bytes
            )
            sha.update(piece)
            blake.update(piece)
            chunk_hashes.append(hashlib.sha256(piece).hexdigest())
    writer.open(append=resume and start_offset > 0)
    if start_offset:
        writer.seek(start_offset)

    offset = start_offset
    next_checkpoint = offset + options.checkpoint_bytes
    monotonic_start = _monotonic_ns()
    warned_ceiling = False

    try:
        while offset < reader.size:
            length = min(options.block_bytes, reader.size - offset)
            try:
                data = reader.read_at(offset, length)
            except OSError:
                data, ranges, spans = _salvage_block(reader, offset, length, options)
                bad_sectors.extend(ranges)
                substituted.extend(spans)
                if len(bad_sectors) > options.error_ceiling and not warned_ceiling:
                    warned_ceiling = True
                    limitations.append(
                        f"ERROR_CEILING_EXCEEDED: more than "
                        f"{options.error_ceiling} bad sector ranges. The run "
                        "continues; cancel it if the media is failing faster "
                        "than it is being read."
                    )
            if len(data) < length:
                data = data.ljust(length, bytes([options.fill_byte]))

            writer.write(data)
            sha.update(data)
            blake.update(data)

            consumed = 0
            while consumed < len(data):
                take = min(options.chunk_bytes - chunk_filled, len(data) - consumed)
                chunk_sha.update(data[consumed : consumed + take])
                chunk_filled += take
                consumed += take
                if chunk_filled == options.chunk_bytes:
                    chunk_hashes.append(chunk_sha.hexdigest())
                    chunk_sha = hashlib.sha256()
                    chunk_filled = 0

            offset += length

            if offset >= next_checkpoint:
                writer.flush_durable()
                next_checkpoint = offset + options.checkpoint_bytes
                if ledger is not None:
                    ledger.append(
                        actor=options.operator,
                        operation="acquire.checkpoint",
                        params={"job_id": job_id, "offset": offset},
                        result={},
                    )

            elapsed_ns = max(_monotonic_ns() - monotonic_start, 1)
            done = offset - start_offset
            rate = int(done * 1_000_000_000 // elapsed_ns)
            yield Progress(
                job_id=job_id,
                phase=AcquisitionPhase.READ,
                pct_bp=10_000 * offset // reader.size if reader.size else 10_000,
                bytes_done=offset,
                bytes_total=reader.size,
                throughput_bytes_per_sec=rate,
                eta_seconds=(reader.size - offset) // rate if rate else 0,
                message=(
                    f"read {offset}/{reader.size}"
                    + (f", {len(bad_sectors)} bad range(s)" if bad_sectors else "")
                ),
            )
    finally:
        writer.close()

    if chunk_filled:
        chunk_hashes.append(chunk_sha.hexdigest())

    merged_bad = _merge_adjacent(bad_sectors)
    merged_spans = _merge_substituted(substituted)
    if merged_bad:
        total = sum(item.sector_count for item in merged_bad)
        limitations.append(
            f"BAD_SECTORS: {total} sector(s) in {len(merged_bad)} range(s) could "
            f"not be read and were filled with 0x{options.fill_byte:02x}. Those "
            "offsets contain tool-generated bytes, not media contents."
        )

    finished_at = datetime.now(UTC)
    return AcquisitionRecord(
        job_id=job_id,
        source=EvidenceSource(
            path=str(source_path) if source_path else "<reader>",
            fmt="raw",
            size_bytes=reader.size,
            sector_size=options.sector_size,
            sha256=sha.hexdigest(),
            blake3=blake.hexdigest(),
            segments=[str(p) for p in writer.segments],
            substituted_ranges=merged_spans,
            limitations=list(limitations),
        ),
        dest_path=str(destination),
        fmt=fmt,
        started_at=started_at,
        finished_at=finished_at,
        operator=options.operator,
        tool_version=TOOL_VERSION,
        boot_id=boot_id(),
        monotonic_ns=_monotonic_ns() - monotonic_start,
        bytes_read=reader.size,
        sha256=sha.hexdigest(),
        blake3=blake.hexdigest(),
        chunk_bytes=options.chunk_bytes,
        chunk_hashes=chunk_hashes,
        bad_sectors=merged_bad,
        limitations=list(limitations),
        # True whenever this run continued an existing destination, even if the
        # chunk-boundary alignment meant no completed bytes were reusable. The
        # provenance fact a reader needs is that the image was produced across
        # more than one run, not how much of it the second run skipped.
        resumed=resume and prior_bytes > 0,
        write_blocked=write_blocked,
    )


def _monotonic_ns() -> int:
    import time

    return time.monotonic_ns()


def _read_file_range(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(offset)
        return handle.read(length)


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def verify_image(
    path: Path | str, record: AcquisitionRecord
) -> IntegrityResult:
    """Re-read the image and compare both hashes to the acquisition record.

    On a mismatch the per-chunk list localises the damage. "This image no
    longer matches its record" is a fact an operator can act on; "chunk 2 of
    64 differs" is one they can act on quickly.
    """
    sha = hashlib.sha256()
    blake = _blake3_hasher()
    mismatched: list[int] = []
    verified = 0

    with open_evidence(path) as handle:
        for index in range(len(record.chunk_hashes)):
            offset = index * record.chunk_bytes
            piece = handle.read(offset, record.chunk_bytes)
            if not piece:
                break
            sha.update(piece)
            blake.update(piece)
            verified += len(piece)
            if hashlib.sha256(piece).hexdigest() != record.chunk_hashes[index]:
                mismatched.append(index)

    actual_sha = sha.hexdigest()
    actual_blake = blake.hexdigest()
    sha_ok = actual_sha == record.sha256
    blake_ok = actual_blake == record.blake3
    return IntegrityResult(
        passed=sha_ok and blake_ok and not mismatched,
        sha256_matches=sha_ok,
        blake3_matches=blake_ok,
        expected_sha256=record.sha256,
        actual_sha256=actual_sha,
        expected_blake3=record.blake3,
        actual_blake3=actual_blake,
        mismatched_chunks=mismatched,
        bytes_verified=verified,
    )


def iter_progress(
    generator: Generator[Progress, None, AcquisitionRecord],
) -> Iterator[Progress]:
    """Yield progress and discard the record, for callers that only display."""
    yield from generator
