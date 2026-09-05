"""``/ledger/verify`` and ``/reports/*`` - the audit surface.

The two endpoints answer different questions and must not be conflated.
``/ledger/verify`` asks whether *this host's* chain is internally consistent.
``/reports/{job_id}/verify`` asks whether *a particular report* is what it says
it is, which is a claim a third party checks on their own machine with only the
file in front of them.

The report verification runs the five checks independently and reports each
one, rather than reducing them to a single pass/fail. They fail for different
reasons and a reader needs to know which: a broken signature means the bytes
changed, while an unverifiable fingerprint means only that the key was not
published anywhere this host can reach - which is not a defect in the report at
all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from core.errors import SanctumError
from fastapi import APIRouter, Depends

from api.deps import AppServices
from api.routes.common import get_services, sanctum_error_response
from api.routes.models import ReportRequest

__all__ = ["router"]

router = APIRouter(tags=["audit"])


def _ledger(services: AppServices) -> Any:
    from core.ledger.chain import Ledger

    return Ledger(
        services.ledger_root,
        tool_version=services.tool_version,
        pubkey_fingerprint="",
    )


@router.get("/ledger/verify")
def verify_chain(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    """Walk the hash chain and report its status.

    Entry N contains the SHA-256 of entry N-1, so a modified or removed entry
    breaks every link after it. The verification names the first broken seq
    rather than only saying "invalid": an examiner needs to know how much of
    the chain is still trustworthy, which is everything before the break.
    """
    from core.errors import LedgerChainBroken

    try:
        ledger = _ledger(services)
        verification = ledger.verify(check_blobs=True)
        entries = ledger.entries()
    except LedgerChainBroken as exc:
        raise sanctum_error_response(
            "LedgerChainBroken", exc.message, exc.remediation
        ) from exc
    except (OSError, ValueError) as exc:
        return {
            "status": "EMPTY",
            "entry_count": 0,
            "explanation": f"No readable ledger at {services.ledger_root}: {exc}",
            "entries": [],
        }

    return {
        "status": verification.status.value,
        "entry_count": verification.entry_count,
        "explanation": verification.explanation,
        "first_broken_seq": getattr(verification, "first_broken_seq", None),
        "root": str(services.ledger_root),
        # Newest first: the audit screen shows the tail, which is what an
        # operator has just done and is looking for.
        "entries": [json.loads(item.model_dump_json()) for item in reversed(entries)],
    }


@router.get("/ledger/entries")
def ledger_entries(
    limit: int = 200, services: AppServices = Depends(get_services)
) -> dict[str, Any]:
    """The chain's entries, newest first, without re-verifying it."""
    try:
        entries = _ledger(services).entries()
    except (OSError, ValueError):
        return {"entries": [], "entry_count": 0}
    ordered = list(reversed(entries))[: max(limit, 0)]
    return {
        "entries": [json.loads(item.model_dump_json()) for item in ordered],
        "entry_count": len(entries),
    }


