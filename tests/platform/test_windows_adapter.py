"""Windows adapter, from a captured Storage-module inventory, on any host.

What is pinned: which disks are protected and why (boot, system, page file,
hibernation file), that a mounted disk is refused, that discovery runs the
in-box PowerShell by absolute path with the script encoded rather than
quoted, and that whole-drive sanitization is refused with a reason rather than
routed anywhere.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from core.device._sysio import CommandResult
from core.errors import PlatformUnsupported
from core.platform.model import CapabilityStatus, Operation
from core.platform.windows import (
    INVENTORY_SCRIPT,
    WindowsAdapter,
    encoded_script,
    parse_inventory,
)

from .conftest import FakeRunner, ok


def _by_id(inventory: dict[str, Any]) -> dict[str, Any]:
    return {device.id: device for device in parse_inventory(inventory)}


def _adapter(inventory: dict[str, Any]) -> tuple[WindowsAdapter, FakeRunner]:
    runner = FakeRunner(lambda argv: ok(argv, json.dumps(inventory)))
    return WindowsAdapter(runner=runner), runner


def test_the_boot_disk_is_protected_for_every_reason_windows_gives(
    windows_inventory: dict[str, Any],
) -> None:
    boot = _by_id(windows_inventory)["PhysicalDrive0"]

    assert boot.system_device is True
    joined = " ".join(boot.system_reasons)
    assert "IsBoot" in joined
    assert "IsSystem" in joined
    assert "system volume C:" in joined
    assert "hiberfil.sys" in joined
    assert boot.mounted is True
    assert "C:\\" in boot.mount_points
    assert boot.media_type == "ssd"
    assert boot.interface == "nvme"
    assert boot.serial == "S5GXNX0T123456A", "trailing padding is stripped"
    assert boot.filesystems == ["NTFS"]


def test_a_data_disk_holding_the_page_file_is_a_system_device(
    windows_inventory: dict[str, Any],
) -> None:
    """Not the boot disk, and still unerasable: the page file lives on D:."""
    data = _by_id(windows_inventory)["PhysicalDrive1"]

    assert data.system_device is True
    assert any("page file" in reason for reason in data.system_reasons)
    assert data.media_type == "hdd", "numeric MediaType 3 decodes to HDD"
    assert data.interface == "sata", "numeric BusType 11 decodes to SATA"
    assert data.removable is False


def test_an_unmounted_usb_stick_is_not_protected_and_is_treated_as_flash(
    windows_inventory: dict[str, Any],
) -> None:
    stick = _by_id(windows_inventory)["PhysicalDrive2"]

    assert stick.system_device is False
    assert stick.mounted is False, "a GUID volume path alone is not a mount"
    assert stick.removable is True
    assert stick.media_type == "flash"
    assert "treated as flash" in stick.media_basis
    assert stick.path == "\\\\.\\PhysicalDrive2"


def test_a_disk_with_no_serial_says_so(windows_inventory: dict[str, Any]) -> None:
    t7 = _by_id(windows_inventory)["PhysicalDrive3"]

    assert t7.serial == "S6WXNS0R998877", "the physical-disk serial is the fallback"
    windows_inventory["physical"][3]["SerialNumber"] = ""
    t7 = _by_id(windows_inventory)["PhysicalDrive3"]
    assert t7.serial == ""
    assert any("no serial" in item for item in t7.limitations)
    assert t7.stable_id == "SAMSUNG_T7_UID"


def test_discovery_runs_inbox_powershell_by_absolute_path_with_an_encoded_script(
    windows_inventory: dict[str, Any],
) -> None:
    adapter, runner = _adapter(windows_inventory)

    adapter.enumerate_devices()

    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv[0].lower().endswith("windowspowershell\\v1.0\\powershell.exe") or argv[
        0
    ].endswith("powershell.exe")
    assert "\\" in argv[0] or "/" in argv[0], "never a bare name looked up on PATH"
    assert "-EncodedCommand" in argv and "-Command" not in argv
    decoded = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode(
        "utf-16-le"
    )
    assert decoded == INVENTORY_SCRIPT
    assert encoded_script() == argv[argv.index("-EncodedCommand") + 1]


def test_the_inventory_script_takes_no_input() -> None:
    """A constant with no parameters: nothing a caller sends can reach it.

    PowerShell reads caller data through ``param()``, ``$args`` or ``$input``;
    the script uses none of them, and the adapter passes nothing after it.
    """
    lowered = INVENTORY_SCRIPT.lower()
    assert "param(" not in lowered
    assert "$args" not in lowered
    assert "$input" not in lowered
    assert "invoke-expression" not in lowered and "iex " not in lowered


def test_virtual_disks_are_hidden_unless_asked_for(
    windows_inventory: dict[str, Any],
) -> None:
    adapter, _ = _adapter(windows_inventory)

    assert "PhysicalDrive4" not in {d.id for d in adapter.enumerate_devices()}
    assert "PhysicalDrive4" in {
        d.id for d in adapter.enumerate_devices(include_virtual=True)
    }


def test_whole_drive_is_not_available_and_says_why(
    windows_inventory: dict[str, Any],
) -> None:
    adapter, _ = _adapter(windows_inventory)
    stick = next(d for d in adapter.enumerate_devices() if d.id == "PhysicalDrive2")

    assessment = adapter.assess_device(stick)

    assert assessment.headline == "NOT AVAILABLE"
    assert assessment.status is CapabilityStatus.UNSUPPORTED
    assert assessment.recommended is None
    assert "not implemented for Windows" in assessment.reason
    assert "Linux build" in assessment.recommended_action
    assert {option.level for option in assessment.unavailable} == {"PURGE", "CLEAR"}
    checks = {check.key: check for check in assessment.safety_checks}
    assert checks["not_system"].passed is True
    assert checks["not_mounted"].passed is True


def test_the_boot_disk_assessment_names_the_system_reason_first(
    windows_inventory: dict[str, Any],
) -> None:
    adapter, _ = _adapter(windows_inventory)
    boot = next(d for d in adapter.enumerate_devices() if d.id == "PhysicalDrive0")

    assessment = adapter.assess_device(boot)

    assert assessment.headline == "NOT AVAILABLE"
    assert "system or boot disk" in assessment.reason
    assert "external media" in assessment.recommended_action
    assert {c.key: c for c in assessment.safety_checks}["not_system"].passed is False


def test_executing_a_whole_drive_job_raises_before_anything_runs(
    windows_inventory: dict[str, Any],
) -> None:
    adapter, runner = _adapter(windows_inventory)

    with pytest.raises(PlatformUnsupported) as excinfo:
        next(
            adapter.execute_drive_sanitization(
                {"path": "PhysicalDrive2", "dry_run": False}
            )
        )

    assert "No operation was performed" in excinfo.value.message
    assert runner.calls == [], "not even discovery ran: nothing was touched"
    with pytest.raises(PlatformUnsupported):
        next(adapter.resume_drive_sanitization({"path": "PhysicalDrive2"}))


def test_a_failed_discovery_is_inconclusive_not_empty() -> None:
    runner = FakeRunner(
        lambda argv: CommandResult(argv, 1, "", "Get-Disk : Access denied")
    )
    adapter = WindowsAdapter(runner=runner)

    with pytest.raises(PlatformUnsupported, match="Access denied"):
        adapter.enumerate_devices()
    rows = {row.operation: row for row in adapter.operation_capabilities()}

    assert rows[Operation.DEVICE_DISCOVERY].status is CapabilityStatus.INCONCLUSIVE
    assert "Access denied" in rows[Operation.DEVICE_DISCOVERY].reason


def test_legacy_rows_keep_the_device_screen_fields(
    windows_inventory: dict[str, Any],
) -> None:
    adapter, _ = _adapter(windows_inventory)

    rows = adapter.device_rows()
    stick = next(r for r in rows if r["normalized"]["id"] == "PhysicalDrive2")

    assert stick["device"]["path"] == "\\\\.\\PhysicalDrive2"
    assert stick["device"]["transport"] == "usb"
    assert stick["capabilities"] is None
    assert "not implemented for Windows" in stick["capability_error"]
    assert stick["assessment"]["headline"] == "NOT AVAILABLE"
    assert stick["media"]["flash"] is True
