"""A Linux block device is root:disk 0660, so its acquisition goes to the helper.

Before this, ``/jobs/acquire`` opened every source in the API process. The
capability resolver said "available" once the helper was running, the UI enabled
Acquire, and the job then failed with Permission denied on the device node.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from api.deps import AppServices
from fastapi.testclient import TestClient

from .conftest import settle


class _StreamingHelper:
    """Answers ``acquire_image`` as the daemon would and records the request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"unexpected non-streaming call {method}")

    def call_stream(
        self, method: str, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        self.requests.append((method, dict(params)))
        yield {
            "job_id": params["job_id"],
            "phase": "ACQUIRE",
            "pct_bp": 10_000,
            "bytes_done": 1,
            "bytes_total": 1,
            "throughput_bytes_per_sec": 1,
            "eta_seconds": 0,
            "message": "fixture",
        }
        return {"record": {"job_id": params["job_id"]}}


@pytest.fixture
def block_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.routes.jobs._is_block_device", lambda path: True)
    adapter = SimpleNamespace(
        inspect_device=lambda path: SimpleNamespace(
            serial="SER-1234", capacity_bytes=4096
        )
    )
    monkeypatch.setattr("core.platform.current_adapter", lambda: adapter)


@pytest.mark.usefixtures("block_device")
def test_a_block_device_is_acquired_through_the_helper_bound_to_its_serial(
    client: TestClient, services: AppServices
) -> None:
    helper = _StreamingHelper()
    services.helper = helper  # type: ignore[assignment]

    accepted = client.post(
        "/jobs/acquire",
        json={
            "source": "/dev/sdz",
            "dest": "stick.dd",
            "expected_serial": "SER-1234",
        },
    )
    assert accepted.status_code == 200, accepted.text
    settle(services, accepted.json()["job_id"])

    [(method, params)] = helper.requests
    assert method == "acquire_image"
    assert params["source"] == "/dev/sdz"
    assert Path(params["dest"]).parent == services.evidence_dir
    assert params["expected_serial"] == "SER-1234"
    assert params["expected_size"] == 4096
    assert params["ledger_root"] == str(services.ledger_root)


@pytest.mark.usefixtures("block_device")
def test_a_block_device_without_the_matching_serial_is_refused_untouched(
    client: TestClient, services: AppServices
) -> None:
    helper = _StreamingHelper()
    services.helper = helper  # type: ignore[assignment]

    refused = client.post(
        "/jobs/acquire",
        json={"source": "/dev/sdz", "dest": "stick.dd", "expected_serial": "WRONG"},
    )
    assert refused.status_code >= 400
    assert helper.requests == []
