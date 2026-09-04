"""Canonical data models for Sanctum Forensics.

Pydantic v2 models shared across every core layer, the helper, and the API.
These are pure data containers: no behaviour, no I/O, no business logic.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    "SubstitutedRange",
    "BadSectorRange",
    "EvidenceSource",
    "AcquisitionRecord",
    "IntegrityResult",
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
    #: Whole seconds the drive estimates a firmware erase will take. hdparm
    #: reports this in minutes; it is converted at the parse site so the field
    #: name states its own unit and nothing float reaches a ledger entry.
    est_erase_seconds: int
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
    #: Detection confidence in basis points: 10000 is 100.00%. An integer at
    #: the source, because this is the one number a third party reads to judge
    #: whether verification meant anything, and it must not be a lossy rewrite
    #: of something else.
    confidence_bp: int = Field(ge=0, le=10_000)
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
    #: Whole seconds the plan is expected to take. Named for the unit it
    #: holds, so a reader never has to guess the scale.
    est_seconds: int
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
    #: Carve confidence in basis points: 10000 is 100.00%.
    confidence_bp: int = Field(ge=0, le=10_000)
    bucket: Literal["HIGH", "MEDIUM", "LOW"]
    sha256: str
    original_name: str | None
    possibly_fragmented: bool


class SubstitutedRange(BaseModel):
    """Bytes that could not be read and were filled with a known value.

    The whole point of recording these is that a caller must never mistake a
    substituted byte for one that was actually on the media. A run of zeros
    that came from a fill and a run of zeros that came from the disk are
    indistinguishable in the returned buffer; only this record separates them.
    """

    offset: int
    length: int
    #: The byte written in place of the unreadable data, 0-255.
    fill_byte: int = Field(ge=0, le=255)
    #: Why the read failed, in the operator's words. Never empty.
    reason: str


class BadSectorRange(BaseModel):
    """A run of sectors that failed to read, in logical block addresses.

    ``last_lba`` is inclusive: a single bad sector has ``first_lba ==
    last_lba``. Recorded on the acquisition, in the ledger and in the report,
    because an image containing silent substitutions and no record of them is
    not admissible.
    """

    first_lba: int
    last_lba: int
    sector_size: int
    #: The errno the final attempt returned, so a reader can tell a medium
    #: error from a device that went away mid-acquisition.
    errno: int
    #: How many times the range was retried before being written off.
    attempts: int

    @property
    def sector_count(self) -> int:
        return self.last_lba - self.first_lba + 1


class EvidenceSource(BaseModel):
    """Identity of an evidence source and everything known about its integrity."""

    path: str
    fmt: Literal["raw", "split_raw", "ewf", "bytes"]
    size_bytes: int
    sector_size: int
    #: Present only once a full pass has hashed the source. ``None`` means not
    #: computed, never "computed and empty".
    sha256: str | None = None
    blake3: str | None = None
    #: Segment paths, in address order, for a split set. Empty otherwise.
    segments: list[str] = []
    substituted_ranges: list[SubstitutedRange] = []
    #: Guarantees that could not be made. Rendered verbatim in the report.
    limitations: list[str] = []


class AcquisitionRecord(BaseModel):
    """Chain of custody for one imaging run. Ledgered in full."""

    job_id: str
    source: EvidenceSource
    dest_path: str
    fmt: Literal["raw", "e01"]
    started_at: datetime
    finished_at: datetime
    operator: str
    tool_version: str
    #: Identifies the boot the monotonic clock belongs to. A monotonic reading
    #: is meaningless across a reboot without it.
    boot_id: str
    monotonic_ns: int
    bytes_read: int
    sha256: str
    blake3: str
    #: Size of each chunk in ``chunk_hashes``. A later integrity failure is
    #: localised to a chunk instead of condemning the whole image.
    chunk_bytes: int
    chunk_hashes: list[str] = []
    bad_sectors: list[BadSectorRange] = []
    limitations: list[str] = []
    #: True when this image was completed by resuming an interrupted run.
    resumed: bool = False
    #: True only when a software write block was applied AND read back as
    #: applied. False is honest; there is no third state that implies more.
    write_blocked: bool = False


class IntegrityResult(BaseModel):
    """Outcome of re-reading an image and comparing it to its record."""

    passed: bool
    sha256_matches: bool
    blake3_matches: bool
    expected_sha256: str
    actual_sha256: str
    expected_blake3: str
    actual_blake3: str
    #: Indices into ``AcquisitionRecord.chunk_hashes`` that no longer match.
    #: Empty when the hashes agree, which is what makes a mismatch locatable.
    mismatched_chunks: list[int] = []
    bytes_verified: int


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
    """Progress record yielded by long-running generator operations.

    Every numeric field is an integer, deliberately. :mod:`core.ledger.canon`
    rejects floats, so any progress value that reaches a ledger entry would
    otherwise be rewritten at the boundary - and a boundary rewrite makes the
    recorded number a lossy transform of the measured one, in units the
    original caller never chose. Percentages are basis points and throughput is
    whole bytes per second at the source instead, so the value a verifier reads
    is the value the operation measured.
    """

    job_id: str
    phase: str
    #: Completion in basis points: 10000 is 100.00%.
    pct_bp: int = Field(ge=0, le=10_000)
    bytes_done: int
    bytes_total: int
    throughput_bytes_per_sec: int
    #: Whole seconds remaining. An ETA does not have sub-second accuracy, so
    #: recording it at finer resolution would be false precision.
    eta_seconds: int
    message: str
