"""Job endpoints: submit, stream, inspect, cancel.

Every submission returns a ``job_id`` immediately and runs the work on a worker
thread. No handler here waits for a wipe, an acquisition or a carve to finish -
a 4 TB overwrite is hours long and an HTTP request that lived that long would
be dead well before the work was.

**Both destructive endpoints default to a dry run.** The request models default
``dry_run=True``, and the value that reaches the helper is the one from the
model, so a body that omits the key simulates. The serial check is not
performed here either: it is re-checked inside the helper against the serial
that process reads from the host, so a stale browser cannot authorise a wipe of
a device that was swapped since the page loaded.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.deps import AppServices
from api.routes.common import (
    get_services,
    resolve_output_path,
    sanctum_error_response,
)
from api.routes.models import (
    AcquireRequest,
    CarveRequest,
    EraseDriveRequest,
    EraseFilesRequest,
    JobAccepted,
)
from api.sse import SSE_HEADERS, progress_events

__all__ = ["router"]

router = APIRouter(tags=["jobs"])


def _signing_fingerprint(services: AppServices) -> str:
    """This deployment's signing-key fingerprint, or ``""`` if it has none yet.

    Recorded in the ledger's genesis entry so a report signed later can be
    checked against the key the chain was started with. The lookup never
    creates a key: accepting a job must not have the side effect of minting one,
    and a deployment with no key still has to be able to start a chain.
    """
    from core.report.sign import fingerprint_of_existing_key

    return fingerprint_of_existing_key(
        services.key_dir or (services.state_dir / "keys")
    )


def _accepted(job_id: str, kind: str, dry_run: bool) -> JobAccepted:
    return JobAccepted(
        job_id=job_id,
        kind=kind,
        state="running",
        dry_run=dry_run,
        stream_url=f"/jobs/{job_id}/stream",
    )


# --------------------------------------------------------------------------
# Erase a drive
# --------------------------------------------------------------------------


@router.post("/jobs/erase-drive", response_model=JobAccepted)
def erase_drive(
    body: EraseDriveRequest,
    services: AppServices = Depends(get_services),
) -> JobAccepted:
    """Start a drive sanitization. Simulates unless ``dry_run`` is false.

    The typed serial is validated by the helper against the device it re-reads,
    and a mismatch comes back as 409 with the core's own remediation text.
    Validating it here as well would duplicate the check in the layer that
    cannot be trusted to hold it.
    """
    from helper.rpc import RpcError

    if not body.dry_run and not body.typed_serial:
        raise sanctum_error_response(
            "ConfirmationMismatch",
            f"Refusing to erase {body.path}: dry_run is off but no serial was "
            "typed. Destructive erasure is opt-in twice.",
            "Re-read the device serial from the capability report and type it "
            "exactly.",
        )

    params: dict[str, Any] = {
        "path": body.path,
        "level": body.level,
        "dry_run": body.dry_run,
        "typed_serial": body.typed_serial,
        "ledger_root": str(services.ledger_root),
        "tool_version": services.tool_version,
    }

    # Validated before the job is accepted, so a mismatch is a 409 the operator
    # sees immediately rather than a job that starts and fails a second later.
    try:
        probe = services.helper.call("probe_capabilities", {"path": body.path})
    except RpcError as exc:
        raise sanctum_error_response(
            exc.kind or "DeviceVanished", exc.message, exc.remediation
        ) from exc
    except OSError as exc:
        raise sanctum_error_response(
            "PlatformUnsupported",
            f"The privileged helper could not be reached: {exc}",
            "Start the helper daemon and set SANCTUM_HELPER_SOCKET.",
        ) from exc

    serial = str(probe.get("device", {}).get("serial", ""))
    if not body.dry_run and body.typed_serial != serial:
        raise sanctum_error_response(
            "ConfirmationMismatch",
            f"The typed serial {body.typed_serial!r} does not match "
            f"{body.path}, whose serial is {serial!r}. Nothing was erased.",
            "Re-read the device serial from the capability report and type it "
            "exactly.",
        )

    registry = services.registry

    # Minted here rather than by the registry, for the same reason the carve
    # route mints its own: the ledger entries this run writes are keyed by the
    # job id the helper is given, and GET /jobs/{id} and the report excerpt
    # both look them up by the id this endpoint returned. Sending a placeholder
    # instead wrote all six phases under an id no consumer ever queries, so a
    # wipe's audit trail could not be found again and the erasure certificate
    # carried nothing but the genesis entry.
    job_id = f"erase-drive-{uuid.uuid4().hex[:12]}"

    def factory() -> Any:
        return _helper_job(services, "run_erase", params, job_id=job_id)

    registry.submit("erase-drive", params, factory, job_id=job_id)
    return _accepted(job_id, "erase-drive", body.dry_run)


def _helper_job(
    services: AppServices, method: str, params: dict[str, Any], *, job_id: str
) -> Any:
    """Drive a helper call as a generator so the registry can stream it.

    The helper streams: one progress frame per record the engine yields, over
    the same connection the request went out on. This used to be one blocking
    request/response that returned the whole progress list at the end, which
    made the UI's progress bar a replay of an operation that had already
    finished and left ``POST /jobs/{id}/cancel`` with no yield point to act on.

    Two consequences follow from ``yield from``, and both are the point:

    * the registry sees records as the device is written, so the bar moves
      while the wipe runs;
    * the registry's cancellation - ``generator.close()`` between yields -
      propagates into the helper transport, which tells the helper, which stops
      the engine at *its* next yield. Nothing is killed mid-write.

    ``job_id`` is the id the caller was handed and the registry filed the job
    under. It has to be the one the helper receives, because the helper is what
    passes it to the engine that writes the ledger.
    """
    from core.models import Progress

    stream = services.helper.call_stream(method, {**params, "job_id": job_id})
    try:
        while True:
            try:
                record = next(stream)
            except StopIteration as stop:
                answer: dict[str, Any] = stop.value
                break
            yield Progress.model_validate(record)
    finally:
        # Explicit, not left to the collector. On cancellation this is the call
        # that reaches the helper, and "when the frame happens to be freed" is
        # not a schedule to run a drive erase on.
        stream.close()
    return answer.get("result") or answer.get("record")


# --------------------------------------------------------------------------
# Erase files
# --------------------------------------------------------------------------


@router.post("/jobs/erase-files", response_model=JobAccepted)
def erase_files(
    body: EraseFilesRequest,
    services: AppServices = Depends(get_services),
) -> JobAccepted:
    """Start a file or folder erase. Simulates unless ``dry_run`` is false.

    File erasure is unprivileged - it writes through ordinary file handles - so
    it runs in this process rather than through the helper. There is nothing
    for the privilege boundary to protect here: the API can already open any
    file the operator can.
    """
    from core.erase.files import erase_paths
    from core.erase.sink import ChainLedgerSink
    from core.ledger.chain import Ledger
    from core.models import FileEraseOptions

    if not body.dry_run and not body.confirm:
        raise sanctum_error_response(
            "ConfirmationMismatch",
            "Refusing to erase: dry_run is off but confirm was not set. "
            "Destructive file erasure is opt-in twice.",
            "Set confirm=true to proceed, or leave dry_run=true to see what "
            "would survive without writing anything.",
        )

    options = FileEraseOptions(
        dry_run=body.dry_run,
        confirm=body.confirm,
        cleanse_metadata=body.cleanse_metadata,
        break_hardlinks=body.break_hardlinks,
        recursive=body.recursive,
    )
    params = {
        "paths": body.paths,
        "dry_run": body.dry_run,
        "confirm": body.confirm,
        "break_hardlinks": body.break_hardlinks,
    }
    # The id is minted here rather than by the registry, so the same string
    # reaches erase_paths and therefore the ledger. Letting the registry
    # allocate it would leave the chain entries keyed to an id the job endpoint
    # never learned, and GET /jobs/{id} could not find them again.
    job_id = f"erase-files-{uuid.uuid4().hex[:12]}"
    ledger = Ledger(
        services.ledger_root,
        tool_version=services.tool_version,
        # The key this deployment signs reports with, when it has one. A chain
        # whose genesis records no fingerprint can never satisfy the report
        # check that compares the two. Looked up rather than created: starting a
        # job must not mint a signing key as a side effect.
        pubkey_fingerprint=_signing_fingerprint(services),
    )

    def factory() -> Any:
        return erase_paths(
            [Path(item) for item in body.paths],
            options,
            job_id=job_id,
            ledger=ChainLedgerSink(ledger),
        )

    services.registry.submit("erase-files", params, factory, job_id=job_id)
    return _accepted(job_id, "erase-files", body.dry_run)


# --------------------------------------------------------------------------
# Acquire
# --------------------------------------------------------------------------


@router.post("/jobs/acquire", response_model=JobAccepted)
def acquire_image(
    body: AcquireRequest,
    services: AppServices = Depends(get_services),
) -> JobAccepted:
    """Image a device or file read-only. No dry-run gate: nothing is destroyed.

    The source is opened ``O_RDONLY`` and, on Linux against a block device, set
    read-only at the block layer first. See :mod:`core.carve.acquire`.

    **The destination is confined to this deployment's evidence directory.** It
    used to be passed through verbatim, and the API has no authentication of any
    kind, so any local process that could open the port could name any path this
    process can write and have an image written over it. Under the runbook's
    former ``sudo python -m api.main`` that process was root, which made it an
    arbitrary-file-write-as-root primitive; the runbook is corrected separately,
    but a path check that only holds when the deployment is configured correctly
    is not a check. A path outside the configured directory is now refused with
    a 400 before anything is opened.

    The acquisition itself runs in *this* process, not through the helper: it is
    an ``O_RDONLY`` read and needs no privilege the operator does not already
    have. The helper's ``acquire_image`` operation exists for the case where the
    source is a raw device the operator cannot open, and this route does not use
    it today.
    """
    from core.carve.acquire import AcquireOptions, acquire
    from core.ledger.chain import Ledger

    source = Path(body.source)
    if not source.exists():
        raise sanctum_error_response(
            "EvidenceIntegrityError",
            f"acquisition source not found: {source}",
            "Check the path. Nothing was created.",
        )

    dest = resolve_output_path(services.evidence_dir, body.dest, field="dest")
    dest.parent.mkdir(parents=True, exist_ok=True)

    params = {
        "source": str(source),
        "dest": str(dest),
        "fmt": body.fmt,
        "compression": body.compression,
    }
    registry = services.registry
    ledger = Ledger(
        services.ledger_root,
        tool_version=services.tool_version,
        # The key this deployment signs reports with, when it has one. A chain
        # whose genesis records no fingerprint can never satisfy the report
        # check that compares the two. Looked up rather than created: starting a
        # job must not mint a signing key as a side effect.
        pubkey_fingerprint=_signing_fingerprint(services),
    )

    job_id = f"acquire-{uuid.uuid4().hex[:12]}"

    def factory() -> Any:
        return acquire(
            source,
            dest,
            fmt=body.fmt,
            options=AcquireOptions(
                compression=body.compression, operator=body.operator
            ),
            ledger=ledger,
            job_id=job_id,
        )

    registry.submit("acquire", params, factory, job_id=job_id)
    return _accepted(job_id, "acquire", dry_run=False)


# --------------------------------------------------------------------------
# Carve
# --------------------------------------------------------------------------


@router.post("/jobs/carve", response_model=JobAccepted)
def carve_image(
    body: CarveRequest,
    services: AppServices = Depends(get_services),
) -> JobAccepted:
    """Recover objects from an image. Read-only by construction.

    Nothing in the carving path opens the evidence ``O_RDWR``; see
    :mod:`core.carve.evidence`, which declares no write method at all.
    """
    from api.carve_job import carve_generator

    image = Path(body.image)
    if not image.exists():
        raise sanctum_error_response(
            "EvidenceIntegrityError",
            f"evidence not found: {image}",
            "Check the path; nothing was opened.",
        )

    from core.ledger.chain import Ledger

    # Same treatment as the acquisition destination, for the same reason: the
    # API has no authentication, so an unchecked output path is a
    # write-anywhere primitive for any local process that can open the port.
    # Confined to the *recovered* directory rather than the evidence one -
    # recovered objects are derived output and evidence is read-only input, and
    # a recovery that wrote into the tree holding the image it is reading is
    # the one thing this path must never do.
    out_dir = (
        resolve_output_path(services.recovered_dir, body.out_dir, field="out_dir")
        if body.out_dir
        else None
    )

    params = {
        "image": str(image),
        "undelete": body.undelete,
        "carve_signatures": body.carve_signatures,
        "out_dir": str(out_dir) if out_dir else None,
    }
    registry = services.registry

    # Minted here for the same reason the erase routes mint theirs: the ledger
    # entries this run writes have to be keyed to an id GET /jobs/{id} and the
    # report excerpt can find again.
    job_id = f"carve-{uuid.uuid4().hex[:12]}"
    ledger = Ledger(
        services.ledger_root,
        tool_version=services.tool_version,
        pubkey_fingerprint=_signing_fingerprint(services),
    )

    def factory() -> Any:
        return carve_generator(
            image,
            undelete=body.undelete,
            carve_signatures=body.carve_signatures,
            out_dir=out_dir,
            job_id=job_id,
            ledger=ledger,
        )

    registry.submit("carve", params, factory, job_id=job_id)
    return _accepted(job_id, "carve", dry_run=False)


# --------------------------------------------------------------------------
# Inspect, stream, cancel
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}")
def job_status(
    job_id: str, services: AppServices = Depends(get_services)
) -> dict[str, Any]:
    """Current state of one job, resumable from the ledger.

    ``ledger_entries`` is what makes a reload survivable beyond this process:
    the registry's buffer lives in memory and dies with the API, while the
    chain is on disk and is what a report is built from.
    """
    try:
        status = services.registry.status(job_id)
    except KeyError:
        raise sanctum_error_response(
            "DeviceVanished",
            f"no job {job_id!r} is known to this process",
            "The API was restarted, or the id is wrong. Job history that "
            "survives a restart lives in the ledger; see GET /ledger/verify.",
        ) from None
    status["ledger_entries"] = _ledger_entries_for(services, job_id)
    return status


def _ledger_entries_for(services: AppServices, job_id: str) -> list[dict[str, Any]]:
    """Chain entries belonging to ``job_id``, as plain dicts."""
    import json

    from core.ledger.chain import Ledger

    try:
        ledger = Ledger(
            services.ledger_root,
            tool_version=services.tool_version,
            pubkey_fingerprint="",  # read-only: this never appends
        )
        found: list[dict[str, Any]] = []
        for entry in ledger.entries():
            params = ledger.params_of(entry)
            if params.get("job_id") == job_id:
                found.append(json.loads(entry.model_dump_json()))
        return found
    except (OSError, ValueError):
        return []


@router.get("/jobs/{job_id}/stream")
def job_stream(
    job_id: str, services: AppServices = Depends(get_services)
) -> StreamingResponse:
    """Server-sent events for one job.

    Reconnecting replays the whole buffered run before following it live, so a
    browser that reloaded mid-wipe sees the progress it missed rather than
    picking up blind from wherever it reattached.
    """
    try:
        services.registry.status(job_id)
    except KeyError:
        raise sanctum_error_response(
            "DeviceVanished",
            f"no job {job_id!r} is known to this process",
            "The API was restarted, or the id is wrong.",
        ) from None

    return StreamingResponse(
        progress_events(services.registry, job_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/jobs/{job_id}/cancel")
def job_cancel(
    job_id: str, services: AppServices = Depends(get_services)
) -> dict[str, Any]:
    """Ask a job to stop at its next yield.

    Cooperative, never a kill. A wipe interrupted between an lseek and a write
    is the state the checkpoint machinery exists to recover from, and creating
    it deliberately would be perverse.
    """
    try:
        return services.registry.cancel(job_id)
    except KeyError:
        raise sanctum_error_response(
            "DeviceVanished",
            f"no job {job_id!r} is known to this process",
            "The API was restarted, or the id is wrong.",
        ) from None
