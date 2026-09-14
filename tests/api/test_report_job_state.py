"""A report says what state its job was in, and refuses when that is no state.

MANUAL_REPORT FINDING 1. ``POST /reports/{job_id}`` read the registry and fell
back to ``{"state": "unknown", "result": None}`` on a miss, and nothing refused a
job still in ``running``. A report generated seconds into a carve was signed,
carried ``recovery: {}`` and ``objects_written: 0``, and passed every check. It
was not false; it invited one reading and supported another.

Two fixes, answering two different questions:

* **Refusal** stops the misleading artifact from existing. A running job has no
  result yet, and a job this process has forgotten has no result here at all.
  Those are different situations with different remedies, so they are refused
  with different kinds.
* **``job_state`` in ``case_identity``** means an artifact that does exist -
  for a failed or a cancelled job, both legitimate things to document - still
  says what it documents, inside the signed bytes.

FINDING 5 rides along: a failed or cancelled job has no result, so the report's
limitations used to collapse to the deployment's own. The one report most in
need of a caveat now carries one that names the state.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from api.deps import AppServices
from api.jobs import JobRegistry
from api.routes.audit import REPORT_GENERATED
from core.ledger.chain import Ledger
from core.models import Progress
from fastapi.testclient import TestClient


def _progress(job_id: str, step: int) -> Progress:
    return Progress(
        job_id=job_id,
        phase="signatures",
        pct_bp=min(step, 10_000),
        bytes_done=step,
        bytes_total=10_000,
        throughput_bytes_per_sec=0,
        eta_seconds=0,
        message=f"step {step}",
    )


def _report_entries(services: AppServices) -> list[Any]:
    chain = Ledger(services.ledger_root, tool_version="t", pubkey_fingerprint="")
    return [e for e in chain.entries() if e.operation == REPORT_GENERATED]


def _reports_on_disk(services: AppServices) -> list[Path]:
    return sorted(services.reports_dir.glob("*.forensic.*"))


def _generate(client: TestClient, job_id: str) -> Any:
    return client.post(
        f"/reports/{job_id}", json={"case_id": f"CASE-{job_id}", "operator": "tester"}
    )


def _document(answer: Any) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(Path(answer.json()["json_path"]).read_text())
    return loaded


def _acquire_job(client: TestClient, tmp_path: Path, name: str) -> str:
    source = tmp_path / f"{name}.dd"
    source.write_bytes(name.encode() * 512)
    accepted = client.post(
        "/jobs/acquire", json={"source": str(source), "dest": f"{name}.dd"}
    )
    assert accepted.status_code == 200, accepted.text
    job_id: str = accepted.json()["job_id"]
    deadline = time.monotonic() + 30
    while client.get(f"/jobs/{job_id}").json()["state"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return job_id


# --------------------------------------------------------------------------
# Refused: running
# --------------------------------------------------------------------------


def test_a_report_for_a_running_job_is_refused_and_nothing_is_written(
    client: TestClient, services: AppServices
) -> None:
    started = threading.Event()
    release = threading.Event()

    def factory() -> Generator[Progress, None, dict[str, Any]]:
        started.set()
        release.wait(timeout=10)
        yield _progress("carve-running", 1)
        return {}

    job_id = services.registry.submit("carve", {}, factory, job_id="carve-running")
    assert started.wait(timeout=5)
    try:
        answer = _generate(client, job_id)
    finally:
        release.set()
        services.registry.wait(job_id)

    assert answer.status_code == 409, answer.text
    detail = answer.json()["detail"]
    assert detail["kind"] == "JobNotFinished"
    assert "running" in detail["error"]
    assert f"GET /jobs/{job_id}" in detail["remediation"]
    assert _report_entries(services) == []
    assert _reports_on_disk(services) == []


# --------------------------------------------------------------------------
# Refused: unknown, in two distinguishable ways
# --------------------------------------------------------------------------


def test_a_report_for_a_job_nobody_has_seen_is_refused_as_unknown(
    client: TestClient, services: AppServices
) -> None:
    answer = _generate(client, "carve-never-submitted")

    assert answer.status_code == 404, answer.text
    detail = answer.json()["detail"]
    assert detail["kind"] == "JobNotKnown"
    assert "no entries" in detail["error"]
    assert detail["remediation"]
    assert _report_entries(services) == []
    assert _reports_on_disk(services) == []


def test_a_job_forgotten_by_a_restart_is_unknown_and_says_what_the_chain_holds(
    client: TestClient, services: AppServices, tmp_path: Path
) -> None:
    """Not the same verdict as running, and not the same message as never-seen.

    The job finished in an earlier process. Its result lived in that process;
    its chain entries did not, and the refusal names the terminal one so the
    operator is not left wondering whether the job ever ran.
    """
    job_id = _acquire_job(client, tmp_path, "restart")
    services.registry = JobRegistry()  # what a process restart leaves behind

    answer = _generate(client, job_id)

    assert answer.status_code == 404, answer.text
    detail = answer.json()["detail"]
    assert detail["kind"] == "JobNotKnown"
    assert "acquire.complete" in detail["error"]
    assert "restart" in detail["error"]
    assert _report_entries(services) == []


# --------------------------------------------------------------------------
# Generated, and marked: failed and cancelled
# --------------------------------------------------------------------------


def test_a_report_for_a_failed_job_carries_its_state_and_a_caveat(
    client: TestClient, services: AppServices
) -> None:
    def factory() -> Generator[Progress, None, dict[str, Any]]:
        yield _progress("carve-failed", 1)
        raise OSError("the image went away")

    job_id = services.registry.submit("carve", {}, factory, job_id="carve-failed")
    assert services.registry.wait(job_id).state == "failed"

    answer = _generate(client, job_id)

    assert answer.status_code == 200, answer.text
    document = _document(answer)
    assert document["sections"]["case_identity"]["job_state"] == "failed"
    items = document["sections"]["limitations"]["items"]
    caveat = items[0]
    assert "failed" in caveat
    assert "the image went away" in caveat
    assert len(_report_entries(services)) == 1


def test_a_report_for_a_cancelled_job_carries_its_state_and_a_caveat(
    client: TestClient, services: AppServices
) -> None:
    def factory() -> Generator[Progress, None, dict[str, Any]]:
        step = 0
        while True:
            step += 1
            yield _progress("carve-cancelled", step)
            time.sleep(0.005)

    job_id = services.registry.submit(
        "carve", {}, factory, job_id="carve-cancelled"
    )
    services.registry.cancel(job_id)
    assert services.registry.wait(job_id).state == "cancelled"

    answer = _generate(client, job_id)

    assert answer.status_code == 200, answer.text
    document = _document(answer)
    assert document["sections"]["case_identity"]["job_state"] == "cancelled"
    assert "cancelled" in document["sections"]["limitations"]["items"][0]


# --------------------------------------------------------------------------
# Unaffected: complete
# --------------------------------------------------------------------------


def test_a_report_for_a_completed_job_is_unaffected_apart_from_naming_its_state(
    client: TestClient, services: AppServices, tmp_path: Path
) -> None:
    job_id = _acquire_job(client, tmp_path, "whole")

    answer = _generate(client, job_id)

    assert answer.status_code == 200, answer.text
    document = _document(answer)
    assert document["sections"]["case_identity"]["job_state"] == "complete"
    for item in document["sections"]["limitations"]["items"]:
        assert "JOB " not in item
    verified = client.get(f"/reports/{job_id}/verify").json()
    assert verified["passed"] is True
