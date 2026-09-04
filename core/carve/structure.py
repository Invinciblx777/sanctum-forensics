"""Structure-aware carving: derive the length, do not guess it.

A signature carver finds a header and then guesses where the object ends -
usually by searching for a footer, which fails whenever the footer byte
sequence also occurs inside the object. A JPEG's ``FFD9`` appears at the end of
its own EXIF thumbnail; a PDF holds one ``%%EOF`` per incremental update. The
guess produces a file that is a few hundred bytes wrong, and a file that is a
few hundred bytes wrong does not open.

Every parser here walks the format's own length fields instead, so the answer
is exact or the parser declines. That is the difference between this and
photorec, and it is what makes a recovered object presentable rather than
merely plausible.

**Every parser is bounded, in three ways at once**, because these length fields
come from a disk that may be damaged or hostile:

* a byte cap - never read past the signature's ``max_size``;
* an iteration cap - :data:`MAX_ITERATIONS`, so a zero-length or backward
  "next" pointer cannot spin forever;
* a wall-clock deadline - :data:`PARSE_DEADLINE_S` per object.

A malformed box size of ``0xFFFFFFFF`` must cost microseconds and return
nothing, not attempt a four-gigabyte read.
"""

from __future__ import annotations

import re
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import structlog

from core.carve.evidence import EvidenceHandle
from core.carve.signature import Signature, load_signatures, scan
from core.models import CarveCandidate

__all__ = [
    "ParsedObject",
    "MAX_ITERATIONS",
    "PARSE_DEADLINE_S",
    "parse_zip",
    "parse_pdf",
    "parse_jpeg",
    "parse_sqlite",
    "parse_png",
    "parse_mp4",
    "PARSERS",
    "carve_structures",
]

logger = structlog.get_logger(__name__)

MIB = 1024 * 1024

#: Cap on any parser's walk. Chosen well above a legitimate object's structure
#: count (a ZIP with 100k entries, a PNG with 100k chunks) and far below a
#: number that costs noticeable time.
MAX_ITERATIONS = 200_000

#: Wall-clock budget for parsing one object. A damaged image can present
#: pathological structure that is bounded in theory and slow in practice.
PARSE_DEADLINE_S = 5.0

Validation = Literal["valid", "truncated", "corrupt"]


@dataclass(frozen=True)
class ParsedObject:
    """A length derived from the format's own structure."""

    length: int
    validation: Validation
    #: What the parser learned that a footer search could not, for the report.
    detail: str = ""


class _Budget:
    """Iteration and time bound shared by every parser."""

    def __init__(self, deadline_s: float = PARSE_DEADLINE_S) -> None:
        self._started = time.monotonic()
        self._deadline = deadline_s
        self._steps = 0

    def step(self) -> bool:
        """False once the walk must stop. Callers return what they have."""
        self._steps += 1
        if self._steps > MAX_ITERATIONS:
            return False
        if self._steps % 256 == 0:
            return time.monotonic() - self._started < self._deadline
        return True


def _read(handle: EvidenceHandle, offset: int, length: int, cap: int) -> bytes:
    """Read, never crossing the object's byte cap."""
    if offset >= cap:
        return b""
    return handle.read(offset, min(length, cap - offset))


# --------------------------------------------------------------------------
# ZIP
# --------------------------------------------------------------------------

_EOCD = b"PK\x05\x06"
_EOCD64_LOCATOR = b"PK\x06\x07"


