"""Probe a device's sanitization capabilities. Read-only.

Method selection is driven by what is probed here, never by user preference
alone (see CLAUDE.md non-negotiables). Implementation deferred to M1.
"""

from __future__ import annotations

from core.models import Device, DeviceCapabilities, EraseMethod, SanitizationLevel

__all__ = ["probe_capabilities", "recommend_method"]


def probe_capabilities(device: Device) -> DeviceCapabilities:
    """Probe ATA/NVMe/SED capability for ``device`` without altering it."""
    raise NotImplementedError


def recommend_method(
    capabilities: DeviceCapabilities, target_level: SanitizationLevel
) -> EraseMethod:
    """Pick the strongest achievable method for ``target_level``."""
    raise NotImplementedError
