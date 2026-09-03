"""Shared fixtures. No real device or image access anywhere in the suite."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
import structlog
from core.models import (
    CarveCandidate,
    Device,
    DeviceCapabilities,
    EraseJob,
    EraseMethod,
    ForensicReport,
    LedgerEntry,
    SanitizationLevel,
    VerificationResult,
)


@pytest.fixture
def sample_device() -> Device:
    return Device(
        path="/dev/sdz",
        model="SYNTHETIC-TEST-DISK",
        serial="SYN-0001",
        size_bytes=512 * 1024 * 1024,
        rotational=False,
        transport="usb",
        is_system_disk=False,
        mounted_at=[],
        pt_type="gpt",
    )


@pytest.fixture
def sample_capabilities() -> DeviceCapabilities:
    return DeviceCapabilities(
        ata_security_erase=False,
        ata_enhanced_erase=False,
        ata_sanitize_ops=[],
        nvme_sanicap={},
        is_sed_opal=False,
        security_frozen=False,
        est_erase_minutes=1.0,
        achievable_levels={SanitizationLevel.CLEAR},
    )


@pytest.fixture
def sample_erase_job(sample_device: Device) -> EraseJob:
    return EraseJob(
        job_id="job-0001",
        device=sample_device,
        method=EraseMethod.SINGLE_PASS_OVERWRITE,
        level=SanitizationLevel.CLEAR,
        dry_run=True,
        confirmed_serial=None,
    )


@pytest.fixture
def sample_verification() -> VerificationResult:
    return VerificationResult(
        passed=False,
        strategy="sampled",
        bytes_checked=0,
        sample_count=0,
        confidence_pct=0.0,
        failed_offsets=[],
    )


@pytest.fixture
def sample_candidate() -> CarveCandidate:
    return CarveCandidate(
        offset=0,
        length=0,
        ext="bin",
        mime="application/octet-stream",
        source="signature",
        validation="corrupt",
        confidence=0.0,
        bucket="LOW",
        sha256="0" * 64,
        original_name=None,
        possibly_fragmented=False,
    )


@pytest.fixture
def sample_ledger_entry() -> LedgerEntry:
    return LedgerEntry(
        seq=0,
        ts_utc=datetime(2026, 1, 1, tzinfo=UTC),
        monotonic_ns=0,
        boot_id="00000000-0000-0000-0000-000000000000",
        actor="test",
        operation="noop",
        params_hash="0" * 64,
        result_hash="0" * 64,
        prev_entry_hash="0" * 64,
        entry_hash="0" * 64,
    )


@pytest.fixture
def sample_report(sample_ledger_entry: LedgerEntry) -> ForensicReport:
    return ForensicReport(
        case_id="CASE-0001",
        operator="test",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        tool_version="0.0.0",
        pubkey_fingerprint="AA:BB",
        sections={},
        ledger_excerpt=[sample_ledger_entry],
        signature=None,
    )


def pytest_configure() -> None:
    """Silence structlog below WARNING so test output stays readable."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
        cache_logger_on_first_use=True,
    )
