"""Detect HPA/DCO hidden areas. Read-only.

A Host Protected Area or Device Configuration Overlay shrinks the sector range
the OS can address. Anything hidden there is invisible to an overwrite pass, so
a CLEAR that ignores it is not a CLEAR of the whole medium. Detection here is
strictly read-only: ``hdparm -N`` and ``hdparm --dco-identify`` report, they do
not set. Restoring a native max is a destructive configuration change and is
deliberately not implemented in this module.

As with capability probing, a privilege failure raises rather than reporting
"no hidden area" — a false negative here silently understates what survives an
erase.
"""

from __future__ import annotations

import re

import structlog

from core.device._sysio import SystemProbe
from core.errors import UnsupportedCapability
from core.models import Device, HiddenAreaReport

__all__ = ["detect_hidden_areas"]

logger = structlog.get_logger(__name__)

SECTOR_BYTES = 512

#: ``hdparm -N`` prints ``max sectors = <accessible>/<native>``.
_MAX_SECTORS = re.compile(r"max sectors\s*=\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
#: ``hdparm --dco-identify`` prints ``Real max sectors: <native>``.
_DCO_REAL_MAX = re.compile(r"Real max sectors:\s*(\d+)", re.IGNORECASE)


def _require_privilege(device: Device, tool: str, denied: bool) -> None:
    if not denied:
        return
    raise UnsupportedCapability(
        f"{tool} could not read {device.path}: permission denied.",
        remediation=(
            "HPA/DCO detection needs raw device access. Run the privileged "
            "helper as root and retry; a permission failure must not be read "
            "as 'no hidden area'."
        ),
    )


def detect_hidden_areas(
    device: Device, io: SystemProbe | None = None
) -> HiddenAreaReport:
    """Report HPA/DCO presence and the byte count they hide from normal I/O.

    Args:
        device: The device to interrogate.
        io: Host access seam. Defaults to the real system.

    Raises:
        UnsupportedCapability: A probe failed for lack of privilege.
    """
    io = io or SystemProbe()

    if device.transport == "nvme":
        # HPA and DCO are ATA features. NVMe namespaces have neither, so the
        # whole namespace is addressable by definition.
        sectors = device.size_bytes // SECTOR_BYTES
        return HiddenAreaReport(
            hpa_present=False,
            dco_present=False,
            native_max_sectors=sectors,
            accessible_sectors=sectors,
            hidden_bytes=0,
        )

    hpa_result = io.run("hdparm", "-N", device.path)
    _require_privilege(device, "hdparm -N", hpa_result.permission_denied)

    accessible = 0
    hpa_native = 0
    if hpa_result.ok and (match := _MAX_SECTORS.search(hpa_result.stdout)):
        accessible = int(match.group(1))
        hpa_native = int(match.group(2))
    else:
        accessible = device.size_bytes // SECTOR_BYTES
        hpa_native = accessible
        logger.info("hpa_probe_unavailable", path=device.path)

    dco_result = io.run("hdparm", "--dco-identify", device.path)
    _require_privilege(device, "hdparm --dco-identify", dco_result.permission_denied)

    dco_native = 0
    if dco_result.ok and (match := _DCO_REAL_MAX.search(dco_result.stdout)):
        dco_native = int(match.group(1))

    native_max = max(hpa_native, dco_native)
    hidden_sectors = max(native_max - accessible, 0)
    report = HiddenAreaReport(
        hpa_present=accessible < hpa_native,
        dco_present=dco_native > hpa_native,
        native_max_sectors=native_max,
        accessible_sectors=accessible,
        hidden_bytes=hidden_sectors * SECTOR_BYTES,
    )
    logger.info(
        "hidden_areas_probed",
        path=device.path,
        hpa=report.hpa_present,
        dco=report.dco_present,
        hidden_bytes=report.hidden_bytes,
    )
    return report
