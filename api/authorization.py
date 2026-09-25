"""The server-side gate a real drive erase must pass. One source of truth.

The problem
-----------
``POST /jobs/erase-drive`` used to accept ``dry_run=false`` plus a typed serial
and start writing. The destructive-workflow state machine in
:mod:`core.workflow` existed, but only the UI and the benchmark script consulted
it, so a direct API request skipped every gate the workflow names: no recorded
approval, no verified backup, no plan. The typed serial was the only check, and
it is a confirmation token, not an approval.

The fix
-------
The gate lives at the API boundary and reuses :func:`core.workflow.derive` and
:func:`core.workflow.advance` rather than a parallel rule set. A real erase
needs an *authorization record*, built in three explicit steps that no request
can collapse into one:

1. ``POST /workflow/erase-drive`` opens the record and verifies a backup image
   read-only (regular file, inside the evidence directory, at least as large as
   the device, hashed).
2. ``POST /workflow/erase-drive/{id}/approve`` records a human approval. Only
   this call sets ``approved``; nothing infers it from a serial.
3. ``POST /jobs/erase-drive`` with ``authorization_id`` re-reads the device,
   rebuilds every :class:`WorkflowFacts` field from that fresh read and the
   record, requires ``derive`` to answer ``PLAN_READY`` and ``advance`` to
   accept ``EXECUTING``. The record is spent before the job is submitted, so it
   authorizes one execution.

What is honestly claimed
------------------------
The backup check proves a hashed image of at least the device's size existed
and was unchanged (size, mtime) at execution. It does not prove the image is a
copy of this device: no helper method reads the device for comparison. The API
has no user authentication, so "human approval" means a distinct deliberate
call by the operator account, recorded with that account. It cannot prove a
person, not a script, made it. The privileged helper daemon also does not
re-check this gate; the boundary here is the API's.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from core.workflow import (
    IllegalTransition,
    WorkflowFacts,
    WorkflowState,
    WorkflowStatus,
    advance,
    derive,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from api.deps import AppServices

__all__ = [
    "AuthorizationStore",
    "GateRefused",
    "SIMULATION_MARK",
    "authorize_execution",
    "facts_for",
]

logger = structlog.get_logger(__name__)

#: Printed on every dry-run answer so a simulation cannot be mistaken for a wipe.
SIMULATION_MARK = "SIMULATION / NO PHYSICAL DEVICE MODIFIED"

_ID = re.compile(r"^auth-[0-9a-f]{16}$")
_CHUNK = 4 * 1024 * 1024


class GateRefused(Exception):
    """A workflow gate is not satisfied. Carries the structured refusal."""

    def __init__(
        self,
        message: str,
        *,
        state: WorkflowState,
        why_blocked: list[str],
        remediation: str,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.state = state
        self.why_blocked = why_blocked
        self.remediation = remediation

    def detail(self) -> dict[str, Any]:
        return {
            "error": self.message,
            "kind": "WorkflowGateRefused",
            "remediation": self.remediation,
            "verdict": "REFUSED",
            "workflow_state": self.state.value,
            "WHY BLOCKED": self.why_blocked,
            "physical_device_modified": False,
        }


@dataclass
class _Record:
    """One authorization, as stored. Plain data so it round-trips as JSON."""

    auth_id: str
    path: str
    level: str
    device: dict[str, Any]
    backup: dict[str, Any]
    plan: dict[str, Any]
    opened_by: str
    opened_at: str
    approved_by: str = ""
    approved_at: str = ""
    consumed_at: str = ""


class AuthorizationStore:
    """Records under ``<state_dir>/authorizations``, one JSON file each."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _file(self, auth_id: str) -> Path:
        if not _ID.match(auth_id):
            raise KeyError(auth_id)
        return self.root / f"{auth_id}.json"

    def create(self, record: _Record) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._file(record.auth_id).write_text(
            json.dumps(record.__dict__, sort_keys=True, indent=2), encoding="utf-8"
        )

    def load(self, auth_id: str) -> _Record | None:
        try:
            raw = json.loads(self._file(auth_id).read_text(encoding="utf-8"))
        except (KeyError, OSError, ValueError):
            return None
        try:
            return _Record(**raw)
        except TypeError:
            return None

    def save(self, record: _Record) -> None:
        self._file(record.auth_id).write_text(
            json.dumps(record.__dict__, sort_keys=True, indent=2), encoding="utf-8"
        )

    def spend(self, auth_id: str) -> bool:
        """Mark spent by exclusive create. False if it was already spent."""
        marker = self.root / f"{auth_id}.spent"
        try:
            with marker.open("x", encoding="utf-8") as handle:
                handle.write(datetime.now(UTC).isoformat())
        except FileExistsError:
            return False
        return True

    def is_spent(self, auth_id: str) -> bool:
        return (self.root / f"{auth_id}.spent").exists()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return f"auth-{secrets.token_hex(8)}"


