"""Bifragment gap carving, for JPEG and for nothing else.

**Read this before extending it.** General fragment reassembly - "SmartCarving"
in the literature - is an open research problem. Deciding which of a million
clusters continue a given object is combinatorial, and published systems reach
useful accuracy only on narrow corpora with format-specific decoders. A tool
that claims to reassemble arbitrary fragmented files is a tool that will be
taken apart by anyone who asks how, and rightly.

What is tractable is the *bifragmented* case: an object split into exactly two
runs separated by one gap, which is what a filesystem produces when an existing
extent blocks a contiguous allocation. It is tractable because JPEG carries its
own validator. A JPEG decoder consuming the whole reassembled object without
error is strong evidence the gap size was right, so the search has an oracle
rather than a heuristic.

The search is bounded on every axis, because an unrecoverable object must cost
a fixed budget rather than the rest of the image:

* :data:`MAX_GAP_CANDIDATES` gap sizes tried, at cluster boundaries only;
* :data:`MAX_SEARCH_WINDOW` bytes ahead of the header searched;
* :data:`DECODE_DEADLINE_S` seconds of wall clock per candidate object.

Everything that is not a JPEG gets ``possibly_fragmented=True`` and no
reconstruction attempt. That is a stated limit, not an oversight, and it
belongs in the report next to the recovered files.
"""

from __future__ import annotations

import io
import time

import structlog

from core.carve.evidence import EvidenceHandle

__all__ = [
    "MAX_GAP_CANDIDATES",
    "MAX_SEARCH_WINDOW",
    "DECODE_DEADLINE_S",
    "DEFAULT_CLUSTER_BYTES",
    "decodes_cleanly",
    "reassemble_bifragmented_jpeg",
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

#: Fragments begin on cluster boundaries, so only those offsets are tried.
DEFAULT_CLUSTER_BYTES = 4096

_SOI = b"\xff\xd8\xff"
_EOI = b"\xff\xd9"


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


def reassemble_bifragmented_jpeg(
    handle: EvidenceHandle,
    start: int,
    *,
    max_size: int,
    cluster_size: int = DEFAULT_CLUSTER_BYTES,
) -> bytes | None:
    """Recover a JPEG split into two runs by one gap, or return ``None``.

    The contiguous case is tried first and costs one decode. Failing that, the
    head is truncated at successive cluster boundaries and the tail is sought
    at successive cluster boundaries after the gap, accepting the first
    combination the decoder consumes whole.

    Returns the reassembled bytes so the caller can hash them: a candidate that
    reports a length but not its content cannot be checked by anyone.
    """
    deadline = time.monotonic() + DECODE_DEADLINE_S
    limit = min(start + max_size, handle.size)

    if handle.read(start, 3) != _SOI:
        return None

    # 1. Contiguous. Cheapest and by far the most common.
    for end in _find_eoi(handle, start, min(limit, start + MAX_SEARCH_WINDOW))[:8]:
        payload = handle.read(start, end - start)
        if decodes_cleanly(payload):
            return payload
        if time.monotonic() > deadline:
            return None

    # 2. Bifragmented. Walk head lengths on cluster boundaries; for each, try
    #    gap sizes that also land on cluster boundaries.
    search_end = min(limit, start + MAX_SEARCH_WINDOW)
    tail_candidates = _find_eoi(handle, start, search_end)
    if not tail_candidates:
        return None

    attempts = 0
    head_bytes = cluster_size
    while head_bytes < MAX_SEARCH_WINDOW and start + head_bytes < search_end:
        head = handle.read(start, head_bytes)
        gap = cluster_size
        while gap <= MAX_SEARCH_WINDOW:
            if attempts >= MAX_GAP_CANDIDATES or time.monotonic() > deadline:
                logger.debug(
                    "bifragment search exhausted",
                    offset=start,
                    attempts=attempts,
                )
                return None
            tail_start = start + head_bytes + gap
            if tail_start >= search_end:
                break
            for end in tail_candidates:
                if end <= tail_start:
                    continue
                attempts += 1
                if attempts > MAX_GAP_CANDIDATES:
                    return None
                payload = head + handle.read(tail_start, end - tail_start)
                if len(payload) > max_size:
                    continue
                if decodes_cleanly(payload):
                    logger.info(
                        "bifragmented jpeg reassembled",
                        offset=start,
                        head_bytes=head_bytes,
                        gap_bytes=gap,
                    )
                    return payload
                break  # only the nearest EOI past this gap is plausible
            gap += cluster_size
        head_bytes += cluster_size

    return None
