"""Bifragment gap carving, for JPEG and for nothing else.

**Read this before extending it.** General fragment reassembly - "SmartCarving"
in the literature - is an open research problem. Deciding which of a million
clusters continue a given object is combinatorial, and published systems reach
useful accuracy only on narrow corpora with format-specific decoders. A tool
that claims to reassemble arbitrary fragmented files is a tool that will be
taken apart by anyone who asks how, and rightly.

What is tractable is the *bifragmented* case: an object split into exactly two
runs separated by one gap, which is what a filesystem produces when an existing
extent blocks a contiguous allocation. It is tractable because JPEG carries
enough of its own structure to be checked, so the search has an oracle rather
than a heuristic.

**That oracle is not the decoder on its own, and assuming it was is what made
this dangerous.** Measured against Pillow 12: ``Image.load`` accepts
``jpeg[:4096] + FFD9`` - five per cent of a 77 KB object - and reports a fully
decoded 256x256 image, because libjpeg stops at EOI and returns whatever it
managed. Accepting the first join a decoder tolerates therefore produces
*fabricated* objects: a head spliced to a stray EOI in an unrelated file,
decoding without complaint, hashing to something that was never on the medium
as one thing. Three checks, layered, are what makes the answer trustworthy:

* :func:`is_whole_jpeg` - the scan data must contain no reserved marker code
  and no early EOI. Foreign bytes spliced into a scan carry roughly one
  ``0xFF`` per 256 bytes and three quarters of those are followed by a reserved
  code, so a wrong gap is rejected here, before a decoder is allocated.
* a one-cluster floor on the second run - a fragment is a run of the medium,
  not two bytes of EOI that happen to land on a cluster boundary. This costs
  the rare object whose tail holds under one cluster of data, which is the
  safe direction to be wrong in.
* head extension - the enumeration tries short heads first, so the first join
  that passes may skip clusters that were the object's own. The gap is where
  *somebody else's* bytes are, so the true head is the longest one that still
  reassembles.

The search is bounded on every axis, because an unrecoverable object must cost
a fixed budget rather than the rest of the image:

* :data:`MAX_GAP_CANDIDATES` gap sizes tried, at cluster boundaries only;
* :data:`MAX_SEARCH_WINDOW` bytes ahead of the header searched;
* :data:`DECODE_DEADLINE_S` seconds of wall clock per candidate object.

Two stated limits belong in the report next to the recovered files. Fragments
are sought on :data:`DEFAULT_CLUSTER_BYTES` boundaries, so a split a volume with
a different cluster size produced is not enumerated and the object is reported
as a low-confidence candidate rather than guessed at. And everything that is not
a JPEG gets ``possibly_fragmented=True`` and no reconstruction attempt at all.
"""

from __future__ import annotations

import io
import time
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from core.carve.evidence import EvidenceHandle
from core.models import CarveFragment

__all__ = [
    "MAX_GAP_CANDIDATES",
    "MAX_SEARCH_STEPS",
    "MAX_SEARCH_WINDOW",
    "DECODE_DEADLINE_S",
    "DEFAULT_CLUSTER_BYTES",
    "Reassembly",
    "decodes_cleanly",
    "is_whole_jpeg",
    "read_fragments",
    "reassemble_bifragmented_jpeg",
    "reassemble_bifragmented_jpeg_runs",
]

logger = structlog.get_logger(__name__)

KIB = 1024
MIB = 1024 * KIB

#: Gap sizes tried before giving up. Each is a full decode attempt, so this is
#: the dominant cost and the number that keeps it bounded.
MAX_GAP_CANDIDATES = 64

#: How far past the header the second fragment is searched for.
MAX_SEARCH_WINDOW = 8 * MIB

#: Wall-clock budget for one object's entire reassembly search.
DECODE_DEADLINE_S = 10.0