def sha256_of(path: Path) -> tuple[str, int, int]:
    """Read-only hash of ``path``. Returns digest, size, mtime_ns."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:  # read-only by construction
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    stat = path.stat()
    return digest.hexdigest(), stat.st_size, stat.st_mtime_ns


def device_identity(probe: dict[str, Any]) -> dict[str, Any]:
    """The fields that must not change between approval and execution."""
    device = probe.get("device", {})
    return {
        "path": str(device.get("path", "")),
        "serial": str(device.get("serial", "")),
        "model": str(device.get("model", "")),
        "size_bytes": int(device.get("size_bytes", 0) or 0),
    }


def build_plan(probe: dict[str, Any], level: str) -> dict[str, Any]:
    """The plan a person approves: what would run, decided by capability."""
    caps = probe.get("capabilities") or {}
    achievable = list(caps.get("achievable_levels", []))
    return {
        "level": level,
        "achievable_levels": achievable,
        "estimated_seconds": caps.get("est_erase_seconds"),
        "limitations": list(caps.get("limitations", [])),
        "blocking": (
            []
            if level in achievable
            else [
                f"level {level} is not achievable on this device "
                f"(achievable: {achievable or 'none'})"
            ]
        ),
    }


def facts_for(
    record: _Record | None,
    probe: dict[str, Any] | None,
    *,
    level: str,
) -> WorkflowFacts:
    """Build :class:`WorkflowFacts` from a *fresh* probe and the stored record.

    Every field is established here from something read now; none is copied
    from the request. ``human_approved`` is true only when the record carries an
    approval the approve endpoint wrote.
    """
    if probe is None:
        return WorkflowFacts()
    device = probe.get("device", {})
    caps = probe.get("capabilities")
    refusal = ""
    if device.get("is_system_disk"):
        refusal = "device hosts the running root filesystem"
    elif device.get("mounted_at"):
        refusal = f"device has mounted filesystems: {', '.join(device['mounted_at'])}"
    elif caps is None:
        refusal = "capabilities could not be probed"
    if record is None:
        return WorkflowFacts(
            device_present=True,
            preflight_ran=True,
            preflight_safe=not refusal,
            preflight_refusal=refusal,
            serial_sources_agree=False,
        )

    now = device_identity(probe)
    recorded = record.device
    identity_changed = [
        f"{key} changed since the backup and approval: recorded "
        f"{recorded.get(key)!r}, now {now.get(key)!r}"
        for key in ("path", "serial", "model", "size_bytes")
        if recorded.get(key) != now.get(key)
    ]
    if identity_changed and not refusal:
        refusal = "device identity changed: " + "; ".join(identity_changed)

    backup_ok = False
    blocking = list(record.plan.get("blocking", []))
    backup_path = Path(str(record.backup.get("path", "")))
    try:
        stat = backup_path.stat()
        backup_ok = (
            stat.st_size == record.backup.get("size_bytes")
            and stat.st_mtime_ns == record.backup.get("mtime_ns")
            and stat.st_size >= now["size_bytes"] > 0
        )
        if not backup_ok:
            blocking.append(
                "the verified backup image changed or no longer covers the device"
            )
    except OSError:
        blocking.append("the verified backup image is no longer readable")
    if level != record.level:
        blocking.append(f"level {level} differs from the approved level {record.level}")
    achievable = list(caps.get("achievable_levels", [])) if caps else []
    if level not in achievable:
        blocking.append(f"level {level} is not achievable on the device now")

    return WorkflowFacts(
        device_present=True,
        preflight_ran=True,
        preflight_safe=not refusal,
        preflight_refusal=refusal,
        serial_sources_agree=bool(now["serial"]) and not identity_changed,
        backup_sufficient=backup_ok,
        plan_generated=True,
        plan_blocking=tuple(blocking),
        human_approved=bool(record.approved_by),
    )


def status_of(facts: WorkflowFacts) -> WorkflowStatus:
    return derive(facts)


def authorize_execution(
    services: AppServices,
    *,
    auth_id: str,
    path: str,
    level: str,
    probe: dict[str, Any],
    actor: str,
) -> None:
    """Pass the workflow gate for a real erase, spend the record, or refuse.

    Raises :class:`GateRefused` and does nothing else on refusal: no device is
    opened, no ledger entry claims execution, the record is not spent.
    """
    store = AuthorizationStore(services.state_dir / "authorizations")
    record = store.load(auth_id) if auth_id else None

    def refuse(
        state: WorkflowState, reasons: list[str], remediation: str
    ) -> GateRefused:
        _ledger_refusal(services, actor=actor, path=path, state=state, reasons=reasons)
        return GateRefused(
            f"REFUSED: a real erase of {path} needs the workflow gates satisfied "
            f"through the API; the workflow is at {state.value}. Nothing was erased.",
            state=state,
            why_blocked=reasons,
            remediation=remediation,
        )

    open_hint = (
        "Open the workflow with POST /workflow/erase-drive, record a human "
        "approval with POST /workflow/erase-drive/{id}/approve, then send the "
        "returned authorization_id here."
    )
    if record is None:
        raise refuse(
            WorkflowState.HUMAN_APPROVAL_REQUIRED,
            [
                "no authorization_id was given, or it names no record"
                if not auth_id
                else f"authorization {auth_id!r} does not exist",
                "no person has approved this plan; approval is required "
                "before any write",
            ],
            open_hint,
        )
    if store.is_spent(auth_id):
        raise refuse(
            WorkflowState.BLOCKED,
            [f"authorization {auth_id} was already used; it authorizes one execution"],
            open_hint,
        )
    if record.path != path:
        raise refuse(
            WorkflowState.BLOCKED,
            [f"authorization {auth_id} was recorded for {record.path}, not {path}"],
            open_hint,
        )

    facts = facts_for(record, probe, level=level)
    status = derive(facts)
    try:
        advance(WorkflowState.PLAN_READY, WorkflowState.EXECUTING, facts)
    except IllegalTransition:
        raise refuse(
            status.state,
            list(status.why_blocked) or ["the workflow is not at PLAN_READY"],
            status.next_action,
        ) from None

    if not store.spend(auth_id):
        raise refuse(
            WorkflowState.BLOCKED,
            [f"authorization {auth_id} was already used; it authorizes one execution"],
            open_hint,
        )


def _ledger_refusal(
    services: AppServices,
    *,
    actor: str,
    path: str,
    state: WorkflowState,
    reasons: list[str],
) -> None:
    """Record a refused destructive request. Best effort, never masks the refusal."""
    try:
        services.ledger().append(
            actor=actor,
            operation="erase.refused",
            params={"path": path, "workflow_state": state.value},
            result={"verdict": "REFUSED", "why_blocked": reasons},
        )
    except Exception as exc:  # noqa: BLE001 - the refusal matters more than its record
        logger.warning("refusal_not_recorded", path=path, error=str(exc))
