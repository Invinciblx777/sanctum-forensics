"""Canonical data models for Sanctum Forensics.

Pydantic v2 models shared across every core layer, the helper, and the API.
These are pure data containers: no behaviour, no I/O, no business logic.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

__all__ = [
    "SanitizationLevel",
    "EraseMethod",
    "Device",
    "DeviceCapabilities",
    "HiddenAreaReport",
    "ResidualRiskAssessment",
    "EraseJob",
    "VerificationResult",
    "CarveCandidate",
    "LedgerEntry",
    "ForensicReport",
    "Progress",
]


class SanitizationLevel(StrEnum):
    """NIST SP 800-88 Rev.1 sanitization categories."""

    CLEAR = "CLEAR"
    PURGE = "PURGE"
    DESTROY = "DESTROY"


class EraseMethod(StrEnum):
    """Concrete mechanism used to satisfy a :class:`SanitizationLevel`."""

    SINGLE_PASS_OVERWRITE = "SINGLE_PASS_OVERWRITE"
    DOD_5220_22_M_3PASS = "DOD_5220_22_M_3PASS"
    ATA_SECURITY_ERASE_ENHANCED = "ATA_SECURITY_ERASE_ENHANCED"
    ATA_SANITIZE_BLOCK_ERASE = "ATA_SANITIZE_BLOCK_ERASE"
    ATA_SANITIZE_OVERWRITE = "ATA_SANITIZE_OVERWRITE"
    NVME_SANITIZE_BLOCK = "NVME_SANITIZE_BLOCK"
    NVME_FORMAT_SES1 = "NVME_FORMAT_SES1"
    CRYPTO_ERASE = "CRYPTO_ERASE"


Transport = Literal["sata", "nvme", "usb", "mmc", "unknown"]


class Device(BaseModel):
    """A block device as enumerated from the host."""

    path: str
    model: str
    serial: str
    size_bytes: int
    rotational: bool
    transport: Transport
    is_system_disk: bool
    mounted_at: list[str]
    pt_type: str | None
    #: Stable ``/dev/disk/by-id`` path. ``path`` is not stable across replug, so
    #: anything that must survive a reconnect refers to the device by this.
    by_id_path: str | None = None


class DeviceCapabilities(BaseModel):
    """Probed sanitization capability of a specific device."""

    ata_security_erase: bool
    ata_enhanced_erase: bool
    ata_sanitize_ops: list[str]
    nvme_sanicap: dict[str, Any]
    is_sed_opal: bool
    security_frozen: bool
    est_erase_minutes: float
    achievable_levels: set[SanitizationLevel]
    #: Plain-language reasons a stronger level could not be established, e.g. a
    #: USB bridge that blocks ATA pass-through. Surfaced verbatim in the report.
    limitations: list[str] = []


class HiddenAreaReport(BaseModel):
    """Result of HPA/DCO probing for a device."""

    hpa_present: bool
    dco_present: bool
    native_max_sectors: int
    accessible_sectors: int
    hidden_bytes: int


class ResidualRiskAssessment(BaseModel):
    """Honest statement of what could not be guaranteed after an erase."""

    level: Literal["low", "medium", "high"]
    factors: list[str]
    purge_achieved: bool
    notes: str


class EraseJob(BaseModel):
    """A single sanitization request against one device."""

    job_id: str
    device: Device
    method: EraseMethod
    level: SanitizationLevel
    dry_run: bool
    confirmed_serial: str | None


class VerificationResult(BaseModel):
    """Outcome of post-erase verification."""

    passed: bool
    strategy: Literal["full_read", "sampled", "hw_attested"]
    bytes_checked: int
    sample_count: int
    confidence_pct: float
    failed_offsets: list[int]


class CarveCandidate(BaseModel):
    """A recovered-or-recoverable object produced by the carving pipeline."""

    offset: int
    length: int
    ext: str
    mime: str
    source: Literal["fs_metadata", "signature", "structure"]
    validation: Literal["valid", "truncated", "corrupt"]
    confidence: float
    bucket: Literal["HIGH", "MEDIUM", "LOW"]
    sha256: str
    original_name: str | None
    possibly_fragmented: bool


class LedgerEntry(BaseModel):
    """One hash-chained audit record. ``prev_entry_hash`` binds entry N to N-1."""

    seq: int
    ts_utc: datetime
    monotonic_ns: int
    actor: str
    operation: str
    params_hash: str
    result_hash: str
    prev_entry_hash: str
    entry_hash: str


class ForensicReport(BaseModel):
    """Operator-facing report, rendered and detached-signed downstream."""

    case_id: str
    operator: str
    generated_at: datetime
    tool_version: str
    pubkey_fingerprint: str
    sections: dict[str, Any]
    ledger_excerpt: list[LedgerEntry]
    signature: str


class Progress(BaseModel):
    """Progress record yielded by long-running generator operations."""

    job_id: str
    phase: str
    pct: float
    bytes_done: int
    bytes_total: int
    throughput_bps: float
    eta_seconds: float
    message: str
