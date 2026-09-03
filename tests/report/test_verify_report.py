"""Independent report verification, and the CLI a stranger runs on stage."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from core.ledger.chain import ChainStatus, Ledger
from core.report.render import build_report, write_report
from core.report.sign import (
    PASSPHRASE_ENV,
    fingerprint,
    load_or_create_key,
    public_key_of,
    sign_report,
)
from core.report.verify_report import CheckName, verify_report_file

PASSPHRASE = "test passphrase"


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A signed report on disk, with the ledger that produced it."""
    monkeypatch.setenv(PASSPHRASE_ENV, PASSPHRASE)
    key = load_or_create_key(tmp_path / "sanctum.key.pem")
    print_fingerprint = fingerprint(public_key_of(key))

    ledger = Ledger(
        tmp_path / "store",
        tool_version="0.1.0",
        pubkey_fingerprint=print_fingerprint,
    )
    for index in range(4):
        ledger.append(
            actor="tester",
            operation=f"erase.phase.{index}",
            params={"index": index},
            result={"ok": True},
        )
    entries = [json.loads(e.model_dump_json()) for e in ledger.entries()]

    report = build_report(
        case_id="CASE-0001",
        operator="A. Operator",
        generated_at=datetime(2026, 3, 1, tzinfo=UTC),
        tool_version="0.1.0",
        device={"model": "M", "serial": "S", "size_bytes": 1},
        method={"method": "SINGLE_PASS_OVERWRITE"},
        hidden_areas={},
        verification={"strategy": "full_read", "passed": True},
        residual_risk={"level": "low", "factors": [], "purge_achieved": False},
        limitations=[],
        ledger_excerpt=entries,
        chain_verification=ledger.verify(),
        pubkey_fingerprint=print_fingerprint,
        merkle_root=ledger.merkle_root(0, 4),
    )
    report["signature"] = sign_report(report, key).model_dump()
    json_path, _ = write_report(report, tmp_path / "out")
    return {
        "path": json_path,
        "ledger_root": tmp_path / "store",
        "fingerprint": print_fingerprint,
    }


def tamper(path: Path, mutate: Any) -> None:
    data = json.loads(path.read_bytes())
    mutate(data)
    path.write_bytes(json.dumps(data).encode("utf-8"))


# --------------------------------------------------------------------------
# Library-level checks
# --------------------------------------------------------------------------


def test_a_good_report_passes_every_check(case: dict[str, Any]) -> None:
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    assert result.ok is True
    assert {c.name for c in result.checks if c.passed} >= {
        CheckName.SIGNATURE,
        CheckName.FINGERPRINT_MATCHES_GENESIS,
        CheckName.CHAIN_INTEGRITY,
        CheckName.BLOBS_AVAILABLE,
    }


def test_each_check_is_reported_independently(case: dict[str, Any]) -> None:
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    assert len(result.checks) == 4
    for check in result.checks:
        assert check.detail


def test_a_tampered_field_fails_only_the_signature_check(
    case: dict[str, Any],
) -> None:
    tamper(case["path"], lambda d: d.update({"case_id": "CASE-9999"}))
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    assert result.ok is False
    failed = {c.name for c in result.checks if not c.passed and c.applicable}
    assert CheckName.SIGNATURE in failed


def test_a_broken_excerpt_fails_the_chain_check(case: dict[str, Any]) -> None:
    def break_chain(data: dict[str, Any]) -> None:
        data["sections"]["audit_trail"]["entries"][2]["actor"] = "impostor"

    tamper(case["path"], break_chain)
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    chain_check = next(
        c for c in result.checks if c.name is CheckName.CHAIN_INTEGRITY
    )
    assert chain_check.passed is False
    assert "2" in chain_check.detail


def test_a_foreign_fingerprint_fails_the_genesis_check(
    case: dict[str, Any],
) -> None:
    tamper(
        case["path"],
        lambda d: d["signature"].update({"pubkey_fingerprint": "00:11:22"}),
    )
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    check = next(
        c for c in result.checks if c.name is CheckName.FINGERPRINT_MATCHES_GENESIS
    )
    assert check.passed is False


def test_blob_check_is_not_applicable_without_a_reachable_store(
    case: dict[str, Any],
) -> None:
    result = verify_report_file(case["path"], ledger_root=None)
    check = next(c for c in result.checks if c.name is CheckName.BLOBS_AVAILABLE)
    assert check.applicable is False
    assert result.ok is True


def test_a_missing_blob_is_reported(case: dict[str, Any]) -> None:
    entries = json.loads(case["path"].read_bytes())["sections"]["audit_trail"][
        "entries"
    ]
    digest = entries[1]["params_hash"]
    (case["ledger_root"] / "blobs" / digest[:2] / digest).unlink()

    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    check = next(c for c in result.checks if c.name is CheckName.BLOBS_AVAILABLE)
    assert check.passed is False
    assert digest[:12] in check.detail


def test_an_unsigned_report_fails_rather_than_passing_vacuously(
    case: dict[str, Any],
) -> None:
    tamper(case["path"], lambda d: d.pop("signature", None))
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    assert result.ok is False


def test_verification_states_that_an_embedded_key_proves_only_consistency(
    case: dict[str, Any],
) -> None:
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    assert "out-of-band" in result.caveat
    assert "third party" in result.caveat


def test_a_report_with_a_broken_chain_verification_status_is_surfaced(
    case: dict[str, Any],
) -> None:
    def break_status(data: dict[str, Any]) -> None:
        data["sections"]["audit_trail"]["chain_status"] = ChainStatus.BROKEN.value

    tamper(case["path"], break_status)
    result = verify_report_file(case["path"], ledger_root=case["ledger_root"])
    assert result.ok is False


# --------------------------------------------------------------------------
# CLI, driven the way a person would
# --------------------------------------------------------------------------


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "core.report.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[2]),
    )


def test_cli_exits_zero_on_a_good_report(case: dict[str, Any]) -> None:
    done = run_cli(
        "verify-report", str(case["path"]), "--ledger-root", str(case["ledger_root"])
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASS" in done.stdout


def test_cli_exits_non_zero_on_a_tampered_report(case: dict[str, Any]) -> None:
    tamper(case["path"], lambda d: d.update({"operator": "someone else"}))
    done = run_cli(
        "verify-report", str(case["path"]), "--ledger-root", str(case["ledger_root"])
    )
    assert done.returncode != 0
    assert "FAIL" in done.stdout
    assert "signature" in done.stdout.lower()


def test_cli_prints_one_line_per_failure(case: dict[str, Any]) -> None:
    def break_two(data: dict[str, Any]) -> None:
        data["operator"] = "someone else"
        data["sections"]["audit_trail"]["entries"][1]["actor"] = "impostor"

    tamper(case["path"], break_two)
    done = run_cli(
        "verify-report", str(case["path"]), "--ledger-root", str(case["ledger_root"])
    )
    failures = [line for line in done.stdout.splitlines() if "FAIL" in line]
    assert len(failures) >= 2


def test_cli_output_is_readable_without_knowing_the_codebase(
    case: dict[str, Any],
) -> None:
    done = run_cli(
        "verify-report", str(case["path"]), "--ledger-root", str(case["ledger_root"])
    )
    lowered = done.stdout.lower()
    assert "signature" in lowered
    assert "chain" in lowered
    assert "out-of-band" in lowered


def test_cli_reports_a_missing_file_clearly(tmp_path: Path) -> None:
    done = run_cli("verify-report", str(tmp_path / "nope.json"))
    assert done.returncode != 0
    assert "not found" in (done.stdout + done.stderr).lower()
