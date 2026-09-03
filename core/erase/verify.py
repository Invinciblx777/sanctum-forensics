"""Post-erase verification and residual-risk assessment. Read-only.

Three strategies, chosen from the method and the media size:

``full_read``
    Every block is read and compared. Used at or below 64 GiB.

``sampled``
    The first and last 1 GiB in full, plus a fixed number of uniformly random
    fixed-size windows drawn from a seeded RNG. The seed is recorded so a third
    party can redraw exactly the same sample set from the report.

``hw_attested``
    Used for firmware sanitize. The drive's own sanitize log is read, *and* a
    sampled read is performed anyway. Attestation is a claim the drive makes
    about itself; it is evidence, not proof, so it never replaces reading the
    medium.

The device is opened ``O_RDONLY``. Nothing in this module ever opens for write.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import structlog

from core.device._sysio import SystemProbe
from core.erase.patterns import SOFTWARE_METHODS, final_pattern
from core.models import (
    Device,
    DeviceCapabilities,
    EraseMethod,
    HiddenAreaReport,
    ResidualRiskAssessment,
    SanitizationLevel,
    UnwritableRange,
    VerificationResult,
)

__all__ = [
    "VerifyConfig",
    "Strategy",
    "verify",
    "choose_strategy",
    "detection_probability",
    "probability_statement",
    "assess_residual_risk",
]

logger = structlog.get_logger(__name__)

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

Strategy = Literal["full_read", "sampled", "hw_attested"]

#: Methods executed by drive firmware. These get hardware attestation.
FIRMWARE_METHODS = frozenset(set(EraseMethod) - set(SOFTWARE_METHODS))

#: Byte values a firmware sanitize may legitimately leave behind. Vendors
#: differ: block erase commonly leaves 0x00, some leave 0xFF. Anything else is
#: residual data.
_FIRMWARE_EXPECTED = frozenset({0x00, 0xFF})

#: NVMe sanitize log SSTAT status field (bits 2:0) values that mean success.
_NVME_SSTAT_OK = frozenset({1, 4})
_NVME_SSTAT_MASK = 0x7

_READ_ONLY_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0)

_PROBABILITY_FORMULA = "P = 1 - (1 - (r + u - 1) / n)^k"


@dataclass(frozen=True)
class VerifyConfig:
    """Tunables for verification. Defaults are the shipped policy."""

    full_read_max_bytes: int = 64 * GIB
    edge_bytes: int = 1 * GIB
    sample_count: int = 4096
    sample_bytes: int = 1 * MIB
    seed: int = 0x5A4E4354
    read_chunk: int = 1 * MIB

    def with_seed(self, seed: int) -> VerifyConfig:
        """Return a copy using ``seed``."""
        return replace(self, seed=seed)


DEFAULT_CONFIG = VerifyConfig()


# --------------------------------------------------------------------------
# Probability
# --------------------------------------------------------------------------


def detection_probability(
    total_bytes: int, residual_bytes: int, *, sample_bytes: int, draws: int
) -> float:
    """Probability that ``draws`` random windows hit a residual region.

    Models each draw as a uniformly placed window of ``sample_bytes`` over
    ``total_bytes``. A window overlaps a residual region of ``residual_bytes``
    when its start falls anywhere in a span of ``residual_bytes + sample_bytes
    - 1``, so a single draw hits with probability ``(r + u - 1) / n`` and
    ``k`` independent draws miss with ``(1 - (r + u - 1) / n) ** k``.
    """
    if total_bytes <= 0 or draws <= 0 or residual_bytes <= 0:
        return 0.0
    per_draw = min(1.0, (residual_bytes + sample_bytes - 1) / total_bytes)
    return 1.0 - (1.0 - per_draw) ** draws


def probability_statement(
    *, total_bytes: int, residual_bytes: int, sample_bytes: int, draws: int
) -> str:
    """Render the detection-probability formula with this run's values."""
    probability = detection_probability(
        total_bytes, residual_bytes, sample_bytes=sample_bytes, draws=draws
    )
    return (
        f"{_PROBABILITY_FORMULA}, where n=total bytes, u=sample window, "
        f"r=size of a hypothetical residual region, k=random draws. "
        f"Here n={total_bytes}, u={sample_bytes}, r={residual_bytes}, "
        f"k={draws}, giving P={probability:.6f}. This is the chance of "
        f"detecting a residual region of {residual_bytes} bytes; it is not a "
        f"guarantee that none exists."
    )


# --------------------------------------------------------------------------
# Strategy
# --------------------------------------------------------------------------


def choose_strategy(
    size_bytes: int, method: EraseMethod, config: VerifyConfig = DEFAULT_CONFIG
) -> Strategy:
    """Pick the verification strategy for this method and media size."""
    if method in FIRMWARE_METHODS:
        return "hw_attested"
    if size_bytes <= config.full_read_max_bytes:
        return "full_read"
    return "sampled"


