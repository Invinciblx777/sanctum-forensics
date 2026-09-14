"""The privilege boundary: an allowlist, and two gates that live behind it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from helper.daemon import OPERATIONS, HelperDaemon, InProcessHelper

from helper import rpc


def test_the_operation_allowlist_is_closed() -> None:
    """A method name that is not in the table is refused.

    The daemon never accepts a shell string and never spawns a shell; a request
    names an operation, and the name is a lookup key in this table.
    """
    assert set(OPERATIONS) == {
        "enumerate_devices",
        "probe_capabilities",
        "detect_hidden_areas",
        "run_erase",
        "acquire_image",
    }

    daemon = HelperDaemon(operator_uid=os.getuid())
    with pytest.raises(KeyError, match="not an allowed helper operation"):
        daemon._dispatch("rm -rf /", {})
    with pytest.raises(KeyError):
        daemon._dispatch("os.system", {})


def test_an_unknown_method_comes_back_as_an_error_frame_not_a_traceback() -> None:
    """A traceback from a root process describes the host to the caller."""
    daemon = HelperDaemon(operator_uid=os.getuid())
    reply = daemon.handle_frame(
        rpc.encode_request("not_a_real_operation", {}, req_id=1)
    )

    payload = json.loads(reply)
    assert "error" in payload
    assert "Traceback" not in reply.decode()
    assert payload["error"]["data"]["remediation"]


def test_a_malformed_frame_is_answered_rather_than_crashing_the_daemon() -> None:
    daemon = HelperDaemon(operator_uid=os.getuid())
    reply = json.loads(daemon.handle_frame(b"{not json"))
    assert "error" in reply


def test_the_in_process_helper_shares_the_daemon_allowlist() -> None:
    """The substitution cannot widen what the API is able to ask for."""
    helper = InProcessHelper()
    with pytest.raises(rpc.RpcError) as caught:
        helper.call("anything_else", {})
    assert "not an allowed helper operation" in caught.value.message


def _device(serial: str = "SYN-1") -> Any:
    from core.models import Device

    return Device(
        path="/dev/fake",
        model="SYNTHETIC",
        serial=serial,
        size_bytes=1024 * 1024,
        rotational=True,
        transport="sata",
        is_system_disk=False,
        mounted_at=[],
        pt_type=None,
        by_id_path=None,
    )


def test_run_erase_defaults_to_a_dry_run_when_the_flag_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate lives behind the boundary, so the API cannot forget it.

    A request that omits ``dry_run`` simulates. Checked here, in the privileged
    process, rather than only in the layer that cannot be trusted to hold it.
    """
    import core.erase.drive as drive_mod
    from core.models import EraseResult

    seen: dict[str, Any] = {}

    def fake_execute(job: Any, capabilities: Any, *, ledger: Any) -> Any:
        seen["dry_run"] = job.dry_run
        seen["confirmed_serial"] = job.confirmed_serial

        def generator() -> Any:
            if False:  # pragma: no cover - makes this a generator
                yield
            return _result(job)

        return generator()

    def _result(job: Any) -> EraseResult:
        from datetime import UTC, datetime

        from core.models import (
            EraseMethod,
            ErasePlan,
            ResidualRiskAssessment,
            SanitizationLevel,
        )

        return EraseResult(
            job_id=job.job_id,
            method=EraseMethod.SINGLE_PASS_OVERWRITE,
            level=SanitizationLevel.CLEAR,
            dry_run=job.dry_run,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            bytes_written=0,
            passes=0,
            plan=ErasePlan(
                method=EraseMethod.SINGLE_PASS_OVERWRITE,
                level=SanitizationLevel.CLEAR,
                justification="test",
                est_seconds=1,
            ),
            residual_risk=ResidualRiskAssessment(
                level="low", factors=[], purge_achieved=False, notes=""
            ),
        )

    monkeypatch.setattr("core.device.enumerate.get_device", lambda path: _device())
    monkeypatch.setattr("core.device.capabilities.probe", lambda device: None)
    monkeypatch.setattr(drive_mod, "execute", fake_execute)

    daemon = HelperDaemon(operator_uid=os.getuid())
    answer = daemon._dispatch(
        "run_erase",
        {
            "path": "/dev/fake",
            "job_id": "j",
            "ledger_root": str(tmp_path / "ledger"),
        },
    )

    assert seen["dry_run"] is True, "an omitted dry_run flag must simulate"
    assert answer["result"]["dry_run"] is True


