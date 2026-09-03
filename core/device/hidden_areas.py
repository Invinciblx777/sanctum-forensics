"""Detect and (on explicit request) restore HPA/DCO hidden areas.

Detection is read-only. Any restore that changes the device's native max is a
destructive-config operation and is out of scope for detection. Deferred to M1.
"""

from __future__ import annotations

from core.models import Device, HiddenAreaReport

__all__ = ["detect_hidden_areas"]


def detect_hidden_areas(device: Device) -> HiddenAreaReport:
    """Report HPA/DCO presence and the byte count they hide from normal I/O."""
    raise NotImplementedError