def parse_zip(
    handle: EvidenceHandle, start: int, *, max_size: int
) -> ParsedObject | None:
    """Walk local file headers to the end-of-central-directory record.

    The EOCD carries the central directory's offset and size, and its own
    position plus its own length is the archive's exact end. That is why a ZIP
    can be carved exactly while a footer search cannot: the EOCD signature also
    occurs inside compressed data.

    The *first* consistent record wins, not the last one inside ``max_size``.
    An unallocated region holds many archives, and a ZIP's cap is a gigabyte,
    so "last record in the window" hands one archive an end address belonging
    to another one megabytes away. Consistency is what settles it: the central
    directory this record describes must start at ``PK\x01\x02`` and must end
    exactly where the record itself begins.
    """
    cap = min(start + max_size, handle.size)
    budget = _Budget()
    window = 1 * MIB
    cursor = start
    end_of_archive: int | None = None

    while cursor < cap and budget.step() and end_of_archive is None:
        block = _read(handle, cursor, window, cap)
        if not block:
            break
        position = 0
        while True:
            found = block.find(_EOCD, position)
            if found == -1:
                break
            absolute = cursor + found
            trailer = _read(handle, absolute, 22, cap)
            if len(trailer) == 22:
                comment_length = int.from_bytes(trailer[20:22], "little")
                end = absolute + 22 + comment_length
                cd_size = int.from_bytes(trailer[12:16], "little")
                cd_offset = int.from_bytes(trailer[16:20], "little")
                cd_start = start + cd_offset
                if (
                    end <= cap
                    and cd_start + cd_size == absolute
                    and (
                        cd_size == 0
                        or _read(handle, cd_start, 4, cap) == b"PK\x01\x02"
                    )
                ):
                    end_of_archive = end
                    break
            position = found + 1
        cursor += max(len(block) - len(_EOCD) - 1, 1)

    if end_of_archive is None:
        return ParsedObject(
            length=min(max_size, handle.size - start),
            validation="truncated",
            detail="no end-of-central-directory record within max_size",
        )
    return ParsedObject(
        length=end_of_archive - start,
        validation="valid",
        detail="length from the end-of-central-directory record",
    )


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def parse_pdf(
    handle: EvidenceHandle, start: int, *, max_size: int
) -> ParsedObject | None:
    """Take the last ``%%EOF`` that a ``startxref`` corroborates.

    An incrementally updated PDF holds one ``%%EOF`` per revision, and every
    earlier one is a valid-looking but wrong end. Following ``startxref`` from
    each marker confirms which revisions actually belong to *this* document,
    and incidentally proves earlier revisions are still present - a fact the
    report states, because those revisions can hold content the author believed
    deleted.

    Corroboration is what stops the walk running into the next PDF in
    unallocated space. That document's ``startxref`` holds an offset relative
    to *its* first byte, so measured from this object's start it points at
    filler rather than at an ``xref`` keyword or an object header, and the
    marker is rejected.
    """
    cap = min(start + max_size, handle.size)
    budget = _Budget()
    window = 1 * MIB
    cursor = start
    accepted: list[int] = []
    rejected = 0

    while cursor < cap and budget.step() and rejected == 0:
        block = _read(handle, cursor, window, cap)
        if not block:
            break
        position = 0
        while True:
            found = block.find(b"%%EOF", position)
            if found == -1:
                break
            marker = cursor + found
            contiguous = not accepted or _revision_follows(
                handle, accepted[-1], marker, cap
            )
            if contiguous and _startxref_corroborates(handle, start, marker, cap):
                accepted.append(marker + 5)
            elif accepted:
                # A marker this object cannot account for is where this object
                # ends. Anything past it belongs to something else.
                rejected += 1
                break
            position = found + 1
        if rejected:
            break
        cursor += max(len(block) - 5, 1)

    if not accepted:
        return ParsedObject(
            length=min(max_size, handle.size - start),
            validation="truncated",
            detail="no %%EOF marker with a corroborating startxref within max_size",
        )

    end = accepted[-1]
    # Consume a trailing newline so the length matches the file on disk.
    tail = _read(handle, end, 2, cap)
    if tail.startswith(b"\r\n"):
        end += 2
    elif tail[:1] in (b"\n", b"\r"):
        end += 1

    detail = "length from the final corroborated %%EOF"
    if len(accepted) > 1:
        detail += (
            f"; {len(accepted) - 1} earlier revision(s) present, which may retain "
            "content removed in later ones"
        )
    return ParsedObject(length=end - start, validation="valid", detail=detail)


#: What the first bytes of a PDF revision look like: a comment, an object
#: header, a cross-reference table or a trailer.
_REVISION_START = re.compile(
    rb"^[ \t\r\n]*(%|[0-9]+[ \t\r\n]+[0-9]+[ \t\r\n]+obj|xref|trailer)"
)


def _revision_follows(
    handle: EvidenceHandle, previous_end: int, marker: int, cap: int
) -> bool:
    """Whether the bytes after the previous ``%%EOF`` continue the same document.

    An incremental update begins immediately after the revision it updates.
    Random unallocated bytes between one ``%%EOF`` and the next mean the second
    marker belongs to a different file that happens to lie further along the
    image - which is exactly the case that hands one PDF an end address five
    megabytes away.
    """
    if marker <= previous_end:
        return False
    return bool(_REVISION_START.match(_read(handle, previous_end, 24, cap)))


