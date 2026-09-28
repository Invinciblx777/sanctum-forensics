"""The evidence reconciliation, the app's validation record and the claims agree.

``docs/validation/evidence-reconciliation-2026-09-28/reconciliation.json`` is
the per-capability statement of what has been physically validated. The app
does not read it; the app reads ``core/platform/validation_record.json``. These
tests pin the two together, so a capability cannot be physically validated in
one and UNVERIFIED in the other, and pin the claims the README and the UI make
to the same states.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from core.platform.validation import hardware_passed, load_record

ROOT = Path(__file__).resolve().parents[2]
RECONCILIATION = (
    ROOT / "docs" / "validation" / "evidence-reconciliation-2026-09-28"
    / "reconciliation.json"
)
STATES = {
    "PHYSICALLY VALIDATED",
    "SOFTWARE/SYNTHETIC ONLY",
    "SUPPORTED BUT NOT PHYSICALLY VALIDATED",
    "UNSUPPORTED / NOT IMPLEMENTED",
    "EVIDENCE MISSING",
}


def _capabilities() -> dict[str, dict[str, Any]]:
    data = json.loads(RECONCILIATION.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in data["capabilities"]}


def test_every_capability_has_exactly_one_known_state() -> None:
    for cap_id, entry in _capabilities().items():
        assert entry["status"] in STATES, cap_id


def test_physically_validated_means_a_recorded_physical_run() -> None:
    for cap_id, entry in _capabilities().items():
        if entry["status"] == "PHYSICALLY VALIDATED":
            assert entry["physical_runs"], cap_id
            for run in entry["physical_runs"]:
                for field in ("device", "date", "method", "result", "evidence"):
                    assert run.get(field), (cap_id, field)


@pytest.mark.parametrize(
    "cap_id",
    [cap_id for cap_id, entry in _capabilities().items() if "app_feature_key" in entry],
)
def test_the_app_record_agrees_with_the_reconciliation(cap_id: str) -> None:
    entry = _capabilities()[cap_id]
    platform, feature = entry["app_feature_key"].split(".", 1)
    record = load_record()
    if entry["status"] == "UNSUPPORTED / NOT IMPLEMENTED":
        rows = [
            row
            for row in record.get("features", [])
            if row.get("platform") == platform and row.get("feature") == feature
        ]
        assert rows and all(row["result"] == "UNSUPPORTED" for row in rows), cap_id
        return
    validated = entry["status"] == "PHYSICALLY VALIDATED"
    assert hardware_passed(platform, feature, record) is validated, cap_id


def test_windows_gaps_are_not_implemented_not_merely_untested() -> None:
    caps = _capabilities()
    for cap_id in ("windows_whole_drive", "windows_raw_acquisition"):
        assert caps[cap_id]["status"] == "UNSUPPORTED / NOT IMPLEMENTED"


def test_the_ui_names_each_unvalidated_capability_with_its_state() -> None:
    summary = (ROOT / "ui" / "src" / "lib" / "summary.ts").read_text(encoding="utf-8")
    block = summary[summary.index("export const NOT_PHYSICALLY_VALIDATED") :]
    block = block[block.index("= [") : block.index("\n]")]
    assert "Raw physical-device acquisition on Windows: not implemented" in block
    assert "Not implemented on Windows or macOS" in block
    assert "Backup restoration: not implemented" in block
    assert "never run on a drive" in block
    assert "No macOS physical device run is recorded" in block


def test_the_readme_separates_not_validated_from_not_implemented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip(): line
        for line in readme.splitlines()
        if line.startswith("> | ")
    }
    not_validated = rows["Supported, not physically validated"]
    not_implemented = rows["Not implemented"]
    assert "Firmware Purge" in not_validated
    assert "Windows whole-drive sanitization" in not_implemented
    assert "Windows raw physical-device acquisition" in not_implemented
    assert "Windows whole-drive" not in not_validated
    assert "raw physical-device acquisition" not in not_validated
