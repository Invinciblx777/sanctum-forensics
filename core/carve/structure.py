"""Structure-aware carving.

Parses container formats (ZIP, MP4, PDF, SQLite, ...) to find true object
boundaries and to survive fragmentation that pure signature carving misses.
Read-only. Deferred to M3.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.carve.acquire import ReadableImage
from core.models import CarveCandidate

__all__ = ["carve_structures"]


def carve_structures(image: ReadableImage) -> Iterator[CarveCandidate]:
    """Yield a candidate per object located by parsing container structure."""
    raise NotImplementedError