@router.post("/reports/{job_id}")
def generate_report(
    job_id: str,
    body: ReportRequest,
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    """Build, sign and write the JSON and PDF artifacts for one job.

    The JSON is authoritative and the PDF is not: the signature covers the
    canonical JSON bytes, and the PDF is a rendering for a human. That
    distinction is printed on the PDF itself so nobody has to be told.
    """
    from core.report.render import build_report, write_report
    from core.report.sign import (
        fingerprint,
        load_or_create_key,
        public_key_of,
        sign_report,
    )

    try:
        status = services.registry.status(job_id)
    except KeyError:
        status = {"job_id": job_id, "state": "unknown", "result": None}

    ledger = _ledger(services)
    try:
        verification = ledger.verify()
        excerpt = [
            json.loads(entry.model_dump_json())
            for entry in ledger.entries()
            if ledger.params_of(entry).get("job_id") == job_id
            or entry.operation == "GENESIS"
        ]
    except (OSError, ValueError) as exc:
        raise sanctum_error_response(
            "LedgerChainBroken",
            f"The ledger at {services.ledger_root} could not be read: {exc}",
            "A report without a ledger excerpt is not independently auditable. "
            "Check that the ledger root exists and is readable.",
        ) from exc

    try:
        key = load_or_create_key(services.key_dir or (services.state_dir / "keys"))
        pub = public_key_of(key)
        finger = fingerprint(pub)
    except SanctumError as exc:
        # KeyPassphraseMissing and KeyPermissionsUnsafe both land here, and
        # both already carry a remediation naming the exact fix. Rewriting it
        # would replace "set SANCTUM_KEY_PASSPHRASE" with a vaguer sentence
        # about permissions that happens to be wrong half the time.
        raise sanctum_error_response(
            type(exc).__name__, exc.message, exc.remediation
        ) from exc
    except (OSError, ValueError) as exc:
        raise sanctum_error_response(
            "SignatureInvalid",
            f"The signing key could not be loaded: {exc}",
            "Check the key directory's permissions; a private key must not be "
            "group- or world-readable.",
        ) from exc

    result = status.get("result") or {}
    fields: dict[str, Any] = {
        "case_id": body.case_id or job_id,
        "operator": body.operator,
        "generated_at": datetime.now(UTC),
        "tool_version": services.tool_version,
        "device": _section(result, "device"),
        "method": _section(result, "plan"),
        "hidden_areas": _section(result, "hidden_areas"),
        "verification": _section(result, "verification"),
        "residual_risk": _section(result, "residual_risk"),
        "limitations": list(result.get("limitations") or []) + services.limitations,
        "ledger_excerpt": excerpt,
        "chain_verification": verification,
        "pubkey_fingerprint": finger,
    }

    # Built twice, deliberately. sign_report signs the canonical bytes of the
    # report *without* a signature block, so the document that is signed and
    # the document that is written must be assembled from identical inputs -
    # anything else and the signature would verify against a document nobody
    # has. Rebuilding with the same `fields` is what guarantees that; mutating
    # the first dict would work today and break the moment build_report starts
    # deriving a field from another.
    unsigned = build_report(**fields)
    signature = sign_report(unsigned, key)
    signed = build_report(**fields, signature=signature)
    json_path, pdf_path = write_report(signed, services.reports_dir)

    return {
        "job_id": job_id,
        "json_path": str(json_path),
        "pdf_path": str(pdf_path),
        "pubkey_fingerprint": finger,
        "bytes": json_path.stat().st_size,
    }


def _section(result: dict[str, Any], key: str) -> dict[str, Any]:
    """One report section from a job result, or an empty one.

    Empty rather than absent: every section is rendered, and a section that is
    simply missing from a report reads as an oversight where an explicitly
    empty one reads as "this job had nothing to say here".
    """
    value = result.get(key)
    return value if isinstance(value, dict) else {}


@router.get("/reports/{job_id}/verify")
def verify_report_endpoint(
    job_id: str, services: AppServices = Depends(get_services)
) -> dict[str, Any]:
    """Run the five report checks against this host's ledger, independently.

    Each check is reported on its own. Reducing them to one boolean would hide
    the difference between "the bytes changed" and "the key was never published
    anywhere I can reach", and only the first is a reason to distrust the
    report.
    """
    from core.report.verify_report import verify_report_file

    candidates = sorted(services.reports_dir.glob(f"*{job_id}*.forensic.json"))
    if not candidates:
        candidates = sorted(services.reports_dir.glob("*.forensic.json"))
    if not candidates:
        raise sanctum_error_response(
            "SignatureInvalid",
            f"No report for {job_id!r} was found in {services.reports_dir}",
            "Generate one with POST /reports/{job_id} before verifying it.",
        )

    target = candidates[-1]
    try:
        verification = verify_report_file(target, ledger_root=services.ledger_root)
    except (OSError, ValueError) as exc:
        raise sanctum_error_response(
            "SignatureInvalid",
            f"{target.name} could not be read as a report: {exc}",
            "The file is not valid JSON, or is not a Sanctum report.",
        ) from exc

    return {
        "report": str(target),
        "passed": verification.ok,
        "fingerprint": verification.fingerprint,
        # The identity caveat travels with the result. A signature proves the
        # bytes have not changed; it does not prove who produced them, and a
        # verification screen that omitted that would invite exactly the
        # conclusion the caveat exists to prevent.
        "caveat": verification.caveat,
        "checks": [
            {
                "name": check.name.value,
                "passed": check.passed,
                # An inapplicable check is not a passing one. A report verified
                # on a host with no ledger cannot have its chain checked, and
                # rendering that as a pass would overstate what was confirmed.
                "applicable": check.applicable,
                "detail": check.detail,
            }
            for check in verification.checks
        ],
    }
