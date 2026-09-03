"""Server-sent events: stream job progress to the UI without blocking.

Adapts a job's :class:`~core.models.Progress` generator into an SSE byte stream.
Deferred to M6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

__all__ = ["progress_events"]


async def progress_events(job_id: str) -> AsyncIterator[bytes]:
    """Yield SSE-framed progress records for ``job_id`` until the job ends."""
    raise NotImplementedError
    yield b""  # unreachable: makes this a typed async generator stub
