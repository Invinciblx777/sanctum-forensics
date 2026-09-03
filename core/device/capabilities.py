"""Probe a device's sanitization capabilities. Read-only.

Method selection is driven by what is probed here, never by user preference
alone (CLAUDE.md non-negotiable). Two rules follow from that and are enforced
throughout this module:

1. A capability that was not *observed* is not claimed. An absent tool, a
   bridge that swallows pass-through, or an unparseable field all yield "not
   supported" plus a recorded limitation — never a silent assumption.
2. A privilege failure is not "unsupported". ``hdparm -I`` returning permission
   denied raises :class:`UnsupportedCapability` with remediation, because
   reporting it as an empty capability set would understate the hardware and
   push the operator toward a weaker method than the drive can honour.

Scope note for the erase module: NVMe *sanitize* acts at controller scope and
affects every namespace, while *format* acts per namespace. A multi-namespace
controller therefore needs a sanitize, not a format, to guarantee the whole
device is covered.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from core.device._sysio import SystemProbe
from core.errors import UnsupportedCapability
from core.models import (
    Device,
    DeviceCapabilities,
    EraseMethod,
    SanitizationLevel,
)

__all__ = ["probe", "recommend_method"]

logger = structlog.get_logger(__name__)

#: ATA SANITIZE operations we look for in the hdparm feature table.
ATA_SANITIZE_OPS = ("BLOCK_ERASE_EXT", "OVERWRITE_EXT", "CRYPTO_SCRAMBLE_EXT")

#: NVMe Identify Controller SANICAP bit positions (NVMe base spec, Figure 275).
_SANICAP_CRYPTO_ERASE = 0
_SANICAP_BLOCK_ERASE = 1
_SANICAP_OVERWRITE = 2
#: NVMe FNA bit 2: cryptographic erase supported by Format NVM.
_FNA_CRYPTO_FORMAT = 2

#: sedutil-cli --scan second column values that mean a real Opal SSC.
_OPAL_CODES = frozenset({"1", "2", "L"})
#: ...and the ones that mean Pyrite, which has no crypto-erase of user data.
_PYRITE_CODES = frozenset({"p", "P"})

_ERASE_TIME = re.compile(r">?\s*(\d+)\s*min for SECURITY ERASE UNIT", re.IGNORECASE)
_ENHANCED_TIME = re.compile(
    r">?\s*(\d+)\s*min for ENHANCED SECURITY ERASE UNIT", re.IGNORECASE
)


# --------------------------------------------------------------------------
# hdparm parsing
# --------------------------------------------------------------------------


def _security_flag(security_block: list[str], keyword: str) -> bool:
    """Read one hdparm security flag.

    hdparm prints ``\\tnot\\tfrozen`` when clear and ``\\t\\tfrozen`` when set,
    so the flag is true when the line ends in the keyword *without* a preceding
    ``not``.
    """
    for line in security_block:
        stripped = line.strip()
        if not stripped.endswith(keyword):
            continue
        return not stripped.startswith("not")
    return False


def _security_block(text: str) -> list[str]:
    """Return the lines of the ``Security:`` section of ``hdparm -I`` output."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().rstrip(":").lower() == "security":
            return lines[index + 1 :]
    return []


def _sanitize_ops(text: str) -> list[str]:
    """Collect SANITIZE operations marked supported (``*``) by hdparm."""
    found: list[str] = []
    for line in text.splitlines():
        for op in ATA_SANITIZE_OPS:
            if op in line and "*" in line.split(op, 1)[0]:
                found.append(op)
    return sorted(set(found))


def _erase_seconds(security_block: list[str], *, enhanced: bool) -> int:
    """Read the erase-time estimate hdparm reports, preferring the enhanced one.

    hdparm prints minutes. The conversion happens here, at the parse site, so
    every consumer downstream handles one unit and the field name matches what
    it holds.
    """
    blob = "\n".join(security_block)
    if enhanced and (match := _ENHANCED_TIME.search(blob)):
        return int(match.group(1)) * 60
    if match := _ERASE_TIME.search(blob):
        return int(match.group(1)) * 60
    return 0


