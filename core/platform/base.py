"""The platform adapter contract, and the logic every adapter shares.

An adapter answers four kinds of question for one operating system:

* **Discovery** - :meth:`PlatformAdapter.enumerate_devices` and
  :meth:`PlatformAdapter.inspect_device` return :class:`NormalizedDevice`
  rows; the second re-reads one device from the OS, and is what the
  privileged layer calls immediately before a destructive operation so it
  never trusts a value the UI sent.
* **Capability** - :meth:`PlatformAdapter.operation_capabilities` is the
  platform matrix; :meth:`PlatformAdapter.assess_device` is the per-device
  answer the Sanitize screen renders.
* **Execution** - :meth:`PlatformAdapter.execute_drive_sanitization` either
  runs the platform's real whole-drive engine or raises
  :class:`~core.errors.PlatformUnsupported` naming why. File erasure is not an
  adapter method: :mod:`core.erase.files` is one cross-platform engine and the
  per-OS differences live in :mod:`core.erase._platform`, which the adapter
  reports on rather than duplicates.
* **Restrictions** - :meth:`PlatformAdapter.restrictions` and
  :meth:`PlatformAdapter.protected_paths`.

What is shared lives in :class:`BaseAdapter`: the safety checks (the same
checks on every OS, so Windows cannot quietly skip one Linux enforces), the
file-erase capability rows derived from the file backend, and the assessment
shape. An adapter supplies facts; it does not get to supply its own rules.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from typing import Any, Protocol

from core.errors import PlatformUnsupported
from core.platform.model import (
    OPERATION_LABELS,
    CapabilityStatus,
    DeviceAssessment,
    MediaClassSupport,
    NormalizedDevice,
    Operation,
    OperationCapability,
    PlatformFamily,
    PlatformInfo,
    PrivilegeState,
    SafetyCheck,
    SanitizeOption,
)

__all__ = [
    "PlatformAdapter",
    "BaseAdapter",
    "FLASH_LIMITATION",
    "DiscoveryOutcome",
    "row",
    "legacy_row",
]

#: The same sentence on every platform. Overwrite never reaches what the flash
#: translation layer has remapped or held back, and no OS changes that.
FLASH_LIMITATION = (
    "SSD / flash limitation: an overwrite cannot address blocks the flash "
    "controller has remapped or held in over-provisioned space. Only a "
    "firmware sanitize or cryptographic erase reaches them, so an overwrite of "
    "flash is reported as Clear, never as Purge."
)

#: Status values that mean "this can run on this host".
_RUNNABLE = frozenset(
    {CapabilityStatus.SUPPORTED, CapabilityStatus.SUPPORTED_WITH_LIMITATIONS}
)


def row(
    operation: Operation,
    status: CapabilityStatus,
    reason: str,
    source: str,
    *,
    verification: str = "",
    limitations: list[str] | None = None,
    requires_privilege: bool = False,
) -> OperationCapability:
    """Build one capability row. ``source`` is mandatory by construction."""
    if not source:
        raise ValueError(f"capability row {operation} has no source")
    return OperationCapability(
        operation=operation,
        label=OPERATION_LABELS[operation],
        status=status,
        reason=reason,
        source=source,
        verification=verification,
        limitations=list(limitations or []),
        requires_privilege=requires_privilege,
    )


class DiscoveryOutcome:
    """What the last device discovery on this adapter established."""

    def __init__(self) -> None:
        self.ran = False
        self.ok = False
        self.tool = ""
        self.detail = ""
        self.devices: list[NormalizedDevice] = []

    def record(
        self, *, ok: bool, tool: str, detail: str, devices: list[NormalizedDevice]
    ) -> None:
        self.ran = True
        self.ok = ok
        self.tool = tool
        self.detail = detail
        self.devices = devices


class PlatformAdapter(Protocol):
    """What the sanitization service needs from an operating system."""

    name: str
    family: PlatformFamily

    def platform_info(self) -> PlatformInfo: ...

    def privilege_state(self) -> PrivilegeState: ...

    def enumerate_devices(
        self, *, include_virtual: bool = False
    ) -> list[NormalizedDevice]: ...

    def inspect_device(self, device_id: str) -> NormalizedDevice: ...

    def assess_device(self, device: NormalizedDevice) -> DeviceAssessment: ...

    def operation_capabilities(self) -> list[OperationCapability]: ...

    def media_classes(self) -> list[MediaClassSupport]: ...

    def restrictions(self) -> list[str]: ...

    def protected_paths(self) -> list[str]: ...

    def execute_drive_sanitization(
        self, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]: ...

    def resume_drive_sanitization(
        self, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]: ...


class BaseAdapter:
    """Shared behaviour. Subclasses supply OS facts, never their own rules."""

    name = "base"
    family: PlatformFamily = "other"

    def __init__(self, *, helper: str = "in-process", helper_basis: str = "") -> None:
        self._helper = helper
        self._helper_basis = helper_basis
        self.discovery = DiscoveryOutcome()

    # -- host ---------------------------------------------------------------

    def platform_info(self) -> PlatformInfo:
        from core.platform.host import platform_info

        return platform_info()

    def privilege_state(self) -> PrivilegeState:
        from core.platform.host import privilege_state

        return privilege_state(helper=self._helper, helper_basis=self._helper_basis)

    # -- discovery (subclass) -----------------------------------------------

    def enumerate_devices(
        self, *, include_virtual: bool = False
    ) -> list[NormalizedDevice]:
        raise NotImplementedError

    def inspect_device(self, device_id: str) -> NormalizedDevice:
        """Re-read one device from the OS. Never served from a cache."""
        from core.errors import DeviceVanished

        needle = device_id.strip()
        for device in self.enumerate_devices(include_virtual=True):
            if needle in {device.id, device.path, device.serial, device.stable_id}:
                return device
        raise DeviceVanished(f"No storage device matches {device_id!r}.")

    def device_rows(self, *, include_virtual: bool = False) -> list[dict[str, Any]]:
        """The helper's ``enumerate_devices`` answer.

        ``device`` keeps the legacy field names so the device screens still
        render; a platform with no Linux capability probe or erase preview
        leaves those keys null with the reason in ``capability_error``.
        :class:`~core.platform.linux.LinuxAdapter` overrides this with the
        real probe.
        """
        rows: list[dict[str, Any]] = []
        reason = self.whole_drive_unavailable_reason()
        for device in self.enumerate_devices(include_virtual=include_virtual):
            entry = legacy_row(device, reason)
            entry["assessment"] = self.assess_device(device).model_dump(mode="json")
            rows.append(entry)
        return rows

    # -- whole-drive (subclass) ---------------------------------------------

    def whole_drive_unavailable_reason(self) -> str:
        """Why this platform has no whole-drive engine, or ``""`` if it has one."""
        return (
            f"This build has no whole-drive sanitization engine for "
            f"{self.family}. Nothing is offered rather than something unverified."
        )

    def whole_drive_recommended_action(self) -> str:
        return (
            "Sanitize this device with the Sanctum Linux build (AppImage) on "
            "any Linux host or live USB, where the validated whole-drive engine "
            "runs, or use the drive vendor's own sanitize tool."
        )

    def execute_drive_sanitization(
        self, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        raise PlatformUnsupported(
            self.whole_drive_unavailable_reason()
            + " No operation was performed on the device.",
            remediation=self.whole_drive_recommended_action(),
        )
        yield {}  # pragma: no cover - makes this a generator

    def resume_drive_sanitization(
        self, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        raise PlatformUnsupported(
            self.whole_drive_unavailable_reason()
            + " There is nothing to resume on this platform.",
            remediation=self.whole_drive_recommended_action(),
        )
        yield {}  # pragma: no cover - makes this a generator

    # -- restrictions -------------------------------------------------------

    def restrictions(self) -> list[str]:
        return []

    def protected_paths(self) -> list[str]:
        from core.platform.paths import protected_prefixes

        return protected_prefixes(self.family)

    # -- safety: identical on every platform --------------------------------

    def safety_checks(
        self, device: NormalizedDevice, privilege: PrivilegeState
    ) -> list[SafetyCheck]:
        """The pre-flight checks, in the order an operator reads them.

        One implementation for every OS. ``passed=None`` is used when a fact
        could not be established, and a check that could not be established
        is never rendered as a pass.
        """
        checks: list[SafetyCheck] = [
            SafetyCheck(
                key="detected",
                label="Device detected",
                passed=True,
                detail=f"{device.path} was read from the OS by {self.name} discovery.",
            ),
            SafetyCheck(
                key="identity",
                label="Identity readable",
                passed=True if (device.serial or device.stable_id) else None,
                detail=(
                    f"Serial {device.serial}."
                    if device.serial
                    else f"No serial reported; stable id {device.stable_id}."
                    if device.stable_id
                    else "Neither a serial nor a stable identifier was reported."
                ),
            ),
            SafetyCheck(
                key="capacity",
                label="Capacity known",
                passed=device.capacity_bytes > 0,
                detail=(
                    f"{device.capacity_bytes} bytes."
                    if device.capacity_bytes > 0
                    else "The OS reported no capacity for this device."
                ),
            ),
            SafetyCheck(
                key="not_system",
                label="Not the system or boot disk",
                passed=not device.system_device,
                detail=(
                    " ".join(device.system_reasons)
                    if device.system_device
                    else "Holds no running system, boot, swap or page file."
                ),
            ),
            SafetyCheck(
                key="not_mounted",
                label="No mounted filesystem",
                passed=not device.mounted,
                detail=(
                    "Mounted at " + ", ".join(device.mount_points) + "."
                    if device.mounted
                    else "Nothing on this device is mounted."
                ),
            ),
            SafetyCheck(
                key="media_type",
                label="Media type identified",
                passed=True if device.media_type != "unknown" else None,
                detail=device.media_basis or "The medium type was not determined.",
            ),
            SafetyCheck(
                key="privilege",
                label="Privilege available",
                passed=self._privileged_enough(privilege),
                detail=self._privilege_detail(privilege),
            ),
        ]
        return checks

    def _privileged_enough(self, privilege: PrivilegeState) -> bool | None:
        if privilege.helper == "socket":
            return True
        return privilege.elevated

    def _privilege_detail(self, privilege: PrivilegeState) -> str:
        if privilege.helper == "socket":
            return (
                "Privileged work runs in the separate helper process over its "
                "authenticated socket; this interface stays unprivileged."
            )
        if privilege.elevated:
            return f"This process is elevated ({privilege.basis})."
        if privilege.elevated is False:
            return (
                f"This process is not elevated ({privilege.basis}). Raw device "
                "access is refused by the OS."
            )
        return f"Elevation could not be determined ({privilege.basis})."

    # -- assessment ---------------------------------------------------------

    def drive_options(
        self, device: NormalizedDevice
    ) -> tuple[list[SanitizeOption], str]:
        """Every level for ``device`` and the verification sentence.

        The default is the honest one for a platform with no engine: both
        levels unavailable, with the platform's reason.
        """
        reason = self.whole_drive_unavailable_reason()
        options = [
            SanitizeOption(
                level=level,
                title=title,
                status=CapabilityStatus.UNSUPPORTED,
                why=reason,
                remediation=self.whole_drive_recommended_action(),
            )
            for level, title in (
                ("PURGE", "Hardware purge"),
                ("CLEAR", "Overwrite (Clear)"),
            )
        ]
        return options, "No verification: no whole-drive operation is offered here."

    def assess_device(self, device: NormalizedDevice) -> DeviceAssessment:
        """Recommended method, alternatives, and why anything is unavailable."""
        privilege = self.privilege_state()
        checks = self.safety_checks(device, privilege)
        options, verification = self.drive_options(device)
        runnable = [item for item in options if item.status in _RUNNABLE]
        unavailable = [item for item in options if item.status not in _RUNNABLE]
        recommended = runnable[0] if runnable else None
        flash_note = FLASH_LIMITATION if device.media_type in {"ssd", "flash"} else ""

        base = {
            "device_id": device.id,
            "platform": self.family,
            "safety_checks": checks,
            "flash_limitation": flash_note,
            "verification": verification,
        }

        if device.system_device:
            return DeviceAssessment(
                **base,
                status=CapabilityStatus.UNSUPPORTED,
                headline="NOT AVAILABLE",
                reason=(
                    "This is the system or boot disk, or holds a protected "
                    "operating-system volume. " + " ".join(device.system_reasons)
                ).strip(),
                recommended_action=(
                    "Boot the machine from external media (for example the "
                    "Sanctum Linux build on a USB stick) and sanitize the "
                    "internal disk from there, or use the OS's own reset flow."
                ),
                unavailable=options,
            )
        if device.mounted:
            return DeviceAssessment(
                **base,
                status=CapabilityStatus.UNSUPPORTED,
                headline="NOT AVAILABLE",
                reason=(
                    "A filesystem on this device is in use ("
                    + ", ".join(device.mount_points)
                    + "). Erasing a mounted device would corrupt it under a "
                    "running program and could not be verified."
                ),
                recommended_action=(
                    "Unmount or eject every volume on this device, then rescan."
                ),
                unavailable=options,
            )
        if recommended is None:
            first = options[0] if options else None
            return DeviceAssessment(
                **base,
                status=CapabilityStatus.UNSUPPORTED,
                headline="NOT AVAILABLE",
                reason=first.why if first else "No sanitization method is available.",
                recommended_action=(first.remediation if first else "")
                or self.whole_drive_recommended_action(),
                unavailable=unavailable,
            )
        privileged = self._privileged_enough(privilege)
        if privileged is False:
            return DeviceAssessment(
                **base,
                status=CapabilityStatus.NOT_AUTHORIZED,
                headline="NOT AUTHORIZED",
                reason=(
                    "The method is available but this process does not have the "
                    "privilege the OS requires for raw device access. A dry run "
                    "is still possible; a real erase is refused by the OS."
                ),
                recommended_action=self._elevation_advice(),
                recommended=recommended,
                alternatives=runnable[1:],
                unavailable=unavailable,
            )
        return DeviceAssessment(
            **base,
            status=recommended.status,
            headline="READY",
            reason=recommended.why,
            recommended=recommended,
            alternatives=runnable[1:],
            unavailable=unavailable,
        )

    def _elevation_advice(self) -> str:
        return "Start the privileged helper, then rescan."

    # -- file-side capability rows, shared -----------------------------------

    def _file_backend_rows(
        self, privilege: PrivilegeState
    ) -> list[OperationCapability]:
        """File, folder, batch, metadata and file-verification rows.

        Derived from the file backend this host actually selected
        (:func:`core.erase._platform.backend`), by checking which capability
        methods it implements rather than inheriting the honest unknown.
        """
        from core.erase._platform import backend
        from core.erase._platform.base import PortableBackend

        chosen = backend()
        cls = type(chosen)
        implemented = {
            name
            for name in (
                "extents",
                "fs_type",
                "alt_data_streams",
                "vss_shadows",
                "cow_snapshots",
            )
            if getattr(cls, name) is not getattr(PortableBackend, name)
        }
        source = (
            f"core.erase.files with the '{chosen.name}' file backend "
            f"({cls.__module__}.{cls.__name__}); implements "
            + (", ".join(sorted(implemented)) or "no platform-specific probes")
        )
        from core.platform.validation import describe, suite_passed

        limits = self._file_limitations()
        validated = suite_passed(self.family, "file_erase")
        source += "; " + describe(self.family, "file_erase")
        file_status = (
            CapabilityStatus.SUPPORTED_WITH_LIMITATIONS
            if chosen.name != "portable" and validated
            else CapabilityStatus.UNVERIFIED
        )
        erase_reason = (
            "Overwrites the file's data in place, renames it to random names of "
            "the same length, truncates and deletes it, then lists every copy "
            "it could not reach (journal, snapshots, SSD remapping)."
        )
        if not validated:
            erase_reason += (
                " The file-erase test suite has not been recorded as passing on "
                "this platform for this build, so this is UNVERIFIED rather "
                "than supported."
            )
        rows = [
            row(
                Operation.FILE_ERASE,
                file_status,
                erase_reason,
                source,
                verification="Physical read-back of the original extents when "
                "they could be mapped and the volume can be read raw.",
                limitations=limits,
            ),
            row(
                Operation.FOLDER_ERASE,
                file_status,
                "Every file in the folder, deepest first, then the folders "
                "themselves, each with the same file erase.",
                source + "; core.erase.files.expand_targets walks depth-first "
                "and never follows symlinks or junctions",
                verification=(
                    "Per file, as above; a link or reparse point is reported "
                    "refused rather than erased."
                ),
                limitations=limits,
            ),
            row(
                Operation.BATCH_ERASE,
                file_status,
                "Many files and folders in one job, one ledger entry per file "
                "per phase, cancellable between files.",
                source,
                verification=(
                    "Per file, as above; a cancelled batch records which files "
                    "it had reached."
                ),
                limitations=limits,
            ),
        ]
        from core.erase import metadata as metadata_mod

        handlers = sorted(
            name.removeprefix("cleanse_").upper()
            for name in dir(metadata_mod)
            if name.startswith("cleanse_") and name != "cleanse_only"
        )
        rows.append(
            row(
                Operation.METADATA_CLEANSE,
                CapabilityStatus.SUPPORTED_WITH_LIMITATIONS,
                "Removes embedded document metadata (EXIF, author fields, "
                "document properties) before the file is erased. Formats not "
                "listed are reported as not cleansed, never as clean.",
                "core.erase.metadata handlers: " + ", ".join(handlers),
                verification="The cleansed file is re-parsed and every field "
                "that survived is named in the record.",
                limitations=[
                    "Filesystem metadata (directory entries, MFT records, "
                    "journal) is renamed and truncated, not cleansed in place."
                ],
            )
        )
        maps_extents = "extents" in implemented
        if not maps_extents:
            verify_status = CapabilityStatus.NOT_VERIFIABLE
            verify_reason = (
                "This platform's file backend cannot map a file to physical "
                "blocks, so there is nothing to read back."
            )
        elif self._privileged_enough(privilege):
            verify_status = CapabilityStatus.SUPPORTED_WITH_LIMITATIONS
            verify_reason = (
                "The original physical blocks are mapped before the erase and "
                "read back from the raw volume afterwards."
            )
        else:
            verify_status = CapabilityStatus.NOT_AUTHORIZED
            verify_reason = (
                "The erase runs, but reading the raw volume back needs "
                "elevation this process does not have, so the result is "
                "reported as not verified rather than as passed."
            )
        rows.append(
            row(
                Operation.FILE_VERIFICATION,
                verify_status,
                verify_reason,
                source + "; core.erase.verify.verify_file_erase",
                verification=(
                    "This row *is* the verification path: a result is passed, "
                    "failed or not possible, and is never inferred from the "
                    "file having disappeared."
                ),
                limitations=[
                    "On copy-on-write filesystems (APFS, Btrfs, ReFS, ZFS) the "
                    "overwrite lands in new blocks, so a read-back of the old "
                    "ones is reported as not verifiable.",
                ],
                requires_privilege=True,
            )
        )
        return rows

    def _file_limitations(self) -> list[str]:
        return [FLASH_LIMITATION]

    # -- platform matrix ----------------------------------------------------

    def operation_capabilities(self) -> list[OperationCapability]:
        privilege = self.privilege_state()
        rows = [self._discovery_row()]
        rows += self._file_backend_rows(privilege)
        rows += self._platform_rows(privilege)
        return rows

    def _discovery_row(self) -> OperationCapability:
        if not self.discovery.ran:
            try:
                self.enumerate_devices()
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                self.discovery.record(
                    ok=False, tool=self.name, detail=str(exc), devices=[]
                )
        outcome = self.discovery
        if outcome.ok:
            return row(
                Operation.DEVICE_DISCOVERY,
                CapabilityStatus.SUPPORTED,
                f"{len(outcome.devices)} storage device(s) found.",
                f"{outcome.tool}: {outcome.detail}".strip(": "),
                verification=(
                    "Every list is read from the OS, never cached; the "
                    "privileged layer re-reads the device again immediately "
                    "before any destructive operation."
                ),
            )
        return row(
            Operation.DEVICE_DISCOVERY,
            CapabilityStatus.INCONCLUSIVE,
            "Device discovery did not complete: " + (outcome.detail or "unknown"),
            outcome.tool or self.name,
        )

    def _platform_rows(self, privilege: PrivilegeState) -> list[OperationCapability]:
        """Free space, whole-drive and resume. Default: none on this platform."""
        reason = self.whole_drive_unavailable_reason()
        src = f"{type(self).__module__}.{type(self).__name__}"
        return [
            row(
                Operation.FREE_SPACE_WIPE,
                CapabilityStatus.UNSUPPORTED,
                "Free-space wipe is implemented for Linux only; its fill "
                "behaviour has not been measured on this platform's filesystems.",
                "core.erase.freespace.FREE_SPACE_PLATFORMS",
                verification="Nothing runs here, so there is nothing to verify.",
            ),
            row(
                Operation.WHOLE_DRIVE_CLEAR,
                CapabilityStatus.UNSUPPORTED,
                reason,
                src,
                verification="Nothing runs here, so there is nothing to verify.",
            ),
            row(
                Operation.WHOLE_DRIVE_PURGE,
                CapabilityStatus.UNSUPPORTED,
                reason,
                src,
                verification="Nothing runs here, so there is nothing to verify.",
            ),
            row(
                Operation.DRIVE_VERIFICATION,
                CapabilityStatus.UNSUPPORTED,
                "No whole-drive operation runs here, so there is none to verify.",
                src,
                verification="Nothing runs here, so there is nothing to verify.",
            ),
            row(
                Operation.RESUME,
                CapabilityStatus.UNSUPPORTED,
                "Only a whole-drive overwrite can resume, and none runs here. A "
                "cancelled file batch records which files it reached.",
                src,
                verification="Nothing runs here, so there is nothing to verify.",
            ),
        ]

    # -- media classes ------------------------------------------------------

    def media_classes(self) -> list[MediaClassSupport]:
        """Storage-class rows, counted from the devices discovery found."""
        caps = {item.operation: item for item in self.operation_capabilities()}
        devices = self.discovery.devices
        file_status = caps[Operation.FILE_ERASE].status
        discovery_status = caps[Operation.DEVICE_DISCOVERY].status
        clear = caps[Operation.WHOLE_DRIVE_CLEAR]

        def count(predicate: Any) -> int:
            return sum(1 for item in devices if predicate(item))

        classes: list[tuple[str, Any, str]] = [
            (
                "Internal HDD",
                lambda d: d.media_type == "hdd" and d.removable is not True,
                "",
            ),
            (
                "Internal SSD",
                lambda d: (
                    d.media_type in {"ssd", "flash"}
                    and d.interface in {"sata", "nvme", "sas", "scsi"}
                    and d.removable is not True
                ),
                FLASH_LIMITATION,
            ),
            (
                "USB HDD",
                lambda d: d.interface == "usb" and d.media_type == "hdd",
                "USB bridges usually block the pass-through a firmware purge needs.",
            ),
            (
                "USB SSD / flash drive",
                lambda d: d.interface == "usb" and d.media_type != "hdd",
                FLASH_LIMITATION,
            ),
            (
                "SD / memory card",
                lambda d: d.interface == "mmc",
                FLASH_LIMITATION + " Card readers expose no sanitize command at all.",
            ),
        ]
        result: list[MediaClassSupport] = []
        for label, predicate, note in classes:
            whole = clear.status
            reason = clear.reason if whole not in _RUNNABLE else note or clear.reason
            if whole in _RUNNABLE and note:
                whole = CapabilityStatus.SUPPORTED_WITH_LIMITATIONS
            result.append(
                MediaClassSupport(
                    media_class=label,
                    discovery=discovery_status,
                    file_erase=file_status,
                    whole_drive=whole,
                    reason=reason,
                    detected_now=count(predicate),
                )
            )
        return result


def running_on(family: PlatformFamily) -> bool:
    """True when this process runs on ``family``."""
    from core.platform.host import family as host_family

    return host_family(sys.platform) == family


def legacy_row(device: NormalizedDevice, reason: str) -> dict[str, Any]:
    """A normalized device in the helper's pre-adapter row shape.

    For platforms with no Linux capability probe: the legacy keys the device
    screens read are filled from the normalized device, and the probe fields
    are null with ``reason`` saying why.
    """
    transport = (
        device.interface
        if device.interface in {"sata", "nvme", "usb", "mmc"}
        else "unknown"
    )
    return {
        "device": {
            "path": device.path,
            "model": device.model,
            "serial": device.serial,
            "size_bytes": device.capacity_bytes,
            "rotational": device.media_type == "hdd",
            "transport": transport,
            "is_system_disk": device.system_device,
            "mounted_at": device.mount_points,
            "pt_type": None,
            "by_id_path": device.stable_id or None,
        },
        "media": {
            "flash": device.media_type in {"ssd", "flash"}
            if device.media_type != "unknown"
            else None,
            "reason": device.media_basis,
        },
        "capabilities": None,
        "erase_preview": None,
        "capability_error": reason,
        "hidden_areas": None,
        "hidden_area_error": "Hidden-area (HPA/DCO) probing is Linux-only.",
        "normalized": device.model_dump(mode="json"),
    }