def test_run_erase_refuses_a_mismatched_serial_behind_the_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-checked against the serial this process reads, not the one it was sent.

    A stale UI cannot authorise a wipe of a device that was swapped since the
    page loaded.

    The refusal is now raised by :func:`core.device.guard.assert_serial_confirmed`
    rather than by a second comparison inside the handler, so this asserts the
    property - a mismatch is refused, the device is named, and nothing was
    modified - instead of the deleted duplicate's exact sentence. The guard
    deliberately does not echo the device's real serial into the message; it is
    the token that authorises the operation, and ``api.jobs._redact`` exists to
    keep it from being repeated back.
    """
    from core.errors import ConfirmationMismatch

    monkeypatch.setattr(
        "core.device.enumerate.get_device", lambda path: _device("ACTUAL-SERIAL")
    )

    daemon = HelperDaemon(operator_uid=os.getuid())
    with pytest.raises(ConfirmationMismatch) as caught:
        daemon._dispatch(
            "run_erase",
            {
                "path": "/dev/fake",
                "job_id": "j",
                "dry_run": False,
                "typed_serial": "WHAT-THE-UI-BELIEVED",
                "ledger_root": str(tmp_path / "ledger"),
            },
        )

    assert "/dev/fake" in caught.value.message
    assert "does not match" in caught.value.message
    assert "Nothing has been modified" in caught.value.remediation


def _serial_less_device() -> Any:
    """A stick that reports no serial but does have a stable by-id link.

    Not a hypothetical: the USB media used for hardware validation is this
    shape whenever the bridge declines to pass the serial through.
    """
    from core.models import Device

    return Device(
        path="/dev/fake",
        model="SYNTHETIC",
        serial="",
        size_bytes=1024 * 1024,
        rotational=True,
        transport="usb",
        is_system_disk=False,
        mounted_at=[],
        pt_type=None,
        by_id_path="/dev/disk/by-id/usb-SYNTHETIC_no_serial-0:0",
    )


def test_a_device_with_no_serial_is_confirmed_by_its_by_id_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate the helper's own copy of the rule used to make unpassable.

    ``core.device.guard.assert_serial_confirmed`` accepts the stable by-id path
    for a device that reports no serial, because there is nothing else the
    operator can read off the capability report. The helper's duplicate demanded
    an exact serial match, so it rejected that path and accepted ``""`` - which
    then failed further in, naming a gate the operator had not touched. The net
    effect was that a serial-less device could not be erased at all.

    This asserts only that the confirmation gate is passed. What follows fails
    for want of a real device, which is a different refusal and the point.
    """
    from core.errors import ConfirmationMismatch

    monkeypatch.setattr(
        "core.device.enumerate.get_device", lambda path: _serial_less_device()
    )

    daemon = HelperDaemon(operator_uid=os.getuid())
    with pytest.raises(Exception) as caught:  # noqa: B017 - the kind is the assertion
        daemon._dispatch(
            "run_erase",
            {
                "path": "/dev/fake",
                "job_id": "j",
                "dry_run": False,
                "typed_serial": "/dev/disk/by-id/usb-SYNTHETIC_no_serial-0:0",
                "ledger_root": str(tmp_path / "ledger"),
            },
        )

    assert not isinstance(caught.value, ConfirmationMismatch), (
        "the by-id path is the only confirmation value a serial-less device has; "
        f"refusing it makes the device unerasable: {caught.value}"
    )


def test_a_serial_less_device_still_refuses_a_wrong_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Delegating the check must not have widened it."""
    from core.errors import ConfirmationMismatch

    monkeypatch.setattr(
        "core.device.enumerate.get_device", lambda path: _serial_less_device()
    )

    daemon = HelperDaemon(operator_uid=os.getuid())
    for typed in ("", "not-the-by-id-path", "/dev/fake"):
        with pytest.raises(ConfirmationMismatch):
            daemon._dispatch(
                "run_erase",
                {
                    "path": "/dev/fake",
                    "job_id": "j",
                    "dry_run": False,
                    "typed_serial": typed,
                    "ledger_root": str(tmp_path / "ledger"),
                },
            )


def test_the_helper_does_not_reimplement_the_confirmation_rule() -> None:
    """Static guard: one implementation, called from both sides of the boundary.

    A second comparison here is a second place the rule can be fixed in one
    spot and left wrong in the other, which is exactly what happened.
    """
    import helper.daemon as daemon_module

    source = Path(daemon_module.__file__).read_text(encoding="utf-8")

    assert "guard.assert_serial_confirmed(device, typed_serial)" in source
    assert "typed_serial != device.serial" not in source


def test_the_socket_is_created_owner_only(tmp_path: Path) -> None:
    """Mode 0600, set after bind because the umask applies during it."""
    import stat

    socket_path = tmp_path / "helper.sock"
    daemon = HelperDaemon(operator_uid=os.getuid(), socket_path=str(socket_path))
    try:
        daemon.bind()
        mode = stat.S_IMODE(socket_path.stat().st_mode)
        assert mode == 0o600, f"socket mode is {oct(mode)}, not 0600"
    finally:
        daemon.close()
