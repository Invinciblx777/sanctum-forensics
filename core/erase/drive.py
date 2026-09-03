"""Whole-device sanitization. Destructive. Dry-run is the default.

Yields :class:`Progress` records so callers never block. The guard layer must
have cleared the device before this runs. Deferred to M1.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.models import EraseJob, Progress

__all__ = ["run_erase"]


def run_erase(job: EraseJob) -> Iterator[Progress]:
    """Execute ``job``. When ``job.dry_run`` is true, only simulate and report."""
    raise NotImplementedError
