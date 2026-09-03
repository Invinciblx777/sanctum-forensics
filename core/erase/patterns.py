"""Overwrite pattern sources for software-overwrite methods.

Only the two software methods stream patterns from here. Capability-based
methods (ATA/NVMe sanitize, ATA security erase, Opal crypto erase) are executed
by the drive's own firmware and never receive a host-generated pattern; asking
this module for one is a programming error and raises.

On DOD_5220_22_M_3PASS: the historical DoD 5220.22-M sequence is a character,
its complement, then a *random* character. A random final pass cannot be
verified by reading the medium back, and unverifiable erasure is exactly what
this project refuses to ship. The third pass here is therefore a fixed zero
character, which keeps the character/complement/character shape and leaves a
result that :mod:`core.erase.verify` can actually check. That substitution is
recorded in ``docs/limitations.md``.

The method is offered only because operators are sometimes required to name it.
It is superseded by NIST SP 800-88 Rev.1 and buys nothing over a single pass on
any post-2001 drive.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.errors import UnsupportedCapability
from core.models import EraseMethod

__all__ = ["pattern_passes", "pass_count", "final_pattern", "SOFTWARE_METHODS"]

#: The only methods this module can generate patterns for.
SOFTWARE_METHODS = frozenset(
    {EraseMethod.SINGLE_PASS_OVERWRITE, EraseMethod.DOD_5220_22_M_3PASS}
)

#: Fill byte per pass, per method. The last entry is what verification expects.
_PASS_BYTES: dict[EraseMethod, tuple[int, ...]] = {
    EraseMethod.SINGLE_PASS_OVERWRITE: (0x00,),
    EraseMethod.DOD_5220_22_M_3PASS: (0x00, 0xFF, 0x00),
}


def _require_software_method(method: EraseMethod) -> tuple[int, ...]:
    if method not in _PASS_BYTES:
        raise UnsupportedCapability(
            f"{method.value} is executed by device firmware and streams no "
            "host-generated pattern.",
            remediation=(
                "Dispatch this method through its firmware handler in "
                "core.erase.drive; do not route it through the overwrite path."
            ),
        )
    return _PASS_BYTES[method]


def _require_block_size(block_size: int) -> None:
    if block_size <= 0:
        raise ValueError(f"block_size must be positive, got {block_size}")


def pattern_passes(method: EraseMethod, *, block_size: int) -> Iterator[bytes]:
    """Yield one block-sized pattern buffer per overwrite pass for ``method``.

    Args:
        method: A member of :data:`SOFTWARE_METHODS`.
        block_size: Buffer length in bytes; must be positive.

    Raises:
        UnsupportedCapability: ``method`` is executed by firmware.
        ValueError: ``block_size`` is not positive.
    """
    fills = _require_software_method(method)
    _require_block_size(block_size)
    for fill in fills:
        yield bytes([fill]) * block_size


def pass_count(method: EraseMethod) -> int:
    """Return how many overwrite passes ``method`` performs.

    Raises:
        UnsupportedCapability: ``method`` is executed by firmware.
    """
    return len(_require_software_method(method))


def final_pattern(method: EraseMethod, *, block_size: int) -> bytes:
    """Return the buffer the medium should hold once ``method`` completes.

    This is what :func:`core.erase.verify.verify` reads back and compares.

    Raises:
        UnsupportedCapability: ``method`` is executed by firmware.
        ValueError: ``block_size`` is not positive.
    """
    fills = _require_software_method(method)
    _require_block_size(block_size)
    return bytes([fills[-1]]) * block_size