#: How far back from a ``%%EOF`` the ``startxref`` keyword and its operand sit.
#: The trailer between them is short by construction.
_STARTXREF_LOOKBACK = 128

_XREF_OBJECT = re.compile(rb"^\d+\s+\d+\s+obj")


def _startxref_corroborates(
    handle: EvidenceHandle, start: int, marker: int, cap: int
) -> bool:
    """Whether the ``startxref`` before ``marker`` points into this object.

    The offset is relative to the first byte of the document, which is exactly
    what makes it useful here: measured from a *different* document's start it
    lands on nothing.
    """
    look_from = max(start, marker - _STARTXREF_LOOKBACK)
    tail = _read(handle, look_from, marker - look_from, cap)
    keyword = tail.rfind(b"startxref")
    if keyword == -1:
        return False
    digits = tail[keyword + len(b"startxref") :].strip()
    number = digits.split(b"%")[0].strip()
    if not number.isdigit():
        return False
    target = start + int(number)
    if not start <= target < marker:
        return False
    at_target = _read(handle, target, 24, cap)
    return at_target.startswith(b"xref") or bool(_XREF_OBJECT.match(at_target))


# --------------------------------------------------------------------------
# JPEG
# --------------------------------------------------------------------------


def parse_jpeg(
    handle: EvidenceHandle, start: int, *, max_size: int
) -> ParsedObject | None:
    """Walk SOI, segment markers, SOS, entropy-coded data, EOI.

    Walking segments is what lets truncation be *detected* rather than assumed:
    reaching the cap without an EOI means the object really is short, and the
    thumbnail's own ``FFD9`` never terminates the walk because it lives inside
    an APP1 segment whose declared length steps straight over it.
    """
    cap = min(start + max_size, handle.size)
    budget = _Budget()
    header = _read(handle, start, 2, cap)
    if header != b"\xff\xd8":
        return None

    cursor = start + 2
    while cursor < cap and budget.step():
        marker = _read(handle, cursor, 2, cap)
        if len(marker) < 2:
            return ParsedObject(
                length=cap - start, validation="truncated", detail="ran out of data"
            )
        if marker[0] != 0xFF:
            return ParsedObject(
                length=cursor - start,
                validation="corrupt",
                detail=f"expected a marker at {cursor}, found {marker[0]:#04x}",
            )
        kind = marker[1]
        if kind == 0xD9:  # EOI
            return ParsedObject(
                length=cursor + 2 - start,
                validation="valid",
                detail="length from the EOI marker after a full segment walk",
            )
        if kind in (0x01,) or 0xD0 <= kind <= 0xD8:
            cursor += 2  # standalone markers carry no length
            continue

        size_bytes = _read(handle, cursor + 2, 2, cap)
        if len(size_bytes) < 2:
            return ParsedObject(
                length=cap - start, validation="truncated", detail="segment truncated"
            )
        segment_length = int.from_bytes(size_bytes, "big")
        if segment_length < 2:
            return ParsedObject(
                length=cursor - start,
                validation="corrupt",
                detail=f"segment length {segment_length} at {cursor} is impossible",
            )

        if kind == 0xDA:  # SOS: entropy-coded data follows, scan for EOI
            cursor += 2 + segment_length
            return _scan_entropy_to_eoi(handle, start, cursor, cap, budget)
        cursor += 2 + segment_length

    return ParsedObject(
        length=cap - start, validation="truncated", detail="no EOI within max_size"
    )


def _scan_entropy_to_eoi(
    handle: EvidenceHandle,
    start: int,
    cursor: int,
    cap: int,
    budget: _Budget,
) -> ParsedObject:
    """Find EOI past the scan header, skipping stuffed bytes and RST markers."""
    window = 64 * 1024
    position = cursor
    while position < cap and budget.step():
        block = _read(handle, position, window, cap)
        if not block:
            break
        index = 0
        while index < len(block) - 1:
            if block[index] != 0xFF:
                index += 1
                continue
            following = block[index + 1]
            # 0x00 is a stuffed byte, 0xD0-0xD7 are restart markers: both are
            # part of the entropy stream, not the end of it.
            if following == 0x00 or 0xD0 <= following <= 0xD7:
                index += 2
                continue
            if following == 0xD9:
                return ParsedObject(
                    length=position + index + 2 - start,
                    validation="valid",
                    detail="length from the EOI marker after a full segment walk",
                )
            index += 1
        position += max(len(block) - 1, 1)
    return ParsedObject(
        length=cap - start,
        validation="truncated",
        detail="entropy-coded data ran to max_size with no EOI",
    )


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------


