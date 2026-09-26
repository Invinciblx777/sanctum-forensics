"""UI -> API -> workflow -> physical-gate chain, as the Sanitize screen drives it.

The Sanitize screen makes exactly these calls, in this order, and nothing else
reaches a destructive path: open the workflow, approve, then execute with the
server-issued ``authorization_id``. Each test below is one step of that chain
against the synthetic helper; none opens a real device. ``run_erase`` on the
recording helper is the only path that writes, so "reached the write path" means
"the helper saw ``run_erase`` with ``dry_run=False``".
"""

from __future__ import annotations

import copy
import os
import time
from typing import Any

import pytest
from api.authorization import SIMULATION_MARK, AuthorizationStore
from api.deps import AppServices
from fastapi.testclient import TestClient

from . import conftest
from .conftest import RecordingHelper, approve_workflow, make_backup, open_workflow

REAL = {"path": "/dev/sdz", "dry_run": False, "typed_serial": "SYN-PURGE-1"}


def _real_writes(helper: RecordingHelper) -> list[dict[str, Any]]:
    return [
        params
        for name, params in helper.calls
        if name in {"run_erase", "resume_erase"} and params.get("dry_run") is False
    ]


def _refused(answer: Any, helper: RecordingHelper) -> dict[str, Any]:
    assert answer.status_code == 409, answer.text
    detail = answer.json()["detail"]
    assert detail["verdict"] == "REFUSED"
    assert detail["WHY BLOCKED"]
    assert detail["physical_device_modified"] is False
    assert _real_writes(helper) == []
    return dict(detail)


def _mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    mounted = copy.deepcopy(conftest.FAKE_DEVICES)
    mounted[0]["device"]["mounted_at"] = ["/run/media/x/SANCTUMREC"]
    monkeypatch.setattr(conftest, "FAKE_DEVICES", mounted)


