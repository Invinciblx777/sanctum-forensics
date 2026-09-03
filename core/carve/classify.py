"""Classify a carved candidate by type and, where possible, sensitivity.

Sets the human-facing ``ext``/``mime`` and may enrich ``original_name``.
Read-only. Deferred to M3.
"""

from __future__ import annotations

from core.models import CarveCandidate

__all__ = ["classify_candidate"]


def classify_candidate(candidate: CarveCandidate) -> CarveCandidate:
    """Return ``candidate`` with type fields normalised and enriched."""
    raise NotImplementedError