def parse_sqlite(
    handle: EvidenceHandle, start: int, *, max_size: int
) -> ParsedObject | None:
    """page_size x page_count, both read straight from the 100-byte header."""
    cap = min(start + max_size, handle.size)
    header = _read(handle, start, 32, cap)
    if len(header) < 32 or not header.startswith(b"SQLite format 3\x00"):
        return None

    raw_page_size = int.from_bytes(header[16:18], "big")
    # 1 is the format's escape for 65536, which does not fit the 16-bit field.
    page_size = 65536 if raw_page_size == 1 else raw_page_size
    if page_size < 512 or (page_size & (page_size - 1)):
        return ParsedObject(
            length=min(max_size, handle.size - start),
            validation="corrupt",
            detail=f"page size {page_size} is not a power of two >= 512",
        )

    page_count = int.from_bytes(header[28:32], "big")
    if page_count == 0:
        return ParsedObject(
            length=min(max_size, handle.size - start),
            validation="truncated",
            detail="page count is zero; the header predates any commit",
        )

    length = page_size * page_count
    if length > max_size or start + length > handle.size:
        return ParsedObject(
            length=min(max_size, handle.size - start),
            validation="truncated",
            detail=(
                f"header declares {length} bytes "
                f"({page_size} x {page_count}), more than remains"
            ),
        )
    return ParsedObject(
        length=length,
        validation="valid",
        detail=f"length from page_size {page_size} x page_count {page_count}",
    )


# --------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def parse_png(
    handle: EvidenceHandle, start: int, *, max_size: int
) -> ParsedObject | None:
    """Walk length-prefixed chunks to IEND, verifying each chunk's CRC32.

    The CRC check is the reason this reports ``corrupt`` rather than ``valid``
    for damaged data. A carver that only checks the IEND marker hands back a
    file that opens to a grey smear, and calls it recovered.
    """
    cap = min(start + max_size, handle.size)
    budget = _Budget()
    if _read(handle, start, 8, cap) != _PNG_MAGIC:
        return None

    cursor = start + 8
    saw_ihdr = False
    while cursor < cap and budget.step():
        head = _read(handle, cursor, 8, cap)
        if len(head) < 8:
            return ParsedObject(
                length=cap - start, validation="truncated", detail="chunk header short"
            )
        length = int.from_bytes(head[0:4], "big")
        kind = head[4:8]
        if length > max_size:
            return ParsedObject(
                length=cursor - start,
                validation="corrupt",
                detail=f"chunk {kind!r} declares {length} bytes",
            )
        payload = _read(handle, cursor + 8, length, cap)
        crc_bytes = _read(handle, cursor + 8 + length, 4, cap)
        if len(payload) < length or len(crc_bytes) < 4:
            return ParsedObject(
                length=cap - start,
                validation="truncated",
                detail=f"chunk {kind!r} runs past the available data",
            )
        expected = int.from_bytes(crc_bytes, "big")
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected:
            return ParsedObject(
                length=cursor + 12 + length - start,
                validation="corrupt",
                detail=f"CRC mismatch in chunk {kind.decode('latin-1')}",
            )
        if kind == b"IHDR":
            saw_ihdr = True
        cursor += 12 + length
        if kind == b"IEND":
            return ParsedObject(
                length=cursor - start,
                validation="valid" if saw_ihdr else "corrupt",
                detail="length from the IEND chunk, every CRC verified",
            )

    return ParsedObject(
        length=cap - start, validation="truncated", detail="no IEND within max_size"
    )


# --------------------------------------------------------------------------
# MP4
# --------------------------------------------------------------------------


