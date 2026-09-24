"""The simulation journey runs every stage on production code and no device.

``scripts/demo_simulation.py`` is what the demo shows for "run a realistic
simulation", so it is held to the same standard as a destructive path: these
tests assert that every stage carries the simulation banner, that the mounted
medium is BLOCKED and left byte-identical, that the verification and the
certificate are real outcomes rather than printed ones, and that nothing under
``/dev`` is opened at any point.
"""

from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path
from typing import Any

import pytest

if sys.platform != "linux":  # pragma: no cover - platform gate
    pytest.skip("core.erase.drive is Linux-only", allow_module_level=True)

from scripts.demo_simulation import (  # noqa: E402
    BANNER,
    STAGES,
    NotSimulated,
    _require_simulated,
    run,
)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path the journey opens, through either open."""
    paths: list[str] = []
    real_open = builtins.open
    real_os_open = os.open

    def tracked(file: Any, *args: Any, **kwargs: Any) -> Any:
        paths.append(str(file))
        return real_open(file, *args, **kwargs)

    def tracked_os(path: Any, *args: Any, **kwargs: Any) -> Any:
        paths.append(str(path))
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracked)
    monkeypatch.setattr(os, "open", tracked_os)
    return paths


def test_the_journey_runs_every_stage_under_the_banner(
    tmp_path: Path, opened: list[str]
) -> None:
    journey = run(tmp_path / "sim")
    assert journey["physical_device_opened"] is False
    assert [stage["stage"] for stage in journey["stages"]] == list(STAGES)
    assert all(stage["banner"] == BANNER for stage in journey["stages"])
    assert BANNER == "SIMULATION / NO PHYSICAL DEVICE MODIFIED"
    assert not [path for path in opened if path.startswith("/dev/")]


def test_the_mounted_medium_is_blocked_and_untouched(tmp_path: Path) -> None:
    stages = {stage["stage"]: stage for stage in run(tmp_path / "sim")["stages"]}
    workflow = stages["PREFLIGHT"]["blocked_device"]["workflow"]
    assert workflow["state"] == "BLOCKED"
    assert any("mounted" in reason for reason in workflow["why_blocked"])
    assert stages["SIMULATED SANITIZATION"]["blocked_device_unchanged"] is True


def test_sanitization_and_verification_are_real_outcomes(tmp_path: Path) -> None:
    stages = {stage["stage"]: stage for stage in run(tmp_path / "sim")["stages"]}
    assert stages["PLAN"]["method"] == "SINGLE_PASS_OVERWRITE"
    assert "not achievable" in stages["PLAN"]["purge"]
    erase = stages["SIMULATED SANITIZATION"]
    assert erase["target_changed"] is True
    assert erase["bytes_written"] > 0
    assert erase["phases"][-2:] == ["VERIFY", "REPORT"]
    verify = stages["SIMULATED VERIFICATION"]
    assert verify["passed"] is True
    assert verify["bytes_checked"] > 0


def test_the_certificate_verifies_and_a_changed_copy_does_not(tmp_path: Path) -> None:
    stages = {stage["stage"]: stage for stage in run(tmp_path / "sim")["stages"]}
    report = stages["FORENSIC REPORT"]
    assert report["chain_status"] == "VALID"
    assert report["verdict"] in {"VERIFIED", "VERIFIED_WITH_LIMITATIONS"}
    assert any(BANNER in line for line in report["limitations"])
    cert = stages["CERTIFICATE"]
    assert cert["signature_valid"] is True
    assert cert["tamper_test"]["rejected"] is True


@pytest.mark.parametrize("target", ["/dev/null", "outside"])
def test_the_engine_is_never_given_anything_but_its_own_file(
    tmp_path: Path, target: str
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    path = Path(target) if target.startswith("/") else tmp_path / "outside.img"
    if not target.startswith("/"):
        path.write_bytes(b"\0" * 512)
    with pytest.raises(NotSimulated, match="Nothing was written"):
        _require_simulated(path, work)
