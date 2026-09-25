"""API gate and helper seam together, over the real helper dispatch.

Unlike the rest of the API suite, the helper here is ``InProcessHelper`` - the
real allowlist, the real ``run_erase`` handler and the real write-seam check -
over a *synthetic, mutable device*. Only two things are replaced: the host reads
(``get_device`` and the capability probe, which answer from a dict this test can
change) and the engine itself (a tripwire that records if it was entered). No
device is opened.

The second test is the approval-to-write race made deterministic: the device is
changed *after* the API gate has passed and *before* the helper starts, exactly
the window the API cannot see. The helper must refuse it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api.authorization import AuthorizationStore
from api.deps import AppServices
from api.jobs import JobRegistry
from api.main import create_app
from core.models import Device, DeviceCapabilities, SanitizationLevel
from fastapi.testclient import TestClient
from helper.daemon import InProcessHelper

from tests._loopback import LOOPBACK_BASE_URL

from .conftest import MIB, approve_workflow, open_workflow

REAL = {"path": "/dev/sdz", "dry_run": False, "typed_serial": "SYN-PURGE-1"}


class World:
    """The synthetic host: what the helper would read from the device now."""

    def __init__(self) -> None:
        self.fields: dict[str, Any] = {
            "path": "/dev/sdz",
            "model": "SYNTHETIC-PURGE",
            "serial": "SYN-PURGE-1",
            "size_bytes": 64 * MIB,
            "rotational": False,
            "transport": "sata",
            "is_system_disk": False,
            "mounted_at": [],
            "pt_type": "gpt",
            "by_id_path": None,
        }
        self.caps: dict[str, Any] = {
            "ata_security_erase": True,
            "ata_enhanced_erase": True,
            "ata_sanitize_ops": [],
            "nvme_sanicap": {},
            "is_sed_opal": False,
            "security_frozen": False,
            "est_erase_seconds": 120,
            "achievable_levels": {SanitizationLevel.CLEAR, SanitizationLevel.PURGE},
            "limitations": [],
        }
        self.engine_entries: list[Any] = []

    def device(self) -> Device:
        return Device(**self.fields)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    state = World()
    monkeypatch.setattr(
        "core.device.enumerate.get_device", lambda path, *a, **k: state.device()
    )
    monkeypatch.setattr(
        "core.device.capabilities.probe",
        lambda device, *a, **k: DeviceCapabilities.model_validate(state.caps),
    )

    def engine(self: Any, params: dict[str, Any]) -> Any:
        state.engine_entries.append(params.get("dry_run"))
        yield from ()
        return {"result": {}}

    for name in ("execute_drive_sanitization", "resume_drive_sanitization"):
        monkeypatch.setattr(f"core.platform.linux.LinuxAdapter.{name}", engine)
    return state


@pytest.fixture
def seam_services(tmp_path: Path, world: World) -> AppServices:
    built = AppServices(
        registry=JobRegistry(),
        helper=InProcessHelper(),
        state_dir=tmp_path / "state",
        key_dir=tmp_path / "keys",
    )
    built.prepare()
    return built


@pytest.fixture
def seam_client(seam_services: AppServices) -> Iterator[TestClient]:
    app = create_app(services=seam_services, serve_ui=False)
    with TestClient(app, base_url=LOOPBACK_BASE_URL) as client:
        yield client


def _finish(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()
        if status["state"] != "running":
            return dict(status)
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def test_the_whole_chain_reaches_the_engine_through_the_real_helper(
    seam_client: TestClient, seam_services: AppServices, world: World
) -> None:
    auth_id = open_workflow(seam_client, seam_services)
    approve_workflow(seam_client, auth_id)
    accepted = seam_client.post(
        "/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}
    )
    assert accepted.status_code == 200, accepted.text
    status = _finish(seam_client, accepted.json()["job_id"])
    assert status["state"] == "complete", status
    assert world.engine_entries == [False]
    # The binding is for the helper; a client reading the job never sees it.
    assert status["params"]["authorization"] == "<redacted>"
    assert status["params"]["authorization_dir"] == "<redacted>"


def test_a_device_changed_after_the_api_gate_is_refused_by_the_helper(
    seam_client: TestClient,
    seam_services: AppServices,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window the API cannot see: after its gate, before the helper writes."""
    auth_id = open_workflow(seam_client, seam_services)
    approve_workflow(seam_client, auth_id)

    real_stream = seam_services.helper.call_stream

    def swapped_before_helper_starts(method: str, params: dict[str, Any]) -> Any:
        world.fields["model"] = (
            "A-DIFFERENT-DISK"  # model is not what typed serial covers
        )
        return real_stream(method, params)

    monkeypatch.setattr(
        seam_services.helper, "call_stream", swapped_before_helper_starts
    )
    accepted = seam_client.post(
        "/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}
    )
    assert accepted.status_code == 200, "the API gate passed; that is the premise"
    status = _finish(seam_client, accepted.json()["job_id"])
    assert status["state"] == "failed"
    assert "model changed" in (status["error"] or "")
    assert "Nothing was erased" in (status["error"] or "")
    assert world.engine_entries == [], "the helper never entered the engine"
    # The API had already spent it: fail closed, the operator opens a new one.
    assert AuthorizationStore(seam_services.state_dir / "authorizations").is_spent(
        auth_id
    )


def test_a_backup_changed_after_the_api_gate_is_refused_by_the_helper(
    seam_client: TestClient,
    seam_services: AppServices,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_id = open_workflow(seam_client, seam_services)
    approve_workflow(seam_client, auth_id)
    real_stream = seam_services.helper.call_stream
    image = seam_services.evidence_dir / "backup.img"

    def edited_before_helper_starts(method: str, params: dict[str, Any]) -> Any:
        with image.open("r+b") as handle:
            handle.write(b"edited after the gate")
        return real_stream(method, params)

    monkeypatch.setattr(
        seam_services.helper, "call_stream", edited_before_helper_starts
    )
    accepted = seam_client.post(
        "/jobs/erase-drive", json={**REAL, "authorization_id": auth_id}
    )
    status = _finish(seam_client, accepted.json()["job_id"])
    assert status["state"] == "failed"
    assert "backup" in (status["error"] or "")
    assert world.engine_entries == []


def test_a_simulation_through_the_real_helper_carries_no_authorization(
    seam_client: TestClient, seam_services: AppServices, world: World
) -> None:
    accepted = seam_client.post("/jobs/erase-drive", json={"path": "/dev/sdz"})
    status = _finish(seam_client, accepted.json()["job_id"])
    assert status["state"] == "complete", status
    assert "authorization" not in status["params"]
    assert world.engine_entries == [True]
