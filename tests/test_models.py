"""Models construct, round-trip through JSON, and export via ``import *``."""

from __future__ import annotations

import core.models as models
from core.models import (
    CarveCandidate,
    Device,
    EraseJob,
    ForensicReport,
    LedgerEntry,
    VerificationResult,
)


def test_star_export_matches_all() -> None:
    exported = {name for name in dir(models) if not name.startswith("_")}
    assert set(models.__all__).issubset(exported)


def test_device_round_trip(sample_device: Device) -> None:
    assert Device.model_validate_json(sample_device.model_dump_json()) == sample_device


def test_erase_job_defaults_to_dry_run(sample_erase_job: EraseJob) -> None:
    assert sample_erase_job.dry_run is True
    assert sample_erase_job.confirmed_serial is None


def test_candidate_round_trip(sample_candidate: CarveCandidate) -> None:
    dumped = sample_candidate.model_dump_json()
    assert CarveCandidate.model_validate_json(dumped) == sample_candidate


def test_verification_round_trip(sample_verification: VerificationResult) -> None:
    dumped = sample_verification.model_dump_json()
    assert VerificationResult.model_validate_json(dumped) == sample_verification


def test_ledger_entry_round_trip(sample_ledger_entry: LedgerEntry) -> None:
    dumped = sample_ledger_entry.model_dump_json()
    assert LedgerEntry.model_validate_json(dumped) == sample_ledger_entry


def test_report_round_trip(sample_report: ForensicReport) -> None:
    dumped = sample_report.model_dump_json()
    assert ForensicReport.model_validate_json(dumped) == sample_report