def test_1_a_mounted_physical_fixture_cannot_open_a_workflow(
    client: TestClient,
    services: AppServices,
    helper: RecordingHelper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The screen shows BLOCKED with the server's reason; no record exists."""
    _mounted(monkeypatch)
    make_backup(services)
    answer = client.post(
        "/workflow/erase-drive", json={"path": "/dev/sdz", "backup_image": "backup.img"}
    )
    assert answer.status_code == 409
    assert answer.json()["detail"]["kind"] == "MountedRefused"
    assert "mounted" in answer.json()["detail"]["error"]
    assert not list((services.state_dir / "authorizations").glob("*"))
    assert _real_writes(helper) == []


def test_2_opening_the_workflow_returns_a_server_state_and_plan(
    client: TestClient, services: AppServices
) -> None:
    """Prerequisites met: the server derives HUMAN_APPROVAL_REQUIRED (not yet
    PLAN_READY, which core.workflow reserves for a recorded approval)."""
    auth_id = open_workflow(client, services)
    view = client.get(f"/workflow/erase-drive/{auth_id}").json()
    assert view["workflow"]["state"] == "HUMAN_APPROVAL_REQUIRED"
    assert view["approved"] is False
    assert view["plan"]["blocking"] == []
    assert view["backup"]["sha256"]
    approve_workflow(client, auth_id)
    assert (
        client.get(f"/workflow/erase-drive/{auth_id}").json()["workflow"]["state"]
        == "PLAN_READY"
    )


def test_3_execution_without_approval_is_refused(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    auth_id = open_workflow(client, services)
    detail = _refused(
        client.post("/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}),
        helper,
    )
    assert detail["workflow_state"] == "HUMAN_APPROVAL_REQUIRED"
    _refused(client.post("/jobs/erase-drive", json=REAL), helper)


def test_4_approval_with_the_wrong_serial_is_refused_and_records_nothing(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    auth_id = open_workflow(client, services)
    answer = client.post(
        f"/workflow/erase-drive/{auth_id}/approve",
        json={"typed_serial": "NOT-THE-SERIAL", "acknowledge_data_destruction": True},
    )
    assert answer.status_code == 409
    assert client.get(f"/workflow/erase-drive/{auth_id}").json()["approved"] is False
    _refused(
        client.post("/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}),
        helper,
    )


def test_5_a_valid_approval_returns_the_servers_authorization_record(
    client: TestClient, services: AppServices
) -> None:
    auth_id = open_workflow(client, services)
    answer = client.post(
        f"/workflow/erase-drive/{auth_id}/approve",
        json={"typed_serial": "SYN-PURGE-1", "acknowledge_data_destruction": True},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["authorization_id"] == auth_id, "the id is the server's, not new"
    assert body["approved"] is True
    assert body["approved_by"]
    stored = AuthorizationStore(services.state_dir / "authorizations").load(auth_id)
    assert stored is not None and stored.approved_by == body["approved_by"]
    operations = [entry.operation for entry in services.ledger().entries()]
    assert "erase.approved" in operations


def test_6_execution_with_the_authorization_enters_the_existing_write_path(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    answer = client.post(
        "/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["notice"] == ""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _real_writes(helper):
        time.sleep(0.02)
    assert len(_real_writes(helper)) == 1


def test_7_reusing_an_authorization_is_refused(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    body = {**REAL, "authorization_id": auth_id}
    assert client.post("/jobs/erase-drive", json=body).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _real_writes(helper):
        time.sleep(0.02)
    before = len(_real_writes(helper))
    answer = client.post("/jobs/erase-drive", json=body)
    assert answer.status_code == 409
    assert "already used" in " ".join(answer.json()["detail"]["WHY BLOCKED"])
    assert answer.json()["detail"]["physical_device_modified"] is False
    assert len(_real_writes(helper)) == before


def test_8_a_device_identity_change_after_approval_is_refused(
    client: TestClient,
    services: AppServices,
    helper: RecordingHelper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    swapped = copy.deepcopy(conftest.FAKE_DEVICES)
    swapped[0]["device"]["serial"] = "OTHER-DISK-9"
    monkeypatch.setattr(conftest, "FAKE_DEVICES", swapped)
    detail = _refused(
        client.post(
            "/jobs/erase-drive",
            json={**REAL, "typed_serial": "OTHER-DISK-9", "authorization_id": auth_id},
        ),
        helper,
    )
    assert any("serial" in reason for reason in detail["WHY BLOCKED"])
    assert not AuthorizationStore(services.state_dir / "authorizations").is_spent(
        auth_id
    ), "a refused request must not spend the authorization"


def test_9_a_changed_backup_after_approval_is_refused(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    """Same size, new mtime: the recorded backup is no longer the verified one."""
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    image = services.evidence_dir / "backup.img"
    stat = image.stat()
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    detail = _refused(
        client.post("/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}),
        helper,
    )
    assert any("backup" in reason for reason in detail["WHY BLOCKED"])


def test_10_a_dry_run_simulates_and_is_marked_without_any_authorization(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    answer = client.post("/jobs/erase-drive", json={"path": "/dev/sdz"})
    assert answer.status_code == 200
    assert answer.json()["dry_run"] is True
    assert (
        answer.json()["notice"]
        == SIMULATION_MARK
        == ("SIMULATION / NO PHYSICAL DEVICE MODIFIED")
    )
    assert not (services.state_dir / "authorizations").exists() or not list(
        (services.state_dir / "authorizations").glob("*")
    ), "a simulation must not create or need an authorization record"


def test_simulation_cannot_reach_the_physical_write_path(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    """A dry run never runs the write path, even when handed every credential.

    Sent with a valid, approved, unspent authorization *and* the right serial,
    a request with ``dry_run=true`` must reach the helper as ``dry_run=True``,
    must not consume the authorization, and must never be upgraded to a real
    write by any parameter the client supplies.
    """
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    store = AuthorizationStore(services.state_dir / "authorizations")

    for extra in ({}, {"authorization_id": auth_id, "typed_serial": "SYN-PURGE-1"}):
        answer = client.post(
            "/jobs/erase-drive", json={"path": "/dev/sdz", "dry_run": True, **extra}
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["dry_run"] is True
        assert answer.json()["notice"] == SIMULATION_MARK

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not [
        c for c in helper.calls if c[0] == "run_erase"
    ]:
        time.sleep(0.02)
    runs = [params for name, params in helper.calls if name == "run_erase"]
    assert runs, "the simulation should have run"
    assert all(params["dry_run"] is True for params in runs)
    assert _real_writes(helper) == []
    assert not store.is_spent(auth_id), "a simulation must not spend an authorization"

    # And the omitted flag defaults closed: no dry_run means a simulation.
    default = client.post("/jobs/erase-drive", json={"path": "/dev/sdz"})
    assert default.json()["dry_run"] is True


def test_a_simulated_job_cannot_be_resumed_into_a_real_write(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    from tests.api.test_resume import _checkpoint, _plan

    _plan(services, "erase-drive-sim1", "SINGLE_PASS_OVERWRITE")
    _checkpoint(services, "erase-drive-sim1", 1024)
    answer = client.post(
        "/jobs/erase-drive-sim1/resume",
        json={"dry_run": False, "typed_serial": "SYN-PURGE-1"},
    )
    _refused(answer, helper)


def test_missing_device_refuses_cleanly(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    make_backup(services)
    answer = client.post(
        "/workflow/erase-drive",
        json={"path": "/dev/does-not-exist", "backup_image": "backup.img"},
    )
    assert answer.status_code == 410
    assert answer.json()["detail"]["kind"] == "DeviceVanished"
    assert _real_writes(helper) == []


def test_a_wrong_serial_at_execution_is_a_structured_refusal_and_spends_nothing(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    answer = client.post(
        "/jobs/erase-drive",
        json={**REAL, "typed_serial": "WRONG", "authorization_id": auth_id},
    )
    detail = _refused(answer, helper)
    assert detail["kind"] == "ConfirmationMismatch"
    assert not AuthorizationStore(services.state_dir / "authorizations").is_spent(
        auth_id
    )


def _report_for(client: TestClient, job_id: str) -> dict[str, Any]:
    import json
    from pathlib import Path

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.get(f"/jobs/{job_id}").json()["state"] != "running":
            break
        time.sleep(0.02)
    answer = client.post(
        f"/reports/{job_id}", json={"case_id": "CASE-CERT", "operator": "t"}
    )
    assert answer.status_code == 200, answer.text
    return dict(json.loads(Path(answer.json()["json_path"]).read_text()))


def test_the_certificate_of_a_real_erase_names_device_levels_and_verification(
    client: TestClient, services: AppServices, helper: RecordingHelper
) -> None:
    auth_id = open_workflow(client, services)
    approve_workflow(client, auth_id)
    job_id = client.post(
        "/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}
    ).json()["job_id"]
    sections = _report_for(client, job_id)["sections"]
    assert sections["device_identity"]["serial"] == "SYN-PURGE-1"
    assert sections["device_identity"]["logical_block_size"] == 512
    assert sections["method"]["level_requested"] == "CLEAR"
    assert sections["method"]["level_achieved"] == "CLEAR"
    assert sections["verification"]["passed"] is True


def test_the_certificate_of_a_dry_run_says_nothing_was_achieved(
    client: TestClient, helper: RecordingHelper
) -> None:
    job_id = client.post("/jobs/erase-drive", json={"path": "/dev/sdz"}).json()[
        "job_id"
    ]
    sections = _report_for(client, job_id)["sections"]
    assert sections["method"]["level_achieved"].startswith("NONE (dry run")
    assert sections["verification"]["passed"] is False
    assert sections["verification"]["bytes_checked"] == 0
