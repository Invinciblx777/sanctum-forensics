"""Secure file and folder erasure (M2). Destructive. Dry-run is the default.

Note the documented limit: on copy-on-write and log-structured filesystems,
per-file overwrite does not guarantee old blocks are unreachable. That caveat
belongs in the report, not hidden. Deferred to M2.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.models import Progress

__all__ = ["erase_paths"]


def erase_paths(
    paths: list[str], *, job_id: str, dry_run: bool = True
) -> Iterator[Progress]:
    """Erase each path in ``paths``, yielding progress. Simulates when ``dry_run``."""
    raise NotImplementedError
