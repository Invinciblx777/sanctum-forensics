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
    "ErasePhase",
    "UnwritableRange",
    "EraseCheckpoint",
    "ErasePlan",
    "EraseResult",
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
    # Two distinct crypto-erase mechanisms on two different subsystems. They are
    # kept separate so the erase dispatcher never has to re-derive which one a
    # job means from the capability set.
    ATA_SANITIZE_CRYPTO_SCRAMBLE = "ATA_SANITIZE_CRYPTO_SCRAMBLE"
    SED_CRYPTO_ERASE = "SED_CRYPTO_ERASE"


class ErasePhase(StrEnum):
    """The phases an erase job moves through, in execution order."""

    PREFLIGHT = "PREFLIGHT"
    HIDDEN_AREA_UNLOCK = "HIDDEN_AREA_UNLOCK"
    ERASE = "ERASE"
    HIDDEN_AREA_RESTORE = "HIDDEN_AREA_RESTORE"
    VERIFY = "VERIFY"
    REPORT = "REPORT"


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
    level: SanitizationLevel
    dry_run: bool
    confirmed_serial: str | None
    #: An explicitly requested method, or ``None`` to select from probed
    #: capability. Only the software methods may be requested; the erase layer
    #: refuses an explicit request for a firmware method, because those are
    #: chosen from what the drive reports or not at all.
    method: EraseMethod | None = None


class VerificationResult(BaseModel):
    """Outcome of post-erase verification."""

    passed: bool
    strategy: Literal["full_read", "sampled", "hw_attested"]
    bytes_checked: int
    sample_count: int
    confidence_pct: float
    failed_offsets: list[int]
    #: RNG seed for the sampled strategy, recorded so the sample set is
    #: reproducible by a third party checking the report.
    sample_seed: int | None = None
    #: The detection-probability formula with this run's values substituted.
    #: A bare percentage hides its own assumptions; the formula does not.
    probability_note: str = ""
    #: True only when the drive's own sanitize log reported clean completion.
    hw_attested: bool = False


class UnwritableRange(BaseModel):
    """A byte range the overwrite pass could not write, and why.

    A single bad sector must not abort a multi-terabyte wipe, so these are
    collected and surfaced as a residual-risk factor instead.
    """

    offset: int
    length: int
    errno: int

    @property
    def end(self) -> int:
        """First byte after the range."""
        return self.offset + self.length


class EraseCheckpoint(BaseModel):
    """Resume point written to the ledger during a long overwrite."""

    job_id: str
    pass_index: int
    offset: int
    bytes_written: int
    ts_utc: datetime


class ErasePlan(BaseModel):
    """What the tool intends to do, shown in full before anything is written."""

    method: EraseMethod
    level: SanitizationLevel
    justification: str
    est_minutes: float
    limitations: list[str] = []
    hidden_bytes: int = 0


class EraseResult(BaseModel):
    """Outcome of one erase job. Returned by the ``execute`` generator."""

    job_id: str
    method: EraseMethod
    level: SanitizationLevel
    dry_run: bool
    started_at: datetime
    finished_at: datetime
    bytes_written: int
    passes: int
    plan: ErasePlan
    residual_risk: ResidualRiskAssessment
    unwritable_ranges: list[UnwritableRange] = []
    limitations: list[str] = []
    #: True only when the drive's own sanitize status reported clean completion.
    #: Never sufficient on its own; verification always samples as well.
    hw_attested: bool = False


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
    #: Per-boot UUID. ``monotonic_ns`` is only comparable within one boot; without
    #: this a verifier cannot tell a reboot from a clock rollback.
    boot_id: str
    actor: str
    operation: str
    params_hash: str
    result_hash: str
    prev_entry_hash: str
    entry_hash: str


class Signature(BaseModel):
    """A detached signature over a report's canonical bytes."""

    alg: str
    pubkey_fingerprint: str
    pubkey_b64: str
    sig_b64: str
    signed_at: str
    #: Which canonicalisation rules produced the signed bytes. A verifier that
    #: does not implement this version cannot check the signature honestly.
    canon_version: str


class ForensicReport(BaseModel):
    """Operator-facing report, rendered and detached-signed downstream."""

    case_id: str
    operator: str
    generated_at: datetime
    tool_version: str
    pubkey_fingerprint: str
    sections: dict[str, Any]
    ledger_excerpt: list[LedgerEntry]
    signature: Signature | None = None


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
