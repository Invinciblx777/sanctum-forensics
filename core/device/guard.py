"""Safety gate for destructive operations.

Enforces the CLAUDE.md non-negotiables. Every destructive path calls both
checks before it touches a device:

* :func:`assert_erasable` - refuse the system disk, refuse anything mounted.
* :func:`assert_serial_confirmed` - the operator must type the device's own
  serial. This is the second of the two opt-ins; dry-run being the default is
  the first.

Both raise on refusal and return ``None`` on success, so a caller cannot
accidentally proceed by ignoring a boolean.
"""

from __future__ import annotations

import structlog

from core.errors import ConfirmationMismatch, MountedRefused, SystemDiskRefused
from core.models import Device

__all__ = ["assert_erasable", "assert_serial_confirmed"]

logger = structlog.get_logger(__name__)


def assert_erasable(device: Device) -> None:
    """Raise if ``device`` is the system disk or has a mounted filesystem.

    Raises:
        SystemDiskRefused: The device holds the running root, ``/boot`` or swap.
        MountedRefused: One or more filesystems on the device are mounted.
    """
    if device.is_system_disk:
        logger.warning("erase_refused", path=device.path, reason="system_disk")
        raise SystemDiskRefused(
            f"{device.path} ({device.model}) holds the running system "
            "(root, /boot or active swap). Refusing to erase it."
        )
    if device.mounted_at:
        mounts = ", ".join(sorted(device.mounted_at))
        logger.warning("erase_refused", path=device.path, reason="mounted")
        raise MountedRefused(
            f"{device.path} has mounted filesystems: {mounts}. Refusing to erase it."
        )


def assert_serial_confirmed(device: Device, typed_serial: str) -> None:
    """Raise unless ``typed_serial`` identifies ``device``.

    The operator normally types the device serial. A device that reports no
    serial cannot be confirmed that way, so its stable ``/dev/disk/by-id`` path
    is accepted instead — still a value the operator must read off the
    capability report, never a value they can guess.

    Raises:
        ConfirmationMismatch: The typed value does not identify this device.
    """
    typed = typed_serial.strip()
    if not typed:
        raise ConfirmationMismatch(
            "No confirmation value was typed.",
            remediation=(
                f"Type the serial of {device.path} exactly as shown in the "
                "capability report to confirm."
            ),
        )

    if device.serial:
        if typed.casefold() == device.serial.strip().casefold():
            return
        logger.warning("confirmation_mismatch", path=device.path)
        raise ConfirmationMismatch(
            f"Typed value does not match the serial of {device.path}.",
            remediation=(
                "Re-read the device serial from the capability report and type "
                "it exactly. Nothing has been modified."
            ),
        )

    accepted = {value.casefold() for value in (device.by_id_path,) if value}
    if typed.casefold() in accepted:
        return
    logger.warning("confirmation_mismatch", path=device.path, reason="no_serial")
    raise ConfirmationMismatch(
        f"{device.path} reports no serial, and the typed value does not match "
        "its stable identifier.",
        remediation=(
            "This device exposes no serial. Confirm it by typing its full "
            "/dev/disk/by-id path from the capability report instead."
        ),
    )
