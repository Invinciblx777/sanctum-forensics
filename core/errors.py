"""Exception hierarchy for Sanctum Forensics.

Every error carries a ``remediation`` string: a concrete next step for the
operator. Layers raise these instead of bare exceptions so the API and CLI can
present an honest, actionable message.
"""

from __future__ import annotations

__all__ = [
    "SanctumError",
    "DeviceVanished",
    "DeviceFrozen",
    "SystemDiskRefused",
    "MountedRefused",
    "ConfirmationMismatch",
    "GeometryRefused",
    "UnsupportedCapability",
    "EvidenceIntegrityError",
    "LedgerChainBroken",
    "SignatureInvalid",
    "PlatformUnsupported",
]


class SanctumError(Exception):
    """Base class for every Sanctum error.

    Args:
        message: Human-readable description of what went wrong.
        remediation: Concrete next step. Defaults to the subclass default.
    """

    default_remediation: str = "No automated remediation is available."

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.remediation: str = remediation or self.default_remediation


class DeviceVanished(SanctumError):
    """The target device disappeared mid-operation (unplugged or reset)."""

    default_remediation = (
        "Re-enumerate devices and confirm the target is still connected before retry."
    )


class DeviceFrozen(SanctumError):
    """ATA security is frozen by firmware/BIOS; security-erase cannot start."""

    default_remediation = (
        "Issue an S3 sleep/wake cycle or power-cycle the drive to clear the frozen "
        "state, then re-probe capabilities."
    )


class SystemDiskRefused(SanctumError):
    """Refused: the target hosts the running root filesystem."""

    default_remediation = (
        "Boot from separate media and run the erase against the drive as a non-system "
        "disk."
    )


class MountedRefused(SanctumError):
    """Refused: the target has one or more mounted filesystems."""

    default_remediation = "Unmount every filesystem on the device and retry."


class ConfirmationMismatch(SanctumError):
    """The serial typed by the operator does not match the target device."""

    default_remediation = (
        "Re-read the device serial from the capability report and type it exactly."
    )


class GeometryRefused(SanctumError):
    """The erase geometry does not cover the whole medium the kernel reports.

    Raised rather than wiping what the smaller number describes. An erase that
    silently covers a fraction of a device is the one failure this tool must
    never produce: it ends with a report saying the medium was sanitized.
    """

    default_remediation = (
        "Re-run enumeration and HPA/DCO detection for the device. If the hidden "
        "area probe cannot produce a trustworthy native max, erase using the "
        "kernel-reported size and record that hidden sectors were not covered."
    )


class UnsupportedCapability(SanctumError):
    """The requested erase method is not achievable on this device."""

    default_remediation = (
        "Select a method from the device's probed achievable_levels, or physically "
        "destroy the media."
    )


class EvidenceIntegrityError(SanctumError):
    """An evidence image or path failed a read-only integrity check."""

    default_remediation = (
        "Re-acquire the evidence from the original source and compare acquisition "
        "hashes before carving."
    )


class LedgerChainBroken(SanctumError):
    """A ledger entry's prev_entry_hash does not match the prior entry."""

    default_remediation = (
        "Treat the ledger as compromised. Preserve the raw store and investigate from "
        "the last verified entry."
    )


class SignatureInvalid(SanctumError):
    """A report or payload signature failed verification."""

    default_remediation = (
        "Confirm the correct public key and that the payload was not modified after "
        "signing."
    )


class PlatformUnsupported(SanctumError):
    """The operation requires a platform this host does not provide.

    Whole-device sanitization needs Linux block-device semantics: O_DIRECT with
    logical-block alignment, the BLKGETSIZE64 ioctl, sysfs queue attributes, and
    ATA/NVMe pass-through. None of these have a faithful equivalent elsewhere, and
    a shim that pretended otherwise would be a silent correctness hazard on the
    one code path where being wrong destroys evidence.
    """

    default_remediation = (
        "Run this on Linux. On Windows use WSL2 and attach the target disk with "
        "usbipd-win (`usbipd bind --busid <id>` then `usbipd attach --wsl`), or "
        "use a Linux VM with the controller passed through. File and folder "
        "erasure (core.erase.files) remains available on this platform."
    )
