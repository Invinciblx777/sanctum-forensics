"""The write seam's own check of an erase authorization.

The API opens, approves and spends an authorization, then hands the helper a
real erase. The helper is the process that would write, so it does not take the
API's word: immediately before the engine starts it re-reads the device and the
backup image from the host and refuses on any difference from what was approved.

What is re-checked, each from a fresh read in *this* process:

* an authorization record exists, is approved, was spent by the API, and says
  exactly what the request says (path, level, identity, plan, backup);
* the device at the path still has the recorded serial, model and size, is not
  the system disk and has no mounted filesystem;
* the capability plan derived now equals the approved plan;
* the backup image still has the recorded size, mtime, ctime and inode and still
  covers the device;
* the authorization has not already been executed: an exclusive-create marker
  ``<id>.executed`` is taken last, so two concurrent attempts cannot both pass.

What this does **not** establish, and the report must not claim: the record and
its markers are files in the API's state directory, so a process able to write
that directory as the operator could forge a consistent set (the helper socket
is what keeps other users out, not this check); the backup is not re-hashed; and
between this check and the first write the engine's own guards (system-disk,
mount, serial re-read) are the only checks left. The window is narrowed, not
removed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.authorization import (
    AUTH_ID,
    backup_drift,
    build_plan,
    device_identity,
    identity_drift,
    plan_drift,
)
from core.errors import WorkflowGateRefused

__all__ = ["revalidate_execution"]

_HINT = (
    "Open a workflow (POST /workflow/erase-drive), approve it, and execute with "
    "the authorization it returns. Nothing was erased."
)


def _refuse(reasons: list[str]) -> WorkflowGateRefused:
    return WorkflowGateRefused(
        "REFUSED at the write seam: " + "; ".join(reasons) + ". Nothing was erased.",
        why_blocked=reasons,
        remediation=_HINT,
    )


def _fresh_probe(path: str) -> dict[str, Any]:
    """Re-read the device and its capabilities from the host, now."""
    from core.device import capabilities
    from core.device.enumerate import get_device

    device = get_device(path)
    return {
        "device": device.model_dump(mode="json"),
        "capabilities": capabilities.probe(device).model_dump(mode="json"),
    }


def revalidate_execution(
    params: dict[str, Any],
    *,
    probe: Callable[[str], dict[str, Any]] | None = None,
) -> None:
    """Refuse a real erase unless its authorization holds up, or return.

    Applies only to a request whose ``dry_run`` is explicitly ``False``: the
    same rule the rest of the helper uses, so a missing flag simulates. Raises
    :class:`~core.errors.WorkflowGateRefused` and does nothing else on refusal.
    On success it has taken the ``.executed`` marker, so a second call with the
    same authorization refuses.
    """
    if params.get("dry_run", True) is not False:
        return

    binding = params.get("authorization")
    root_raw = params.get("authorization_dir")
    if not isinstance(binding, dict) or not root_raw:
        raise _refuse(["the request carries no authorization"])
    auth_id = str(binding.get("auth_id", ""))
    if not AUTH_ID.match(auth_id):
        raise _refuse(["the authorization id is malformed"])
    root = Path(str(root_raw))

    try:
        record = json.loads((root / f"{auth_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _refuse([f"authorization {auth_id} does not exist"]) from None
    if not isinstance(record, dict):
        raise _refuse([f"authorization {auth_id} is unreadable"])

    reasons: list[str] = []
    if not record.get("approved_by"):
        reasons.append("no person has approved this authorization")
    if not (root / f"{auth_id}.spent").exists():
        reasons.append("the authorization was not consumed by the API gate")
    path = str(params.get("path", ""))
    level = str(params.get("level", ""))
    for label, asked, held in (
        ("path", path, record.get("path")),
        ("level", level, record.get("level")),
    ):
        if asked != held:
            reasons.append(
                f"the request's {label} {asked!r} is not the approved {held!r}"
            )
    for key in ("device", "backup", "plan"):
        if binding.get(key) != record.get(key):
            reasons.append(f"the request's {key} does not match the recorded approval")
    if reasons:
        raise _refuse(reasons)

    # From here every fact is read from the host, not from either party.
    try:
        fresh = (probe or _fresh_probe)(path)
    except Exception as exc:  # noqa: BLE001 - any failure to re-read is a refusal
        raise _refuse(
            [
                "the device could not be re-read at the write seam "
                f"({type(exc).__name__})"
            ]
        ) from None
    device = fresh.get("device", {})
    now = device_identity(fresh)
    reasons.extend(identity_drift(record["device"], now))
    if device.get("is_system_disk"):
        reasons.append("the device hosts the running root filesystem")
    if device.get("mounted_at"):
        reasons.append(
            "the device has mounted filesystems: " + ", ".join(device["mounted_at"])
        )
    if fresh.get("capabilities") is None:
        reasons.append("the device's capabilities could not be probed")
    reasons.extend(plan_drift(record["plan"], build_plan(fresh, level)))
    if level not in build_plan(fresh, level)["achievable_levels"]:
        reasons.append(f"level {level} is not achievable on the device now")
    reasons.extend(backup_drift(record["backup"], now["size_bytes"]))
    if reasons:
        raise _refuse(reasons)

    # Last, so a refusal above never burns the marker and two racers cannot both
    # pass: exclusive create is atomic on a local filesystem.
    try:
        fd = os.open(
            root / f"{auth_id}.executed", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        raise _refuse(
            [
                f"authorization {auth_id} was already executed; it authorizes "
                "one execution"
            ]
        ) from None
    except OSError as exc:
        raise _refuse(
            [f"the execution marker could not be taken ({type(exc).__name__})"]
        ) from None
    os.close(fd)
