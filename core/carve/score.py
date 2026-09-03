"""Assign a confidence score and HIGH/MEDIUM/LOW bucket to a candidate.

Score combines source trust, validation outcome and fragmentation signals.
Pure function of the candidate. Deferred to M3.
"""

from __future__ import annotations

from core.models import CarveCandidate

__all__ = ["score_candidate"]


def score_candidate(candidate: CarveCandidate) -> CarveCandidate:
    """Return ``candidate`` with ``confidence_bp`` and ``bucket`` populated."""
    raise NotImplementedError
