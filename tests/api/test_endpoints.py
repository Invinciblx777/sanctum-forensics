"""Every endpoint, and the two properties that must not regress.

The properties, stated once so the tests below can be read as instances of
them:

1. **Omitting a flag never wipes anything.** ``dry_run`` defaults to True in
   the request model, so a body that does not mention it simulates. This is the
   one direction the API is not allowed to fail in.
2. **A mismatched serial is refused with the core's own remediation text.** The
   API does not paraphrase it. An operator reads the sentence the library
   author wrote, not the API author's guess about a subsystem it does not
   implement.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.deps import AppServices
from fastapi.testclient import TestClient

from tests._loopback import LOOPBACK_BASE_URL

from .conftest import RecordingHelper

CONFIRMATION_REMEDIATION = (
    "Re-read the device serial from the capability report and type it exactly."
)


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------


def test_devices_returns_capability_and_hidden_area_reports(
    client: TestClient,
) -> None:
    answer = client.get("/devices").json()

    assert len(answer["devices"]) == 3
    first = answer["devices"][0]
    assert first["device"]["serial"] == "SYN-PURGE-1"
    assert first["capabilities"]["ata_sanitize_ops"] == ["BLOCK_ERASE_EXT"]
    assert first["hidden_areas"]["hidden_bytes"] == 3145728


def test_a_failed_probe_does_not_fail_the_whole_list(client: TestClient) -> None:
    """A USB bridge that blocks pass-through is the common case, not an error.

    A device list that returned 500 because one disk could not be interrogated
    would be useless on exactly the hardware an operator most needs to look at.
    """
    answer = client.get("/devices").json()
    system_disk = answer["devices"][2]

    assert system_disk["capabilities"] is None
    assert "root filesystem" in system_disk["capability_error"]


def test_devices_reports_helper_failure_as_a_limitation_not_a_500(
    services: object, tmp_path: Path
) -> None:
    """An unreachable helper must be actionable, and a 500 is not."""
    from api.deps import AppServices
    from api.jobs import JobRegistry
    from api.main import create_app

    class Unreachable:
        def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
            raise OSError("connection refused")

    broken = AppServices(
        registry=JobRegistry(),
        helper=Unreachable(),
        state_dir=tmp_path / "state",
    )
    broken.prepare()
    with TestClient(
        create_app(services=broken, serve_ui=False), base_url=LOOPBACK_BASE_URL
    ) as probe:
        answer = probe.get("/devices")

    assert answer.status_code == 200
    assert answer.json()["devices"] == []
    assert any("helper" in item for item in answer.json()["limitations"])


# --------------------------------------------------------------------------
# Dry run is the default
# --------------------------------------------------------------------------


def test_erase_drive_defaults_to_dry_run_when_the_flag_is_omitted(
    client: TestClient, helper: RecordingHelper
) -> None:
    """The single most important assertion in this file.

    A body with no ``dry_run`` key must simulate. If this ever fails, a
    forgotten field in a caller becomes a wiped disk.
    """
    answer = client.post("/jobs/erase-drive", json={"path": "/dev/sdz"})

    assert answer.status_code == 200
    assert answer.json()["dry_run"] is True

    client.get(f"/jobs/{answer.json()['job_id']}")
    erase_calls = [params for name, params in helper.calls if name == "run_erase"]
    assert erase_calls, "the job never reached the helper"
    assert all(call["dry_run"] is True for call in erase_calls)


def test_erase_files_defaults_to_dry_run_when_the_flag_is_omitted(
    client: TestClient, tmp_path: Path
) -> None:
    target = tmp_path / "keep.bin"
    target.write_bytes(b"intact")

    answer = client.post("/jobs/erase-files", json={"paths": [str(target)]})

    assert answer.status_code == 200
    assert answer.json()["dry_run"] is True
    client.get(f"/jobs/{answer.json()['job_id']}")
    assert target.read_bytes() == b"intact", "a defaulted request wrote to disk"


def test_erase_files_without_confirm_is_refused(client: TestClient) -> None:
    """Two gates. Turning off the first is not enough."""
    answer = client.post(
        "/jobs/erase-files", json={"paths": ["/tmp/x"], "dry_run": False}
    )

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert detail["kind"] == "ConfirmationMismatch"
    assert "opt-in twice" in detail["error"]


# --------------------------------------------------------------------------
# Serial confirmation
# --------------------------------------------------------------------------


def test_a_mismatched_serial_is_refused_with_the_remediation_verbatim(
    client: TestClient, helper: RecordingHelper
) -> None:
    answer = client.post(
        "/jobs/erase-drive",
        json={"path": "/dev/sdz", "dry_run": False, "typed_serial": "WRONG"},
    )

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert detail["kind"] == "ConfirmationMismatch"
    assert detail["remediation"] == CONFIRMATION_REMEDIATION, (
        "the remediation must cross the boundary verbatim, not paraphrased"
    )
    assert "SYN-PURGE-1" in detail["error"]

    assert not [name for name, _ in helper.calls if name == "run_erase"], (
        "a refused request must never reach the erase handler"
    )


def test_a_missing_serial_with_dry_run_off_is_refused_before_the_helper(
    client: TestClient, helper: RecordingHelper
) -> None:
    answer = client.post(
        "/jobs/erase-drive", json={"path": "/dev/sdz", "dry_run": False}
    )

    assert answer.status_code == 409
    assert answer.json()["detail"]["kind"] == "ConfirmationMismatch"
    assert helper.calls == [], "nothing should reach the helper at all"


def test_a_matching_serial_is_accepted(client: TestClient) -> None:
    answer = client.post(
        "/jobs/erase-drive",
        json={"path": "/dev/sdz", "dry_run": False, "typed_serial": "SYN-PURGE-1"},
    )

    assert answer.status_code == 200
    assert answer.json()["dry_run"] is False


def test_the_typed_serial_is_never_echoed_back(client: TestClient) -> None:
    """It is a confirmation token, not a fact worth repeating.

    Echoing it into a UI that may be screen-shared, or into a browser cache,
    spreads the one string that authorises a destructive operation.
    """
    accepted = client.post(
        "/jobs/erase-drive",
        json={"path": "/dev/sdz", "dry_run": False, "typed_serial": "SYN-PURGE-1"},
    ).json()

    status = client.get(f"/jobs/{accepted['job_id']}").json()
    assert status["params"]["typed_serial"] == "<redacted>"
    assert "SYN-PURGE-1" not in json.dumps(status["params"])


def test_an_unknown_device_is_refused_with_its_remediation(
    client: TestClient,
) -> None:
    answer = client.post("/jobs/erase-drive", json={"path": "/dev/nope"})

    assert answer.status_code in {409, 410}
    assert answer.json()["detail"]["remediation"]


# --------------------------------------------------------------------------
# Acquire and carve
# --------------------------------------------------------------------------


def test_acquire_rejects_a_missing_source(client: TestClient) -> None:
    answer = client.post(
        "/jobs/acquire", json={"source": "/nope.dd", "dest": "/tmp/out.dd"}
    )
    assert answer.status_code == 422
    assert answer.json()["detail"]["kind"] == "EvidenceIntegrityError"


def test_carve_rejects_a_missing_image(client: TestClient) -> None:
    answer = client.post("/jobs/carve", json={"image": "/nope.dd"})
    assert answer.status_code == 422
    assert answer.json()["detail"]["kind"] == "EvidenceIntegrityError"


# --------------------------------------------------------------------------
# Job lifecycle
# --------------------------------------------------------------------------


def test_an_unknown_job_is_a_clean_error_not_a_traceback(
    client: TestClient,
) -> None:
    answer = client.get("/jobs/does-not-exist")
    assert answer.status_code in {409, 410}
    assert "remediation" in answer.json()["detail"]
    assert "Traceback" not in answer.text


def test_cancel_is_accepted_and_recorded(client: TestClient) -> None:
    accepted = client.post("/jobs/erase-drive", json={"path": "/dev/sdz"}).json()
    answer = client.post(f"/jobs/{accepted['job_id']}/cancel")
    assert answer.status_code == 200
    assert "cancel_requested" in answer.json()


def test_job_status_carries_its_ledger_entries(
    client: TestClient, tmp_path: Path
) -> None:
    """The registry's buffer dies with the process; the chain does not.

    That is what makes a job resumable beyond a page reload, and it is why the
    status endpoint joins the two.
    """
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 64)
    accepted = client.post(
        "/jobs/erase-files", json={"paths": [str(target)]}
    ).json()

    import time

    for _ in range(200):
        status = client.get(f"/jobs/{accepted['job_id']}").json()
        if status["state"] in {"complete", "failed"}:
            break
        time.sleep(0.02)

    assert status["state"] == "complete", status.get("error")
    assert status["ledger_entries"], "the file erase must have ledgered its phases"


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


def test_ledger_verify_reports_a_valid_chain(client: TestClient) -> None:
    answer = client.get("/ledger/verify")
    assert answer.status_code == 200
    assert answer.json()["status"] in {"VALID", "EMPTY"}


def test_ledger_entries_come_back_newest_first(
    client: TestClient, tmp_path: Path
) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 32)
    client.post("/jobs/erase-files", json={"paths": [str(target)]})

    import time

    time.sleep(0.6)
    entries = client.get("/ledger/entries").json()["entries"]
    if len(entries) > 1:
        assert entries[0]["seq"] > entries[-1]["seq"]


# --------------------------------------------------------------------------
# Transport safety
# --------------------------------------------------------------------------


def test_every_response_carries_a_restrictive_csp(client: TestClient) -> None:
    """`default-src 'self'` makes an accidental CDN link fail loudly.

    Without it a stray external reference works on the developer's machine and
    breaks at the venue, which is the worst possible place to find out.
    """
    headers = client.get("/health").headers
    policy = headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_the_interactive_docs_are_not_served(client: TestClient) -> None:
    """FastAPI's default Swagger UI loads its assets from a CDN."""
    assert client.get("/docs").status_code in {404, 405}
    assert client.get("/redoc").status_code in {404, 405}


