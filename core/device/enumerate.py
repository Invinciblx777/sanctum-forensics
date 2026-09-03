"""Enumerate host block devices. Read-only.

Implementation deferred to a later milestone (M1).
"""

from __future__ import annotations

from core.models import Device

__all__ = ["enumerate_devices", "get_device"]


def enumerate_devices() -> list[Device]:
    """Return every block device visible to the host, system disk included."""
    raise NotImplementedError


def get_device(path: str) -> Device:
    """Return the single device at ``path`` (e.g. ``/dev/sdb``)."""
    raise NotImplementedError
