"""Safety gate for destructive operations.

Enforces the CLAUDE.md non-negotiables: refuse the system disk, refuse any
mounted filesystem, and require the operator to type the exact device serial.
Deferred to M1.
"""

from __future__ import annotations

from core.models import Device

__all__ = ["assert_erasable", "assert_serial_confirmed"]


def assert_erasable(device: Device) -> None:
    """Raise if ``device`` is the system disk or has a mounted filesystem."""
    raise NotImplementedError


def assert_serial_confirmed(device: Device, typed_serial: str) -> None:
    """Raise :class:`ConfirmationMismatch` if ``typed_serial`` != device serial."""
    raise NotImplementedError
