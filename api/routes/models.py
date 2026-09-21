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
    "CaseCreateRequest",
    "EvidenceAttachRequest",
    "TamperDemoRequest",
    "ResumeEraseRequest",
    "EraseDriveRequest",
    "EraseFilesRequest",
    "WipeFreeSpaceRequest",
    "AcquireRequest",
    "CarveRequest",
    "ReportRequest",
    "JobAccepted",
]


class CaseCreateRequest(BaseModel):
    """Body for ``POST /cases``.

    There is no ``created_by``. The author is the trusted local identity the
    helper resolves, and a field here would be a second, spoofable source for
    the one value a chain of custody exists to record.
    """

    case_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)


class EvidenceAttachRequest(BaseModel):
    """Body for ``POST /cases/{case_id}/evidence``.

    Registration only: this records that an exhibit exists and what its hashes
    are. Nothing here opens a device or reads an image - acquisition is a job,
    with its own read-only path and its own chain entries.
    """

    evidence_id: str = Field(min_length=1, max_length=64)
    #: Free text describing where the exhibit came from.
    source: str = Field(default="", max_length=500)
    media_type: str = Field(default="image", max_length=64)
    acquired_at: str = Field(default="", max_length=64)
    #: The hash recorded at acquisition, if there is one.
    source_hash: str = Field(default="", max_length=128)
    #: The hash a later read-back produced, if one was done.
    verification_hash: str = Field(default="", max_length=128)
    state: str = Field(default="registered", max_length=32)


class TamperDemoRequest(BaseModel):
    """Body for ``POST /ledger/tamper-demo``.

    ``seq`` names the entry to alter **in the scratch copy**. The production
    chain is never opened for writing by this endpoint; see
    :func:`api.routes.audit.tamper_demo`.
    """

    #: Which entry to corrupt in the copy. Defaults to the middle of the chain,
    #: which is the interesting case: it shows both the intact prefix and the
    #: unverifiable suffix.
    seq: int | None = None


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


class WipeFreeSpaceRequest(BaseModel):
    """Body for ``POST /jobs/wipe-free-space``."""

    #: The volume's mount point, exactly. A folder inside a volume is refused.
    mount_point: str
    #: Gate one.
    dry_run: bool = True
    #: Gate two: the volume identifier a dry run reports (the filesystem UUID,
    #: or the mount point when the volume has none).
    typed_identifier: str = ""
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
    #: Walk filesystem metadata for deleted entries before carving. The undelete
    #: pass also returns the allocated/unallocated map, which is reported but
    #: deliberately does not bound the carve: bounding was measured and loses
    #: recall. It does supply each volume's cluster size to bifragment
    #: reassembly; with this off, reassembly walks 512-byte sectors instead,
    #: which is slower and reaches less. See :mod:`api.carve_job`.
    undelete: bool = True
    #: Run the signature and structure carvers over the whole image. Both, from
    #: one pass: the structure carver runs the signature scan itself and then
    #: derives each object's length from its own format where a parser exists.
    #: A baseline JPEG split into exactly two runs, with a gap of at most 2 MiB,
    #: is reassembled and scored MEDIUM at most; nothing else is. See
    #: :mod:`api.carve_job` for the exact scope.
    carve_signatures: bool = True
    #: Count Aadhaar, PAN, IFSC, mobile, card and email identifiers in each
    #: document, database or unclassified object. Kinds and counts only: no
    #: matched value is stored, logged or returned. See :mod:`core.carve.pii`.
    pii_triage: bool = True
    #: Where recovered objects are written. None means nothing is written and
    #: only the candidate list is returned.
    out_dir: str | None = None
    case_id: str = ""
    operator: str = "sanctum"


class ResumeEraseRequest(BaseModel):
    """Body for ``POST /jobs/{job_id}/resume``.

    Both gates again. A resume writes to the medium and is not a lesser
    operation than the run it continues.
    """

    dry_run: bool = True
    typed_serial: str = ""
    operator: str = "sanctum"


class ReportRequest(BaseModel):
    """Body for ``POST /reports/{job_id}``."""

    case_id: str = ""
    operator: str = "sanctum"
    #: The signing-key passphrase, for the desktop app, which has no terminal
    #: and no environment the operator set. Used to open the key for this one
    #: request; never logged, never stored, never echoed into the report.
    key_passphrase: str = Field(default="", repr=False)


class JobAccepted(BaseModel):
    """What every job endpoint returns: an id to stream, not a result."""

    job_id: str
    kind: str
    state: str
    dry_run: bool
    #: Where to attach for live progress.
    stream_url: str