def _expected_bytes(method: EraseMethod) -> frozenset[int]:
    """Byte values the medium may legitimately hold after ``method``."""
    if method in SOFTWARE_METHODS:
        return frozenset({final_pattern(method, block_size=1)[0]})
    return _FIRMWARE_EXPECTED


def _first_bad_index(chunk: bytes, allowed: frozenset[int]) -> int | None:
    """Index of the first byte in ``chunk`` outside ``allowed``, else ``None``."""
    for value in allowed:
        if chunk.count(value) == len(chunk):
            return None
    for index, byte in enumerate(chunk):
        if byte not in allowed:
            return index
    return None


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _read_windows(
    fd: int, windows: Iterator[tuple[int, int]], allowed: frozenset[int]
) -> tuple[int, list[int]]:
    """Read each ``(offset, length)`` window, returning bytes read and failures."""
    checked = 0
    failed: list[int] = []
    for offset, length in windows:
        os.lseek(fd, offset, os.SEEK_SET)
        remaining = length
        position = offset
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            checked += len(chunk)
            bad = _first_bad_index(chunk, allowed)
            if bad is not None:
                failed.append(position + bad)
            position += len(chunk)
            remaining -= len(chunk)
    return checked, failed


def _full_read_windows(size: int, config: VerifyConfig) -> Iterator[tuple[int, int]]:
    offset = 0
    while offset < size:
        yield offset, min(config.read_chunk, size - offset)
        offset += config.read_chunk