#: (head, gap) pairs examined before giving up, counting the ones cheap enough
#: to reject without a decode. Separate from :data:`MAX_GAP_CANDIDATES`, which
#: counts only the joins that reach a decoder, because the two failure modes are
#: different: a hostile image can present thousands of ``FFD9`` bytes that cost
#: nothing individually and everything in aggregate. Measured on a 9 MiB image
#: with an EOI on every cluster boundary: without this cap the search ran the
#: full :data:`DECODE_DEADLINE_S` on a single candidate, which on a stick with
#: many unrecoverable JPEG headers is minutes of a live demo per header.
MAX_SEARCH_STEPS = 20_000

#: Fragments begin on cluster boundaries, so only those offsets are tried.
DEFAULT_CLUSTER_BYTES = 4096

_SOI = b"\xff\xd8\xff"
_EOI = b"\xff\xd9"


@dataclass(frozen=True)
class Reassembly:
    """Reassembled bytes together with where on the medium they came from.

    The runs are the point. Returning only the bytes would leave the caller
    holding a digest it cannot describe the provenance of, and a candidate that
    reports a digest for bytes nobody can locate again is not evidence.
    """

    #: The object, in file order, ready to hash or write.
    payload: bytes
    #: Image-absolute runs, in file order. One run means the object turned out
    #: to be contiguous after all.
    runs: tuple[CarveFragment, ...]

    @property
    def fragmented(self) -> bool:
        """True when the object was recovered from more than one run."""
        return len(self.runs) > 1


