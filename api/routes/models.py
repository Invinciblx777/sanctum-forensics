"""Request and response bodies for the API.

Separate from :mod:`core.models` on purpose. These describe *what a browser is
allowed to ask for*, which is a narrower thing than what the core layers can
represent: a request body cannot name an erase method, cannot supply a
capability set, and cannot set a flag that would let a wipe run without a typed
serial. Reusing the core models here would widen the attack surface of every
endpoint to the full expressiveness of the domain.

**Every destructive flag defaults to the safe value.** ``dry_run`` is ``True``
in the model, so a request body that omits it simulates. A field that defaulted
the other way would make a forgotten key destructive, and that is the one
direction this cannot fail in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "EraseDriveRequest",
    "EraseFilesRequest",
    "AcquireRequest",
    "CarveRequest",
    "ReportRequest",
    "JobAccepted",
]


class EraseDriveRequest(BaseModel):
    """Body for ``POST /jobs/erase-drive``."""

    #: Device path or by-id link. Resolved and re-read by the helper, so a
    #: stale value fails the serial check rather than erasing the wrong disk.
    path: str
    level: Literal["CLEAR", "PURGE"] = "CLEAR"
    #: Gate one. True means nothing is written. Defaults closed.
    dry_run: bool = True
    #: Gate two. The operator types the device serial; the helper compares it
    #: against the serial it reads itself, not against anything the UI sent.
    typed_serial: str = ""
    case_id: str = ""
    operator: str = "sanctum"


class EraseFilesRequest(BaseModel):
    """Body for ``POST /jobs/erase-files``."""

    paths: list[str] = Field(min_length=1)
    #: Gate one.
    dry_run: bool = True
    #: Gate two. File erasure has no serial to type, so an explicit confirm
    #: takes its place - the same two-gate shape as a drive wipe.
    confirm: bool = False
    cleanse_metadata: bool = True
    #: Overwriting a file with more than one hard link destroys data reachable
    #: under names the operator did not give. Off by default; the finding is
    #: reported either way.
    break_hardlinks: bool = False
    recursive: bool = True
    case_id: str = ""
    operator: str = "sanctum"


class AcquireRequest(BaseModel):
    """Body for ``POST /jobs/acquire``. Read-only: no dry-run gate needed."""

    source: str
    dest: str
    fmt: Literal["raw", "e01"] = "raw"
    compression: Literal["none", "fast", "best"] = "fast"
    case_id: str = ""
    operator: str = "sanctum"


class CarveRequest(BaseModel):
    """Body for ``POST /jobs/carve``. Read-only by construction."""

    image: str
    #: Walk filesystem metadata for deleted entries before signature carving.
    #: The undelete pass also returns the unallocated map the carver then uses.
    undelete: bool = True
    #: Run the signature and structure carvers over the image.
    carve_signatures: bool = True
    #: Where recovered objects are written. None means nothing is written and
    #: only the candidate list is returned.
    out_dir: str | None = None
    case_id: str = ""
    operator: str = "sanctum"


class ReportRequest(BaseModel):
    """Body for ``POST /reports/{job_id}``."""

    case_id: str = ""
    operator: str = "sanctum"


class JobAccepted(BaseModel):
    """What every job endpoint returns: an id to stream, not a result."""

    job_id: str
    kind: str
    state: str
    dry_run: bool
    #: Where to attach for live progress.
    stream_url: str