def _sample_windows(
    size: int, config: VerifyConfig
) -> tuple[list[tuple[int, int]], int]:
    """Both edges in full plus seeded random windows over the middle."""
    edge = min(config.edge_bytes, size // 2)
    windows: list[tuple[int, int]] = []
    if edge > 0:
        windows.append((0, edge))
        windows.append((size - edge, edge))
    else:
        windows.append((0, size))

    low = edge
    high = size - edge - config.sample_bytes
    draws = 0
    if high > low:
        rng = random.Random(config.seed)
        for _ in range(config.sample_count):
            windows.append((rng.randrange(low, high + 1), config.sample_bytes))
            draws += 1
    return windows, draws


# --------------------------------------------------------------------------
# Hardware attestation
# --------------------------------------------------------------------------


def _ata_attested(device: Device, io: SystemProbe) -> bool | None:
    result = io.run("hdparm", "--sanitize-status", device.path)
    if not result.ok:
        return None
    text = result.stdout.lower()
    if "fail" in text:
        return False
    return "without error" in text or "idle" in text


def _nvme_attested(device: Device, io: SystemProbe) -> bool | None:
    result = io.run("nvme", "sanitize-log", device.path, "-o", "json")
    if not result.ok:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    sstat = int(payload.get("sstat") or 0)
    return (sstat & _NVME_SSTAT_MASK) in _NVME_SSTAT_OK


_ATTESTERS = {
    EraseMethod.ATA_SANITIZE_BLOCK_ERASE: _ata_attested,
    EraseMethod.ATA_SANITIZE_OVERWRITE: _ata_attested,
    EraseMethod.ATA_SANITIZE_CRYPTO_SCRAMBLE: _ata_attested,
    EraseMethod.NVME_SANITIZE_BLOCK: _nvme_attested,
}


def _attestation(
    device: Device, method: EraseMethod, io: SystemProbe | None
) -> bool | None:
    """Read the drive's own sanitize status. ``None`` when none is available."""
    attester = _ATTESTERS.get(method)
    if attester is None:
        return None
    return attester(device, io or SystemProbe())


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def verify(
    device: Device,
    method: EraseMethod,
    *,
    source_path: Path | str | None = None,
    config: VerifyConfig = DEFAULT_CONFIG,
    io: SystemProbe | None = None,
) -> VerificationResult:
    """Read the medium back and report whether it looks sanitized.

    Args:
        device: The device that was erased.
        method: The method that was used; decides the expected pattern.
        source_path: Read this path instead of ``device.path``. Used by tests
            and by callers verifying an image rather than a live device.
        config: Verification tunables.
        io: Host access seam for reading the drive's sanitize log.

    Returns:
        A :class:`VerificationResult`. ``passed`` is false if any block holds
        unexpected data, or if hardware attestation reported a failure.
    """
    path = Path(source_path) if source_path is not None else Path(device.path)
    strategy = choose_strategy(device.size_bytes, method, config)
    allowed = _expected_bytes(method)

    attested = _attestation(device, method, io) if strategy == "hw_attested" else None

    fd = os.open(path, _READ_ONLY_FLAGS)
    try:
        size = os.lseek(fd, 0, os.SEEK_END)
        if strategy == "full_read":
            windows: list[tuple[int, int]] = list(_full_read_windows(size, config))
            draws = 0
        else:
            windows, draws = _sample_windows(size, config)
        checked, failed = _read_windows(fd, iter(windows), allowed)
    finally:
        os.close(fd)

    if strategy == "full_read":
        confidence = 100.0
        note = (
            "Every addressable block was read and compared. No sampling "
            "assumption applies."
        )
    else:
        note = probability_statement(
            total_bytes=max(size, 1),
            residual_bytes=config.sample_bytes,
            sample_bytes=config.sample_bytes,
            draws=max(draws, 1),
        )
        confidence = (
            detection_probability(
                max(size, 1),
                config.sample_bytes,
                sample_bytes=config.sample_bytes,
                draws=max(draws, 1),
            )
            * 100.0
        )

    passed = not failed and attested is not False
    result = VerificationResult(
        passed=passed,
        strategy=strategy,
        bytes_checked=checked,
        sample_count=draws,
        confidence_pct=confidence,
        failed_offsets=sorted(failed),
        sample_seed=config.seed if draws else None,
        probability_note=note,
        hw_attested=bool(attested),
    )
    logger.info(
        "verification_complete",
        path=str(path),
        strategy=strategy,
        passed=passed,
        bytes_checked=checked,
        failures=len(result.failed_offsets),
        hw_attested=result.hw_attested,
    )
    return result


def assess_residual_risk(
    *,
    device: Device,
    capabilities: DeviceCapabilities,
    method: EraseMethod,
    requested_level: SanitizationLevel,
    achieved_level: SanitizationLevel,
    verification: VerificationResult,
    hidden: HiddenAreaReport | None = None,
    hidden_covered: bool = True,
    unwritable_ranges: list[UnwritableRange] | None = None,
    limitations: list[str] | None = None,
) -> ResidualRiskAssessment:
    """State plainly what this erase could not guarantee.

    Always emitted. Where a guarantee cannot be made, it is named rather than
    omitted (CLAUDE.md non-negotiable).
    """
    unwritable = unwritable_ranges or []
    factors: list[str] = list(limitations or [])
    overwrite_only = method in SOFTWARE_METHODS
    flash = not device.rotational

    if device.transport in {"usb", "mmc"}:
        factors.append(
            f"Device is behind a {device.transport} bridge; no firmware sanitize "
            "could be issued, so erasure is limited to what host writes reach."
        )
    if overwrite_only and flash:
        factors.append(
            "Flash media erased by overwrite only. Remapped bad blocks and "
            "over-provisioned capacity are not host-addressable and cannot be "
            "reached by any host write pattern."
        )
    if unwritable:
        total = sum(item.length for item in unwritable)
        factors.append(
            f"{len(unwritable)} unwritable range(s) totalling {total} bytes were "
            "skipped after I/O errors; their prior contents remain."
        )
    if hidden is not None and hidden.hidden_bytes > 0:
        if hidden_covered:
            factors.append(
                f"{hidden.hidden_bytes} bytes were hidden by HPA/DCO and were "
                "unlocked and covered by this erase."
            )
        else:
            factors.append(
                f"{hidden.hidden_bytes} bytes hidden by HPA/DCO were NOT covered "
                "by this erase and may still hold data."
            )
    if capabilities.is_sed_opal and method is not EraseMethod.SED_CRYPTO_ERASE:
        factors.append(
            "Drive is self-encrypting (Opal) but was not crypto-erased; the "
            "media encryption key was left in place."
        )
    if verification.strategy == "sampled":
        factors.append(
            "Verification was sampled, not exhaustive. " + verification.probability_note
        )
    if verification.hw_attested:
        factors.append(
            "The drive reported clean sanitize completion. That is the drive's "
            "own claim about itself and was corroborated, not replaced, by "
            "reading the medium."
        )
    factors.extend(capabilities.limitations)

    purge_achieved = achieved_level is SanitizationLevel.PURGE

    if not verification.passed:
        level: Literal["low", "medium", "high"] = "high"
        notes = "Verification failed: residual data was read back after the erase."
    elif requested_level is SanitizationLevel.PURGE and not purge_achieved:
        level = "high"
        notes = (
            "A Purge was requested but only Clear was achieved. Data may be "
            "recoverable with laboratory techniques."
        )
    elif unwritable or (
        hidden is not None and hidden.hidden_bytes > 0 and not hidden_covered
    ):
        level = "high"
        notes = "Part of the medium was not erased. See factors."
    elif overwrite_only and flash:
        level = "medium"
        notes = (
            "Overwrite on flash cannot reach remapped or over-provisioned "
            "blocks. Use a firmware sanitize or crypto-erase where available."
        )
    elif purge_achieved and verification.hw_attested and verification.passed:
        level = "low"
        notes = "Hardware-attested purge with clean verification."
    else:
        level = "medium"
        notes = "Erase completed and verified within the stated sampling limits."

    return ResidualRiskAssessment(
        level=level,
        factors=factors,
        purge_achieved=purge_achieved,
        notes=notes,
    )