def _probe_ata(
    device: Device, io: SystemProbe, limitations: list[str]
) -> dict[str, Any]:
    """Run and parse ``hdparm -I``. Raises on a privilege failure."""
    result = io.run("hdparm", "-I", device.path)
    if result.permission_denied:
        raise UnsupportedCapability(
            f"hdparm could not read {device.path}: permission denied.",
            remediation=(
                "Capability probing needs raw device access. Run the privileged "
                "helper as root (see helper/daemon.py) and retry; do not treat "
                "this as an unsupported device."
            ),
        )
    if not result.ok:
        reason = "hdparm is not installed" if result.missing else "hdparm failed"
        if device.transport in {"usb", "mmc"}:
            limitations.append(
                f"ATA pass-through is unavailable through this {device.transport} "
                "bridge, so firmware sanitize and secure erase cannot be verified "
                "or issued. Only overwrite-based CLEAR can be assured."
            )
        else:
            limitations.append(
                f"{reason} for {device.path}; ATA security and SANITIZE support "
                "could not be established."
            )
        logger.info(
            "hdparm_unavailable", path=device.path, returncode=result.returncode
        )
        return {}

    text = result.stdout
    block = _security_block(text)
    enhanced = _security_flag(block, "supported: enhanced erase")
    return {
        "ata_security_erase": _security_flag(block, "supported"),
        "ata_enhanced_erase": enhanced,
        "security_frozen": _security_flag(block, "frozen"),
        "ata_sanitize_ops": _sanitize_ops(text),
        "est_erase_seconds": _erase_seconds(block, enhanced=enhanced),
    }


# --------------------------------------------------------------------------
# NVMe parsing
# --------------------------------------------------------------------------


def _probe_nvme(
    device: Device, io: SystemProbe, limitations: list[str]
) -> dict[str, Any]:
    """Run and parse ``nvme id-ctrl -o json``."""
    result = io.run("nvme", "id-ctrl", device.path, "-o", "json")
    if result.permission_denied:
        raise UnsupportedCapability(
            f"nvme id-ctrl could not read {device.path}: permission denied.",
            remediation=(
                "Capability probing needs raw device access. Run the privileged "
                "helper as root and retry."
            ),
        )
    if not result.ok:
        limitations.append(
            "nvme-cli is unavailable, so controller sanitize capability could "
            "not be established for this device."
        )
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        limitations.append("nvme id-ctrl output could not be parsed.")
        return {}

    sanicap = int(payload.get("sanicap") or 0)
    fna = int(payload.get("fna") or 0)
    namespaces = int(payload.get("nn") or 0)
    if namespaces > 1:
        limitations.append(
            f"Controller exposes {namespaces} namespaces. Format NVM acts per "
            "namespace; only a controller-scope sanitize covers the whole device."
        )
    return {
        "nvme_sanicap": {
            "crypto_erase": bool(sanicap >> _SANICAP_CRYPTO_ERASE & 1),
            "block_erase": bool(sanicap >> _SANICAP_BLOCK_ERASE & 1),
            "overwrite": bool(sanicap >> _SANICAP_OVERWRITE & 1),
            "fna_crypto_format": bool(fna >> _FNA_CRYPTO_FORMAT & 1),
            "namespace_count": namespaces,
            "raw_sanicap": sanicap,
            "raw_fna": fna,
        }
    }


# --------------------------------------------------------------------------
# SED
# --------------------------------------------------------------------------


def _probe_sed(device: Device, io: SystemProbe, limitations: list[str]) -> bool:
    """Detect a true Opal SSC. Pyrite is deliberately not counted as Opal.

    Pyrite implements the Opal command set but provides no cryptographic erase
    of user data, so treating it as Opal would promise a PURGE the drive cannot
    deliver.
    """
    result = io.run("sedutil-cli", "--scan")
    if not result.ok:
        return False
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] != device.path:
            continue
        code = fields[1]
        if code in _OPAL_CODES:
            return True
        if code in _PYRITE_CODES or "pyrite" in line.lower():
            limitations.append(
                "Drive reports Pyrite, not Opal. Pyrite has no cryptographic "
                "erase of user data, so crypto-erase cannot be used for PURGE."
            )
        return False
    return False


# --------------------------------------------------------------------------
# achievable_levels
# --------------------------------------------------------------------------


def _achievable_levels(caps: dict[str, Any]) -> set[SanitizationLevel]:
    """Compute, never store, the NIST SP 800-88 Rev.1 levels this device can reach.

    DERIVED RULE — the source prompt was truncated mid-sentence at
    "PURGE only when a hardware sanitize, enhanced security erase,". Completed
    from NIST SP 800-88 Rev.1 Appendix A:

    * CLEAR  - always reachable: a logical overwrite of the user-addressable
      area works on any writable block device.
    * PURGE  - reachable only via a mechanism the media itself implements:
      ATA SANITIZE, ATA enhanced SECURITY ERASE (blocked while frozen),
      NVMe sanitize, NVMe Format with cryptographic erase, or Opal
      cryptographic erase.
    * DESTROY - never reachable in software; it is physical destruction.

    Change this function if the intended rule differs; nothing else decides it.
    """
    levels = {SanitizationLevel.CLEAR}
    nvme = caps.get("nvme_sanicap") or {}
    purge_paths = (
        bool(caps.get("ata_sanitize_ops")),
        bool(caps.get("ata_enhanced_erase")) and not caps.get("security_frozen"),
        bool(nvme.get("crypto_erase")),
        bool(nvme.get("block_erase")),
        bool(nvme.get("overwrite")),
        bool(nvme.get("fna_crypto_format")),
        bool(caps.get("is_sed_opal")),
    )
    if any(purge_paths):
        levels.add(SanitizationLevel.PURGE)
    return levels


