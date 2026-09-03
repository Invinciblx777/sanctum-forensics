"""Overwrite pattern sources for software-overwrite methods.

Only used when the method is a software overwrite. Capability-based methods
(ATA/NVMe sanitize, crypto-erase) do not stream patterns from here. Deferred to M1.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.models import EraseMethod

__all__ = ["pattern_passes"]


def pattern_passes(method: EraseMethod, *, block_size: int) -> Iterator[bytes]:
    """Yield one block-sized pattern buffer per overwrite pass for ``method``."""
    raise NotImplementedError
