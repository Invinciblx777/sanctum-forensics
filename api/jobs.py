"""In-process registry of background jobs.

A job wraps a core generator (erase, acquire, carve) and buffers its
:class:`~core.models.Progress` records for SSE consumers. No blocking calls in
request context. Deferred to M6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from core.models import Progress

__all__ = ["JobRegistry"]


class JobRegistry:
    """Tracks running jobs and fans their progress out to subscribers. Stub."""

    def submit(self, kind: str, params: dict[str, Any]) -> str:
        """Start a job of ``kind`` and return its job id."""
        raise NotImplementedError

    def status(self, job_id: str) -> dict[str, Any]:
        """Return the current status record for ``job_id``."""
        raise NotImplementedError

    async def stream(self, job_id: str) -> AsyncIterator[Progress]:
        """Yield progress records for ``job_id`` until it completes."""
        raise NotImplementedError
        yield Progress(  # unreachable: typed async generator stub
            job_id=job_id,
            phase="",
            pct_bp=0,
            bytes_done=0,
            bytes_total=0,
            throughput_bytes_per_sec=0,
            eta_seconds=0,
            message="",
        )

    def cancel(self, job_id: str) -> None:
        """Request cancellation of ``job_id``."""
        raise NotImplementedError
