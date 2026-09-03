"""Signature-based carving (recover without filesystem metadata).

Scans the image for known header/footer byte patterns. Multi-pattern matching
is the intended approach (Aho-Corasick). Read-only. Deferred to M3.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.carve.acquire import ReadableImage
from core.models import CarveCandidate

__all__ = ["carve_signatures"]


def carve_signatures(image: ReadableImage) -> Iterator[CarveCandidate]:
    """Yield a candidate per header match, bounded by a footer or a size cap."""
    raise NotImplementedError