def decodes_cleanly(payload: bytes) -> bool:
    """True when a real decoder consumes the whole object without error.

    This is the oracle the gap search needs. ``Image.verify`` checks structure
    but not entropy data, so the pixels are actually loaded: a wrong gap
    produces a stream that parses and then fails partway through decoding, and
    only ``load`` catches that.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as image:
            image.load()
        return True
    except Exception:
        return False


#: Bytes that may legally follow ``0xFF`` inside entropy-coded data: ``0x00``
#: is a stuffed byte, ``0xFF`` is a fill byte, and ``0xC0``-``0xFE`` are the
#: defined markers. ``0x01`` is TEM. Everything from ``0x02`` to ``0xBF`` is
#: reserved and cannot appear in a JPEG at all, which is what makes it a
#: usable rejection test for foreign bytes spliced into a scan.
def _entropy_data_is_well_formed(payload: bytes) -> bool:
    """Whether the scan data reads as one JPEG scan ending exactly at EOI.

    This exists because **Pillow is not the oracle the search needs on its
    own.** Measured: ``Image.load`` accepts ``jpeg[:4096] + FFD9`` and reports
    a fully decoded 256x256 image. Once an EOI arrives libjpeg stops and
    returns what it has, so "it decoded" says nothing about whether the bytes
    between SOS and EOI were the object's own.

    What the bytes themselves say is checkable. Inside entropy-coded data a
    ``0xFF`` is either stuffed (``FF00``), a fill byte (``FFFF``) or a marker
    (``FFC0``-``FFFE``); ``FF02``-``FFBF`` is reserved and cannot occur. A
    32 KiB run of somebody else's file spliced into a scan carries roughly 128
    ``0xFF`` bytes and about three quarters of them are followed by a reserved
    code, so a wrong gap is rejected here without a decode being attempted.
    An early ``FFD9`` is rejected for the same reason: the image ended there,
    so anything after it belongs to something else.

    It does not catch a join of two runs that are both genuinely this object's
    data with a piece missing between them - both halves are legal scans. That
    case is what the head extension in
    :func:`reassemble_bifragmented_jpeg_runs` is for.
    """
    scan_at = _scan_start(payload)
    if scan_at is None:
        return False
    if not payload.endswith(_EOI):
        return False
    end = len(payload) - 2
    position = payload.find(b"\xff", scan_at, end)
    while position != -1:
        following = payload[position + 1]
        if following == 0xD9:
            return False  # the image ended before the bytes did
        if following in (0x00, 0x01, 0xFF) or following >= 0xC0:
            position = payload.find(b"\xff", position + 1, end)
            continue
        return False
    return True


def _scan_start(payload: bytes) -> int | None:
    """Offset of the first byte of entropy-coded data, or ``None``."""
    if not payload.startswith(b"\xff\xd8"):
        return None
    cursor = 2
    limit = len(payload)
    while cursor + 4 <= limit:
        if payload[cursor] != 0xFF:
            return None
        kind = payload[cursor + 1]
        if kind == 0xDA:  # SOS: the scan begins after its header
            return cursor + 2 + int.from_bytes(payload[cursor + 2 : cursor + 4], "big")
        if kind == 0x01 or 0xD0 <= kind <= 0xD8:
            cursor += 2
            continue
        cursor += 2 + int.from_bytes(payload[cursor + 2 : cursor + 4], "big")
    return None


def is_whole_jpeg(payload: bytes) -> bool:
    """The oracle the gap search actually needs: whole object, not merely parsed.

    Structure first, because it is cheap and rejects almost every wrong gap
    without allocating a decoder, then the decode, because structure alone does
    not prove the Huffman data is this image's.
    """
    return _entropy_data_is_well_formed(payload) and decodes_cleanly(payload)


def _find_eoi(handle: EvidenceHandle, start: int, limit: int) -> list[int]:
    """Offsets just past each EOI marker in ``[start, limit)``, in order."""
    found: list[int] = []
    window = 256 * KIB
    cursor = start
    while cursor < limit:
        block = handle.read(cursor, min(window, limit - cursor))
        if not block:
            break
        position = 0
        while True:
            index = block.find(_EOI, position)
            if index == -1:
                break
            found.append(cursor + index + 2)
            position = index + 1
        cursor += max(len(block) - 1, 1)
    return found


def read_fragments(
    handle: EvidenceHandle, fragments: Sequence[CarveFragment]
) -> bytes:
    """Read a candidate's runs back through the handle, in file order.

    The counterpart to :attr:`Reassembly.runs`: it reads the same bytes that
    produced ``candidate.sha256``, so anyone holding the evidence can recompute
    the digest rather than take the candidate's word for it.
    """
    out = bytearray()
    for fragment in fragments:
        cursor = fragment.offset
        remaining = fragment.length
        while remaining > 0:
            piece = handle.read(cursor, min(MIB, remaining))
            if not piece:
                break
            out += piece
            cursor += len(piece)
            remaining -= len(piece)
    return bytes(out)


def _extend_head(
    handle: EvidenceHandle,
    start: int,
    *,
    head_bytes: int,
    tail_start: int,
    end: int,
    cluster_size: int,
    deadline: float,
    accepted: bytes,
) -> tuple[int, bytes]:
    """Grow an accepted head one cluster at a time while it still holds.

    The enumeration tries short heads first, so the first combination that
    passes may be a short head joined to the right tail with some of the
    object's own clusters skipped. Measured: a JPEG split 8192 bytes in was
    "recovered" as head 4096 + the correct tail - a real, decodable, structurally
    clean JPEG that is 4096 bytes short of the original and hashes to something
    else. Both halves are this object's own scan data, so no structural test can
    see it.

    What sees it is the medium's own layout. The gap is where somebody *else's*
    bytes are, so every byte of the object that can be contiguous with the head
    is contiguous with the head: the true head is the longest one that still
    reassembles. Extending stops at the first cluster that fails, which is the
    first cluster of the gap.
    """
    tail = handle.read(tail_start, end - tail_start)
    best_head, best = head_bytes, accepted
    grown = head_bytes + cluster_size
    while start + grown <= tail_start and time.monotonic() <= deadline:
        payload = handle.read(start, grown) + tail
        if not is_whole_jpeg(payload):
            break
        best_head, best = grown, payload
        grown += cluster_size
    return best_head, best


def reassemble_bifragmented_jpeg(
    handle: EvidenceHandle,
    start: int,
    *,
    max_size: int,
    cluster_size: int = DEFAULT_CLUSTER_BYTES,
) -> bytes | None:
    """The reassembled bytes only. See :func:`reassemble_bifragmented_jpeg_runs`."""
    found = reassemble_bifragmented_jpeg_runs(
        handle, start, max_size=max_size, cluster_size=cluster_size
    )
    return None if found is None else found.payload


def reassemble_bifragmented_jpeg_runs(
    handle: EvidenceHandle,
    start: int,
    *,
    max_size: int,
    cluster_size: int = DEFAULT_CLUSTER_BYTES,
) -> Reassembly | None:
    """Recover a JPEG split into two runs by one gap, or return ``None``.

    The contiguous case is tried first and costs one decode. Failing that, the
    head is truncated at successive cluster boundaries and the tail is sought
    at successive cluster boundaries after the gap, accepting the first
    combination the decoder consumes whole.

    Returns the reassembled bytes *and the runs they came from*, so the caller
    can hash the content and still say where on the medium each byte was: a
    candidate that reports a length but not its content cannot be checked by
    anyone, and one that reports content it cannot locate cannot be corroborated
    by anyone.
    """
    deadline = time.monotonic() + DECODE_DEADLINE_S
    limit = min(start + max_size, handle.size)

    if handle.read(start, 3) != _SOI:
        return None

    # 1. Contiguous. Cheapest and by far the most common.
    for end in _find_eoi(handle, start, min(limit, start + MAX_SEARCH_WINDOW))[:8]:
        payload = handle.read(start, end - start)
        if is_whole_jpeg(payload):
            return Reassembly(
                payload=payload,
                runs=(CarveFragment(offset=start, length=len(payload)),),
            )
        if time.monotonic() > deadline:
            return None

    # 2. Bifragmented. Walk head lengths on cluster boundaries; for each, try
    #    gap sizes that also land on cluster boundaries.
    search_end = min(limit, start + MAX_SEARCH_WINDOW)
    tail_candidates = _find_eoi(handle, start, search_end)
    if not tail_candidates:
        return None

    attempts = 0
    steps = 0
    head_bytes = cluster_size
    while head_bytes < MAX_SEARCH_WINDOW and start + head_bytes < search_end:
        head = handle.read(start, head_bytes)
        gap = cluster_size
        while gap <= MAX_SEARCH_WINDOW:
            if (
                attempts >= MAX_GAP_CANDIDATES
                or steps >= MAX_SEARCH_STEPS
                or time.monotonic() > deadline
            ):
                logger.debug(
                    "bifragment search exhausted",
                    offset=start,
                    attempts=attempts,
                    steps=steps,
                )
                return None
            steps += 1
            tail_start = start + head_bytes + gap
            if tail_start >= search_end:
                break
            # Only the nearest EOI past this gap is a plausible object end, and
            # the list is ascending, so find it by bisection rather than
            # rescanning from the front - which on an image carrying an EOI on
            # every cluster boundary made this loop quadratic and spent the
            # whole per-object deadline on one candidate.
            index = bisect_right(tail_candidates, tail_start)
            if index >= len(tail_candidates):
                break
            end = tail_candidates[index]
            # A second fragment is a run of the medium. Two bytes of EOI that
            # happen to land on a cluster boundary are not one, and joining a
            # head to them yields "prefix + FFD9", which Pillow decodes without
            # complaint and which is a fabricated object. Costs the rare file
            # whose tail holds under one cluster of data; that is the safe
            # direction to be wrong in.
            if end - tail_start >= cluster_size:
                attempts += 1
                payload = head + handle.read(tail_start, end - tail_start)
                if len(payload) <= max_size and is_whole_jpeg(payload):
                    head_bytes, payload = _extend_head(
                        handle,
                        start,
                        head_bytes=head_bytes,
                        tail_start=tail_start,
                        end=end,
                        cluster_size=cluster_size,
                        deadline=deadline,
                        accepted=payload,
                    )
                    logger.info(
                        "bifragmented jpeg reassembled",
                        offset=start,
                        head_bytes=head_bytes,
                        gap_bytes=tail_start - start - head_bytes,
                    )
                    return Reassembly(
                        payload=payload,
                        runs=(
                            CarveFragment(offset=start, length=head_bytes),
                            CarveFragment(
                                offset=tail_start, length=len(payload) - head_bytes
                            ),
                        ),
                    )
            gap += cluster_size
        head_bytes += cluster_size

    return None
