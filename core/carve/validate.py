"""Validate a carved candidate by decoding it.

Attempts a real parse/decode of the candidate's bytes to decide whether it is
``valid``, ``truncated`` or ``corrupt``. Read-only. Deferred to M3.
"""

from __future__ import annotations

from core.carve.acquire import ReadableImage
from core.models import CarveCandidate

__all__ = ["validate_candidate"]


def validate_candidate(
    candidate: CarveCandidate, image: ReadableImage
) -> CarveCandidate:
    """Return ``candidate`` with ``validation`` set from a real decode attempt."""
    raise NotImplementedError
