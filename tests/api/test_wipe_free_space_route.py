"""POST /jobs/wipe-free-space: every gate answers before a job exists.

Nothing here fills a volume. The fill is exercised on udisks loop volumes in
``tests/erase/files/test_free_space_wipe_carve.py``; this file checks that the
route refuses what it must with the right status and remediation, and that a
defaulted request simulates.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from core.models import VolumeInfo
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the volume is resolved from /proc/mounts"
)


def _fake_volume(point: Path, st_dev: int) -> VolumeInfo:
    return VolumeInfo(
        mount_point=str(point),
        fs_type="exfat",
        source="/dev/sdz1",
        fs_uuid="7BF9-380B",
        identifier="7BF9-380B",
        st_dev=st_dev,
        frsize=4096,
        trim_likely=None,
    )


def _patch_volume(monkeypatch: pytest.MonkeyPatch, volume: VolumeInfo) -> None:
    from core.erase import freespace

    monkeypatch.setattr(freespace, "resolve_volume", lambda path: volume)


def test_a_folder_that_is_not_a_mount_point_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    answer = client.post("/jobs/wipe-free-space", json={"mount_point": str(tmp_path)})

    assert answer.status_code == 422
    detail = answer.json()["detail"]
    assert detail["kind"] == "UnsupportedCapability"
    assert "not a mount point" in detail["error"]


def test_the_system_volume_is_refused_before_a_job_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_volume(monkeypatch, _fake_volume(tmp_path, os.stat("/").st_dev))

    answer = client.post("/jobs/wipe-free-space", json={"mount_point": str(tmp_path)})

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert detail["kind"] == "SystemDiskRefused"
    assert detail["remediation"]


def test_the_volume_holding_the_ledger_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, services: object
) -> None:
    ledger_root = services.ledger_root  # type: ignore[attr-defined]
    ledger_root.mkdir(parents=True, exist_ok=True)
    _patch_volume(monkeypatch, _fake_volume(ledger_root, os.stat(ledger_root).st_dev))

    answer = client.post(
        "/jobs/wipe-free-space", json={"mount_point": str(ledger_root)}
    )

    assert answer.status_code == 409
    assert answer.json()["detail"]["kind"] == "SystemDiskRefused"


def test_a_real_run_without_the_identifier_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    point = tmp_path / "volume"
    point.mkdir()
    _patch_volume(monkeypatch, _fake_volume(point, -1))

    answer = client.post(
        "/jobs/wipe-free-space",
        json={"mount_point": str(point), "dry_run": False, "typed_identifier": ""},
    )

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert detail["kind"] == "ConfirmationMismatch"
    assert "7BF9-380B" in detail["remediation"]
    assert list(point.iterdir()) == []


def test_a_defaulted_request_simulates_and_writes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    point = tmp_path / "volume"
    point.mkdir()
    _patch_volume(monkeypatch, _fake_volume(point, -1))

    answer = client.post("/jobs/wipe-free-space", json={"mount_point": str(point)})

    assert answer.status_code == 200
    body = answer.json()
    assert body["dry_run"] is True
    assert body["kind"] == "wipe-free-space"

    state: dict[str, object] = {}
    for _ in range(200):
        state = client.get(f"/jobs/{body['job_id']}").json()
        if state.get("state") not in {"pending", "running"}:
            break
        time.sleep(0.02)
    assert state.get("state") == "complete", state
    result = state["result"]
    assert isinstance(result, dict)
    assert result["dry_run"] is True
    assert result["bytes_written"] == 0
    assert result["verified"] is None
    assert list(point.iterdir()) == []
