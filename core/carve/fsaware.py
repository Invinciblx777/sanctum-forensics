"""Filesystem-metadata-aware recovery (undelete).

Uses surviving filesystem structures (MFT records, inodes, directory entries) to
recover deleted files with their original names. Read-only. Deferred to M3.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.carve.evidence import EvidenceHandle
from core.models import CarveCandidate

__all__ = ["undelete"]


def undelete(image: EvidenceHandle) -> Iterator[CarveCandidate]:
    """Yield a candidate per recoverable entry found in filesystem metadata."""
    raise NotImplementedError