def test_the_app_is_configured_for_loopback_only() -> None:
    from api.main import LOOPBACK_HOST

    assert LOOPBACK_HOST == "127.0.0.1"

    source = Path("api/main.py").read_text(encoding="utf-8")
    assert "0.0.0.0" not in source, (
        "binding a wildcard address would make this a remote wipe primitive"
    )


# --------------------------------------------------------------------------
# The unhandled-exception handler discloses nothing about this host
# --------------------------------------------------------------------------


def test_an_unanticipated_failure_returns_an_incident_id_and_not_the_message(
    services: AppServices,
) -> None:
    """The body used to be ``f"{type(exc).__name__}: {exc}"``.

    The exceptions that reach this handler are the ones nobody anticipated, and
    their messages quote host paths: "[Errno 13] Permission denied:
    '/var/lib/sanctum/ledger/chain.jsonl'". An error a layer *did* anticipate
    carries a remediation its author wrote and is returned verbatim long before
    it could get here, so reaching this handler means there is no such sentence
    to pass through.
    """
    from api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(services=services, serve_ui=False)

    @app.get("/boom-for-the-test")
    def _boom() -> dict[str, str]:
        raise OSError(
            "[Errno 13] Permission denied: '/var/lib/sanctum/secret/chain.jsonl'"
        )

    with TestClient(
        app, raise_server_exceptions=False, base_url=LOOPBACK_BASE_URL
    ) as client:
        answer = client.get("/boom-for-the-test")

    assert answer.status_code == 500
    body = answer.json()
    assert "/var/lib/sanctum" not in answer.text
    assert "Permission denied" not in answer.text
    assert "Errno" not in answer.text
    assert body["kind"] == "InternalError"
    # The operator is given the string to grep the server log for.
    assert len(body["incident"]) == 12
    assert body["incident"] in body["error"]
    assert body["incident"] in body["remediation"]