def parse_mp4(
    handle: EvidenceHandle, start: int, *, max_size: int
) -> ParsedObject | None:
    """Walk top-level boxes by their size field, honouring the 64-bit escape.

    ``size == 1`` means the real size follows as a 64-bit ``largesize``;
    ``size == 0`` means "to end of file" and may only appear last. Both are
    common sources of runaway reads in naive parsers, along with a corrupt
    ``0xFFFFFFFF``: every one of them is bounded here rather than trusted.
    """
    cap = min(start + max_size, handle.size)
    budget = _Budget()
    cursor = start
    saw_ftyp = False

    while cursor < cap and budget.step():
        head = _read(handle, cursor, 8, cap)
        if len(head) < 8:
            break
        size = int.from_bytes(head[0:4], "big")
        kind = head[4:8]
        header_bytes = 8

        if size == 1:
            large = _read(handle, cursor + 8, 8, cap)
            if len(large) < 8:
                return ParsedObject(
                    length=cursor - start,
                    validation="truncated",
                    detail="64-bit box size is itself truncated",
                )
            size = int.from_bytes(large, "big")
            header_bytes = 16
        elif size == 0:
            # Legal only as the final box: it means "to end of file".
            return ParsedObject(
                length=cap - start,
                validation="truncated",
                detail=f"box {kind!r} extends to end of data",
            )

        if size < header_bytes or cursor + size > cap:
            return ParsedObject(
                length=cursor - start if saw_ftyp else 0,
                validation="corrupt",
                detail=(
                    f"box {kind!r} at {cursor} declares {size} bytes, which "
                    f"exceeds the {cap - start} byte bound"
                ),
            )
        if kind == b"ftyp":
            saw_ftyp = True
        cursor += size

    if not saw_ftyp:
        return None
    return ParsedObject(
        length=cursor - start,
        validation="valid",
        detail="length from walking the top-level box sizes",
    )


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

#: Signature name -> parser. A format absent here falls back to the signature
#: carver's footer bound, and its candidate says ``source="signature"``.
PARSERS = {
    "ZIP": parse_zip,
    "PDF": parse_pdf,
    "JPEG": parse_jpeg,
    "SQLite": parse_sqlite,
    "PNG": parse_png,
    "MP4": parse_mp4,
}

#: Formats for which bifragment reassembly is attempted. Deliberately one.
_FRAGMENT_CAPABLE = {"JPEG"}


def _signature_by_ext(signatures: list[Signature]) -> dict[str, Signature]:
    return {signature.ext: signature for signature in signatures}


def carve_structures(
    image: EvidenceHandle, *, attempt_reassembly: bool = True
) -> Iterator[CarveCandidate]:
    """Locate objects, then derive each one's length by parsing it.

    Candidates a parser resolved carry ``source="structure"``. Candidates whose
    format has no parser, or whose parse declined, keep ``source="signature"``
    and the footer-derived bound.
    """
    signatures = load_signatures()
    by_ext = _signature_by_ext(signatures)
    by_name = {signature.name: signature for signature in signatures}
    report = scan(image, signatures=signatures)

    for candidate in report.candidates:
        signature = by_ext.get(candidate.ext)
        name = signature.name if signature else ""
        # GIF87a/GIF89a share an ext; the parser table is keyed by name.
        parser = PARSERS.get(name) or next(
            (PARSERS[key] for key in PARSERS if by_name[key].ext == candidate.ext),
            None,
        )
        if parser is None or signature is None:
            yield candidate
            continue

        parsed = parser(image, candidate.offset, max_size=signature.max_size)
        if parsed is None:
            yield candidate
            continue

        fragmented = parsed.validation != "valid"
        length = parsed.length

        if (
            attempt_reassembly
            and fragmented
            and name in _FRAGMENT_CAPABLE
        ):
            from core.carve.fragmentation import reassemble_bifragmented_jpeg

            rebuilt = reassemble_bifragmented_jpeg(
                image, candidate.offset, max_size=signature.max_size
            )
            if rebuilt is not None:
                import hashlib

                yield candidate.model_copy(
                    update={
                        "length": len(rebuilt),
                        "source": "structure",
                        "validation": "valid",
                        "confidence_bp": 7500,
                        "bucket": "MEDIUM",
                        "sha256": hashlib.sha256(rebuilt).hexdigest(),
                        "possibly_fragmented": True,
                    }
                )
                continue

        yield candidate.model_copy(
            update={
                "length": length,
                "source": "structure",
                "validation": parsed.validation,
                "confidence_bp": 9500 if parsed.validation == "valid" else 4000,
                "bucket": "HIGH" if parsed.validation == "valid" else "LOW",
                "sha256": _hash_range(image, candidate.offset, length),
                "possibly_fragmented": fragmented,
            }
        )


def _hash_range(handle: EvidenceHandle, offset: int, length: int) -> str:
    import hashlib

    digest = hashlib.sha256()
    cursor = offset
    remaining = length
    while remaining > 0:
        piece = handle.read(cursor, min(MIB, remaining))
        if not piece:
            break
        digest.update(piece)
        cursor += len(piece)
        remaining -= len(piece)
    return digest.hexdigest()
