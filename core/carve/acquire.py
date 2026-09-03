"""Read-only acquisition of an evidence source.

The evidence path is opened read-only, always. Nothing in the carve package
opens a device or image O_RDWR (CLAUDE.md non-negotiable). ``ReadableImage`` is
the only surface the rest of the pipeline is allowed to touch. Deferred to M3.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from core.models import Progress

__all__ = ["ReadableImage", "open_readonly", "acquire_image"]


@runtime_checkable
class ReadableImage(Protocol):
    """A byte-addressable, read-only view over an evidence source."""

    @property
    def size_bytes(self) -> int:
        """Total readable length in bytes."""
        ...

    def read_at(self, offset: int, length: int) -> bytes:
        """Return up to ``length`` bytes starting at ``offset``. Never writes."""
        ...

    def close(self) -> None:
        """Release the underlying handle."""
        ...


def open_readonly(path: str) -> ReadableImage:
    """Open a raw device or image file (raw/E01/AFF4) as a read-only image."""
    raise NotImplementedError


def acquire_image(source: str, dest: str) -> Iterator[Progress]:
    """Copy ``source`` to an E01 image at ``dest``, yielding progress."""
    raise NotImplementedError