#: Ordered PURGE mechanisms, strongest and most directly attested first.
_PURGE_PREFERENCE: tuple[tuple[str, EraseMethod], ...] = (
    ("ATA_SANITIZE_BLOCK_ERASE", EraseMethod.ATA_SANITIZE_BLOCK_ERASE),
    ("ATA_SANITIZE_CRYPTO", EraseMethod.ATA_SANITIZE_CRYPTO_SCRAMBLE),
    ("ATA_SANITIZE_OVERWRITE", EraseMethod.ATA_SANITIZE_OVERWRITE),
    ("NVME_SANITIZE_BLOCK", EraseMethod.NVME_SANITIZE_BLOCK),
    ("SED_CRYPTO", EraseMethod.SED_CRYPTO_ERASE),
    ("NVME_FORMAT_SES1", EraseMethod.NVME_FORMAT_SES1),
    ("ATA_ENHANCED", EraseMethod.ATA_SECURITY_ERASE_ENHANCED),
)


def probe(device: Device, io: SystemProbe | None = None) -> DeviceCapabilities:
    """Probe ATA/NVMe/SED capability for ``device`` without altering it.

    Args:
        device: The device to interrogate.
        io: Host access seam. Defaults to the real system.

    Raises:
        UnsupportedCapability: A probe failed for lack of privilege. This is
            never reported as an absent capability.
    """
    io = io or SystemProbe()
    limitations: list[str] = []
    found: dict[str, Any] = {}

    if device.transport == "nvme":
        found.update(_probe_nvme(device, io, limitations))
    else:
        found.update(_probe_ata(device, io, limitations))

    found["is_sed_opal"] = _probe_sed(device, io, limitations)

    caps = DeviceCapabilities(
        ata_security_erase=bool(found.get("ata_security_erase")),
        ata_enhanced_erase=bool(found.get("ata_enhanced_erase")),
        ata_sanitize_ops=list(found.get("ata_sanitize_ops") or []),
        nvme_sanicap=dict(found.get("nvme_sanicap") or {}),
        is_sed_opal=bool(found.get("is_sed_opal")),
        security_frozen=bool(found.get("security_frozen")),
        est_erase_seconds=int(found.get("est_erase_seconds") or 0),
        achievable_levels=_achievable_levels(found),
        limitations=limitations,
    )
    logger.info(
        "capabilities_probed",
        path=device.path,
        transport=device.transport,
        levels=sorted(level.value for level in caps.achievable_levels),
        limitation_count=len(caps.limitations),
    )
    return caps


def recommend_method(
    capabilities: DeviceCapabilities, target_level: SanitizationLevel
) -> EraseMethod:
    """Pick the strongest achievable method for ``target_level``.

    Raises:
        UnsupportedCapability: ``target_level`` is not in
            ``capabilities.achievable_levels``.
    """
    if target_level not in capabilities.achievable_levels:
        reachable = ", ".join(
            sorted(level.value for level in capabilities.achievable_levels)
        )
        detail = " ".join(capabilities.limitations) or "No mechanism was observed."
        raise UnsupportedCapability(
            f"{target_level.value} is not achievable on this device. {detail}",
            remediation=(
                f"Choose one of: {reachable}. To reach "
                f"{target_level.value} on this media, physical destruction is "
                "the remaining option."
            ),
        )

    if target_level is SanitizationLevel.CLEAR:
        return EraseMethod.SINGLE_PASS_OVERWRITE

    nvme = capabilities.nvme_sanicap
    available = {
        "ATA_SANITIZE_BLOCK_ERASE": "BLOCK_ERASE_EXT" in capabilities.ata_sanitize_ops,
        "ATA_SANITIZE_CRYPTO": "CRYPTO_SCRAMBLE_EXT" in capabilities.ata_sanitize_ops,
        "ATA_SANITIZE_OVERWRITE": "OVERWRITE_EXT" in capabilities.ata_sanitize_ops,
        "NVME_SANITIZE_BLOCK": bool(nvme.get("block_erase"))
        or bool(nvme.get("crypto_erase")),
        "SED_CRYPTO": capabilities.is_sed_opal,
        "NVME_FORMAT_SES1": bool(nvme.get("fna_crypto_format")),
        "ATA_ENHANCED": capabilities.ata_enhanced_erase
        and not capabilities.security_frozen,
    }
    for key, method in _PURGE_PREFERENCE:
        if available[key]:
            return method
    raise UnsupportedCapability(
        f"{target_level.value} was reported achievable but no mechanism matched.",
        remediation="Re-probe the device; the capability set is inconsistent.",
    )
