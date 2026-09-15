"""Report assembly and rendering.

Two artifacts per case, and which one is authoritative is stated in both:

``<case>.forensic.json``
    The signed, authoritative artifact. Canonical bytes, so the signature is
    checkable byte-for-byte by anyone with the public key.

``<case>.forensic.pdf``
    A human-readable rendering for people who will not run a verifier. It is
    **not** authoritative, and says so on the document itself. A PDF is
    trivially editable and its text is not what was signed.

Two rules the section builder enforces rather than leaves to the caller:

* Sections 6 (residual risk) and 7 (limitations) are never omitted and never
  truncated. An empty limitations section prints "none recorded" rather than
  disappearing, because a missing section reads as "nothing to say" while an
  explicit "none recorded" reads as "we checked".
* Section 3 prints the capability *evidence* - the actual probed flags - not
  only the conclusion drawn from it. A reader who disagrees with the conclusion
  can check the reasoning.

Percentages are carried as integer basis points because the canonical form
rejects floats; see :mod:`core.ledger.canon`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from core.ledger.canon import CANON_VERSION, canonical_bytes
from core.ledger.chain import ChainVerification
from core.models import Signature

__all__ = [
    "SECTION_ORDER",
    "NONE_RECORDED",
    "PDF_DISCLAIMER",
    "build_report",
    "build_file_erase_report",
    "build_carve_report",
    "dpdp_erasure_reference",
    "excerpt_gaps",
    "file_erasure_standards",
    "sanitization_standards",
    "render_json",
    "render_pdf",
    "write_report",
]

logger = structlog.get_logger(__name__)

SECTION_ORDER = (
    "case_identity",
    "device_identity",
    "method",
    "hidden_areas",
    "verification",
    "residual_risk",
    "limitations",
    "audit_trail",
    "signature",
)

NONE_RECORDED = "none recorded"

PDF_DISCLAIMER = (
    "This PDF is a human-readable rendering and is NOT the authoritative "
    "artifact. The signed JSON report is authoritative. Verify it with: "
    "sanctum verify-report <case>.forensic.json"
)


def sanitization_standards() -> dict[str, Any]:
    """What a drive erasure certificate rests on, and what it does not claim.

    Stated inside the signed bytes so the claim travels with the certificate.
    NIST SP 800-88r1 was withdrawn on 2025-09-26; r2 keeps clear, purge and
    destroy but defers technique detail to IEEE 2883, whose text this project
    has not had. The sources are in docs/compliance.md.
    """
    return {
        "method_vocabulary": (
            "clear, purge and destroy, as defined in NIST SP 800-88r2 "
            "(September 2025), Sec. 3.1. NIST SP 800-88r1 was withdrawn on "
            "2025-09-26."
        ),
        "technique_standard": (
            "NIST SP 800-88r2 Sec. 4.4 says sanitization should be performed in "
            "a manner that complies with IEEE 2883 or a standard identified as "
            "acceptable by organizational policy. This tool's techniques have "
            "not been verified against the text of IEEE 2883-2022, and this "
            "report does not claim conformance to it."
        ),
        "verification_and_validation": (
            "The verification section records this tool's read-back of the "
            "medium. NIST SP 800-88r2 Sec. 4.5.2 validation - the decision to "
            "accept the outcome or to repeat or escalate sanitization - belongs "
            "to the organization and is not made by this tool."
        ),
        "nist_sp_800_88r2_sec_4_6_fields_not_recorded": [
            "manufacturer, as a field separate from the model string",
            "organizationally assigned media or property number",
            "media source",
            "pre-sanitization confidentiality categorization (optional)",
            "name, position or title, location, contact information and "
            "signature of each individual performing verification and "
            "validation",
        ],
    }


def file_erasure_standards() -> dict[str, Any]:
    """What a file erasure report claims against the sanitization standards.

    A per-file overwrite is not a clear of a medium, so no method is claimed.
    """
    return {
        "method_vocabulary": (
            "No NIST SP 800-88r2 sanitization method is claimed. Sec. 3.1.1 "
            "defines clear over all user-addressable storage locations of a "
            "medium; a file erasure overwrites only the extents of the files "
            "named."
        ),
        "scope": (
            "This is closest to what NIST SP 800-88r2 Sec. 4.2 calls partial "
            "sanitization, which it notes carries the risk that sensitive data "
            "spilled into other areas of the medium. The residual findings "
            "section names the areas this tool knows of."
        ),
        "technique_standard": (
            "Not verified against the text of IEEE 2883-2022; no conformance "
            "is claimed."
        ),
    }


def dpdp_erasure_reference() -> dict[str, Any]:
    """The one Indian instrument an erasure record directly bears on.

    Which obligation the record helps discharge, and what it cannot establish.
    Verified against the Act (MeitY) and G.S.R. 843(E) and 846(E) of
    13 November 2025; see docs/compliance.md.
    """
    return {
        "instrument": "Digital Personal Data Protection Act, 2023, section 8(7)",
        "in_force": (
            "Not at the date of this build: G.S.R. 843(E) of 13 November 2025 "
            "brings sections 3 to 17 of the Act into force eighteen months "
            "after its publication."
        ),
        "this_record_evidences": (
            "that the device or paths named in this report were overwritten or "
            "sanitized by the method named, and the verification result this "
            "tool recorded for them"
        ),
        "requires_a_person": [
            "deciding that the specified purpose is no longer served or that "
            "consent was withdrawn",
            "confirming that no law in force requires the data to be retained",
            "finding every other copy, including backups and data made "
            "available to a Data Processor, which section 8(7)(b) also requires "
            "to be erased",
            "informing the Data Principal where rule 8(2) of the Digital "
            "Personal Data Protection Rules, 2025 applies",
            "retaining the processing logs rule 8(3) requires for at least one "
            "year, which an erasure must not destroy",
        ],
    }

_SECTION_TITLES = {
    "case_identity": "1. Case Identity",
    "device_identity": "2. Device Identity",
    "method": "3. Method",
    "hidden_areas": "4. Hidden Areas",
    "verification": "5. Verification",
    "residual_risk": "6. Residual Risk",
    "limitations": "7. Limitations",
    "audit_trail": "8. Audit Trail",
    "signature": "9. Signature",
    # File erasure. Numbered from 2 because the shapes diverge after case
    # identity and converge again at limitations; a reader comparing two reports
    # side by side should find the same thing under the same number.
    "scope": "2. Scope",
    "results": "3. Results",
    "residual_findings": "4. Residual Findings",
    "erase_verification": "5. Verification",
    # Recovery.
    "evidence": "2. Evidence",
    "acquisition_integrity": "3. Evidential Integrity",
    "recovery": "4. Recovery",
    "confidence": "5. Confidence",
}

#: Fields rendered in monospace: hashes, serials, paths, and anything an
#: operator may have to transcribe or compare character by character.
_MONOSPACE_KEYS = frozenset(
    {
        "serial",
        "by_id_path",
        "path",
        "entry_hash",
        "prev_entry_hash",
        "params_hash",
        "result_hash",
        "pubkey_fingerprint",
        "sig_b64",
        "pubkey_b64",
        "merkle_root",
    }
)


def _or_none_recorded(items: list[str]) -> list[str]:
    return list(items) if items else [NONE_RECORDED]


def _rows_or_none_recorded(rows: list[dict[str, Any]]) -> list[Any]:
    """Rows, or the single placeholder string the renderer draws for an empty
    section. The return type widens to ``list[Any]`` because the placeholder is
    a string standing in for a table of dicts, which is exactly what the
    renderer expects to see when a section recorded nothing."""
    return list(rows) if rows else [NONE_RECORDED]


def excerpt_gaps(entries: list[dict[str, Any]]) -> list[dict[str, int]]:
    """The ``seq`` ranges an excerpt omits, as inclusive ``from_seq``/``to_seq``.

    A report's excerpt is a *filtered* view: it carries the entries for one job
    plus genesis, and leaves out whatever belonged to other jobs on the same
    ledger. That is deliberate - a report for one case must not have to disclose
    another case's entries to prove its own integrity - but it means the excerpt
    cannot link end to end, and a verifier that assumed it could reported the
    first entry after a gap as a broken chain.

    The omissions are therefore declared here rather than left to be inferred
    from seq arithmetic by whoever reads the report. Declaring them puts them
    under the signature and lets a verifier cross-check what the excerpt says it
    left out against what it actually left out, so an excerpt that was trimmed
    after the fact and did not update this field is detectable.
    """
    seqs = sorted(
        int(entry["seq"])
        for entry in entries
        if isinstance(entry.get("seq"), int)
    )
    gaps: list[dict[str, int]] = []
    for earlier, later in zip(seqs, seqs[1:], strict=False):
        if later > earlier + 1:
            gaps.append(
                {
                    "from_seq": earlier + 1,
                    "to_seq": later - 1,
                    "count": later - earlier - 1,
                }
            )
    return gaps


def build_report(
    *,
    case_id: str,
    operator: str,
    generated_at: datetime,
    tool_version: str,
    device: dict[str, Any],
    method: dict[str, Any],
    hidden_areas: dict[str, Any],
    verification: dict[str, Any],
    residual_risk: dict[str, Any],
    limitations: list[str],
    ledger_excerpt: list[dict[str, Any]],
    chain_verification: ChainVerification,
    pubkey_fingerprint: str,
    merkle_root: str | None = None,
    anchor: dict[str, Any] | None = None,
    signature: Signature | None = None,
    job_state: str | None = None,
) -> dict[str, Any]:
    """Assemble the nine report sections in order.

    Returns a plain dict so it can be canonicalised and signed without any
    dependency on a serialisation library's version.
    """
    sections: dict[str, Any] = {
        "case_identity": {
            "case_id": case_id,
            "operator": operator,
            "generated_at": generated_at,
            "tool_version": tool_version,
            "canon_version": CANON_VERSION,
            "job_state": job_state or NONE_RECORDED,
        },
        "device_identity": {
            "model": device.get("model", ""),
            "serial": device.get("serial", ""),
            "by_id_path": device.get("by_id_path") or "",
            "size_bytes": int(device.get("size_bytes") or 0),
            "transport": device.get("transport", "unknown"),
            "logical_block_size": int(device.get("logical_block_size") or 0),
            "physical_block_size": int(device.get("physical_block_size") or 0),
        },
        "method": {
            "method": method.get("method", ""),
            "level_requested": method.get("level_requested", ""),
            "level_achieved": method.get("level_achieved", ""),
            "justification": method.get("justification", ""),
            "capability_evidence": method.get("capability_evidence", {}),
            "standards": sanitization_standards(),
            "regulatory_references": [dpdp_erasure_reference()],
        },
        "hidden_areas": {
            "hpa_present": bool(hidden_areas.get("hpa_present")),
            "dco_present": bool(hidden_areas.get("dco_present")),
            "native_max_sectors": int(hidden_areas.get("native_max_sectors") or 0),
            "accessible_sectors": int(hidden_areas.get("accessible_sectors") or 0),
            "hidden_bytes": int(hidden_areas.get("hidden_bytes") or 0),
            "covered_by_this_erase": bool(hidden_areas.get("covered")),
        },
        "verification": {
            "strategy": verification.get("strategy", ""),
            "passed": bool(verification.get("passed")),
            "bytes_checked": int(verification.get("bytes_checked") or 0),
            "sample_count": int(verification.get("sample_count") or 0),
            "sample_seed": verification.get("sample_seed"),
            "confidence_bp": int(verification.get("confidence_bp") or 0),
            "failed_offsets": list(verification.get("failed_offsets") or []),
            "probability_note": verification.get("probability_note", ""),
            "hw_attested": bool(verification.get("hw_attested")),
        },
        "residual_risk": {
            "level": residual_risk.get("level", ""),
            "purge_achieved": bool(residual_risk.get("purge_achieved")),
            "factors": _or_none_recorded(list(residual_risk.get("factors") or [])),
            "notes": residual_risk.get("notes") or NONE_RECORDED,
        },
        "limitations": {"items": _or_none_recorded(list(limitations))},
        "audit_trail": {
            "chain_status": chain_verification.status.value,
            "chain_explanation": chain_verification.explanation,
            "verified_through": chain_verification.verified_through,
            "first_bad_seq": chain_verification.first_bad_seq,
            "failure_kind": (
                chain_verification.failure_kind.value
                if chain_verification.failure_kind
                else None
            ),
            "entry_count": chain_verification.entry_count,
            "merkle_root": merkle_root or "",
            "anchor": anchor or {},
            "entries": list(ledger_excerpt),
            #: Declared, not inferred. See :func:`excerpt_gaps`.
            "excerpt_gaps": excerpt_gaps(list(ledger_excerpt)),
        },
        "signature": signature.model_dump() if signature else {},
    }

    report: dict[str, Any] = {
        "case_id": case_id,
        "generated_at": generated_at,
        "tool_version": tool_version,
        "canon_version": CANON_VERSION,
        "pubkey_fingerprint": pubkey_fingerprint,
        "authoritative": True,
        "authoritative_artifact": f"{case_id}.forensic.json",
        "sections": {name: sections[name] for name in SECTION_ORDER},
    }
    if signature is not None:
        report["signature"] = signature.model_dump()
    return report


def _case_identity(
    *,
    case_id: str,
    operator: str,
    generated_at: datetime,
    tool_version: str,
    job_state: str | None,
) -> dict[str, Any]:
    """Who, when, with what - and what state the documented job was in.

    ``job_state`` is inside the signed bytes so a report for a failed or a
    cancelled job says so wherever it travels. A caller with no job registry
    to ask gets ``none recorded`` rather than an implied ``complete``.
    """
    return {
        "case_id": case_id,
        "operator": operator,
        "generated_at": generated_at,
        "tool_version": tool_version,
        "canon_version": CANON_VERSION,
        "job_state": job_state or NONE_RECORDED,
    }


def _audit_trail(
    *,
    ledger_excerpt: list[dict[str, Any]],
    chain_verification: ChainVerification,
    merkle_root: str | None,
    anchor: dict[str, Any] | None,
) -> dict[str, Any]:
    """The section every report shape shares, byte for byte.

    A file erasure and a recovery are different documents from a drive erasure,
    but the thing a third party checks is the same thing in all three, and it
    must be assembled identically or `verify_report` would have to know which
    kind it was handed.
    """
    return {
        "chain_status": chain_verification.status.value,
        "chain_explanation": chain_verification.explanation,
        "verified_through": chain_verification.verified_through,
        "first_bad_seq": chain_verification.first_bad_seq,
        "failure_kind": (
            chain_verification.failure_kind.value
            if chain_verification.failure_kind
            else None
        ),
        "entry_count": chain_verification.entry_count,
        "merkle_root": merkle_root or "",
        "anchor": anchor or {},
        "entries": list(ledger_excerpt),
        "excerpt_gaps": excerpt_gaps(list(ledger_excerpt)),
    }


def _envelope(
    *,
    case_id: str,
    generated_at: datetime,
    tool_version: str,
    pubkey_fingerprint: str,
    sections: dict[str, Any],
    signature: Signature | None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "case_id": case_id,
        "generated_at": generated_at,
        "tool_version": tool_version,
        "canon_version": CANON_VERSION,
        "pubkey_fingerprint": pubkey_fingerprint,
        "authoritative": True,
        "authoritative_artifact": f"{case_id}.forensic.json",
        "sections": sections,
    }
    if signature is not None:
        report["signature"] = signature.model_dump()
    return report


def build_file_erase_report(
    *,
    case_id: str,
    operator: str,
    generated_at: datetime,
    tool_version: str,
    records: list[dict[str, Any]],
    dry_run: bool,
    limitations: list[str],
    ledger_excerpt: list[dict[str, Any]],
    chain_verification: ChainVerification,
    pubkey_fingerprint: str,
    merkle_root: str | None = None,
    anchor: dict[str, Any] | None = None,
    signature: Signature | None = None,
    job_state: str | None = None,
) -> dict[str, Any]:
    """The M2 report: what was erased, and what the filesystem kept anyway.

    The residual findings are the deliverable, not the overwrite, so they get
    their own section rather than a line inside results. A per-file overwrite is
    usually unverifiable, and this report says so per file rather than reporting
    an unverifiable erasure as a success.
    """
    findings: list[dict[str, Any]] = []
    severities: dict[str, int] = {}
    unverified = 0
    failed = 0
    bytes_overwritten = 0

    for record in records:
        path = str(record.get("path", ""))
        if not record.get("ok", False):
            failed += 1
        bytes_overwritten += int(record.get("bytes_overwritten") or 0)
        verification = record.get("verification") or {}
        if verification.get("passed") is not True:
            unverified += 1
        for finding in record.get("findings") or []:
            severity = str(finding.get("severity", ""))
            severities[severity] = severities.get(severity, 0) + 1
            findings.append(
                {
                    "path": path,
                    "kind": finding.get("kind", ""),
                    "severity": severity,
                    "addressable": bool(finding.get("addressable")),
                    "explanation": finding.get("explanation", ""),
                }
            )

    sections: dict[str, Any] = {
        "case_identity": _case_identity(
            case_id=case_id,
            operator=operator,
            generated_at=generated_at,
            tool_version=tool_version,
            job_state=job_state,
        ),
        "scope": {
            "paths_requested": len(records),
            "dry_run": dry_run,
            "paths": _or_none_recorded([str(item.get("path", "")) for item in records]),
            "standards": file_erasure_standards(),
            "regulatory_references": [dpdp_erasure_reference()],
        },
        "results": {
            "erased": len(records) - failed,
            "failed": failed,
            "bytes_overwritten": bytes_overwritten,
            "items": [
                {
                    "path": str(record.get("path", "")),
                    "ok": bool(record.get("ok")),
                    "dry_run": bool(record.get("dry_run")),
                    "bytes_overwritten": int(record.get("bytes_overwritten") or 0),
                    "unlinked": bool(record.get("unlinked")),
                    "streams_removed": list(record.get("streams_removed") or []),
                    "xattrs_removed": list(record.get("xattrs_removed") or []),
                    "error": record.get("error") or "",
                    "error_kind": record.get("error_kind") or "",
                }
                for record in records
            ]
            or [NONE_RECORDED],
        },
        "residual_findings": {
            "count": len(findings),
            "by_severity": severities or {"none": 0},
            # Never collapsed to a count. What survived, and where, is the
            # question this module exists to answer.
            "items": _rows_or_none_recorded(findings),
        },
        "erase_verification": {
            "files_verified_by_physical_read": len(records) - unverified,
            "files_not_verifiable": unverified,
            "note": (
                "A per-file overwrite is verifiable only where the filesystem "
                "lets the original extents be re-read. Where it does not, this "
                "report says the erasure was not verified rather than reporting "
                "a pass it cannot support."
            ),
            "items": [
                {
                    "path": str(record.get("path", "")),
                    "passed": (record.get("verification") or {}).get("passed"),
                    "strategy": (record.get("verification") or {}).get("strategy", ""),
                    "reason": (record.get("verification") or {}).get("reason", ""),
                }
                for record in records
            ]
            or [NONE_RECORDED],
        },
        "limitations": {"items": _or_none_recorded(list(limitations))},
        "audit_trail": _audit_trail(
            ledger_excerpt=ledger_excerpt,
            chain_verification=chain_verification,
            merkle_root=merkle_root,
            anchor=anchor,
        ),
        "signature": signature.model_dump() if signature else {},
    }
    return _envelope(
        case_id=case_id,
        generated_at=generated_at,
        tool_version=tool_version,
        pubkey_fingerprint=pubkey_fingerprint,
        sections=sections,
        signature=signature,
    )


def build_carve_report(
    *,
    case_id: str,
    operator: str,
    generated_at: datetime,
    tool_version: str,
    evidence: dict[str, Any],
    candidates: list[dict[str, Any]],
    partitions: list[dict[str, Any]],
    unallocated_bytes: int,
    written: list[str],
    limitations: list[str],
    ledger_excerpt: list[dict[str, Any]],
    chain_verification: ChainVerification,
    pubkey_fingerprint: str,
    merkle_root: str | None = None,
    anchor: dict[str, Any] | None = None,
    signature: Signature | None = None,
    job_state: str | None = None,
) -> dict[str, Any]:
    """The M3 report: what was recovered, how sure the tool is, and why.

    Confidence gets a section of its own carrying the component weights, because
    a bucket label with no arithmetic behind it is an assertion. The weights are
    calibrated; see docs/performance/calibration.md.
    """
    buckets: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    fragmented = 0
    contradicted = 0
    reassembled = 0

    for item in candidates:
        bucket = str(item.get("bucket", ""))
        buckets[bucket] = buckets.get(bucket, 0) + 1
        source = str(item.get("source", ""))
        by_source[source] = by_source.get(source, 0) + 1
        category = str(item.get("category", ""))
        by_category[category] = by_category.get(category, 0) + 1
        if item.get("contiguity_assumed"):
            fragmented += 1
        if item.get("contiguity_contradicted"):
            contradicted += 1
        if item.get("fragments"):
            reassembled += 1

    sections: dict[str, Any] = {
        "case_identity": _case_identity(
            case_id=case_id,
            operator=operator,
            generated_at=generated_at,
            tool_version=tool_version,
            job_state=job_state,
        ),
        "evidence": {
            "path": evidence.get("path", ""),
            "size_bytes": int(evidence.get("size_bytes") or 0),
            "format": evidence.get("format", ""),
            "identity": evidence.get("identity", {}),
            "partitions": _rows_or_none_recorded(partitions),
            "unallocated_bytes": unallocated_bytes,
        },
        "acquisition_integrity": {
            "opened_read_only": True,
            "note": (
                "Nothing in the carving path opens evidence for writing: "
                "core.carve.evidence declares no write method and opens "
                "O_RDONLY. Recovered objects are written to an operator-chosen "
                "output directory, never back to the evidence."
            ),
            "objects_written": len(written),
            "output_paths": _or_none_recorded(list(written)),
        },
        "recovery": {
            "candidates": len(candidates),
            "by_source": by_source or {"none": 0},
            "by_category": by_category or {"none": 0},
            "contiguity_assumed": fragmented,
            "contiguity_contradicted": contradicted,
            "reassembled_from_fragments": reassembled,
            "items": [
                {
                    "offset": int(item.get("offset") or 0),
                    "length": int(item.get("length") or 0),
                    "ext": item.get("ext", ""),
                    "mime": item.get("mime", ""),
                    "source": item.get("source", ""),
                    "validation": item.get("validation", ""),
                    # What the decoder said, verbatim. A verdict without its
                    # reason is half a finding: "valid" for a phone photo whose
                    # MPF index declares a gain map this object does not hold
                    # is only honest next to the sentence saying so.
                    "validation_detail": item.get("validation_detail") or "",
                    "bucket": item.get("bucket", ""),
                    "confidence_bp": int(item.get("confidence_bp") or 0),
                    "sha256": item.get("sha256", ""),
                    "original_name": item.get("original_name") or "",
                    # Where the bytes behind that digest were. Empty means the
                    # object is the span above; a list means it is not, and
                    # hashing offset..offset+length would cover a gap holding
                    # somebody else's data. A reader checking the digest has to
                    # know which, so the report has to say.
                    "fragments": item.get("fragments") or [],
                }
                for item in candidates
            ]
            or [NONE_RECORDED],
        },
        "confidence": {
            "by_bucket": buckets or {"none": 0},
            "thresholds": {"HIGH": 8000, "MEDIUM": 5000},
            "components": [
                "header",
                "exact_length",
                "decoder",
                "entropy",
                "fs_metadata",
                "no_overlap",
                "reassembly",
            ],
            "note": (
                "Confidence is the sum of six measured components in basis "
                "points, clamped and never scaled, plus a seventh, reassembly, "
                "that is zero unless the object was rebuilt from separate runs, "
                "where it holds the total below HIGH because the layout across "
                "the gap is inferred. The weights were calibrated against a "
                "ground-truth corpus and bounded from above by that measurement; "
                "see docs/performance/calibration.md. A bucket is a reading of "
                "the number, not a substitute for it."
            ),
        },
        "limitations": {"items": _or_none_recorded(list(limitations))},
        "audit_trail": _audit_trail(
            ledger_excerpt=ledger_excerpt,
            chain_verification=chain_verification,
            merkle_root=merkle_root,
            anchor=anchor,
        ),
        "signature": signature.model_dump() if signature else {},
    }
    return _envelope(
        case_id=case_id,
        generated_at=generated_at,
        tool_version=tool_version,
        pubkey_fingerprint=pubkey_fingerprint,
        sections=sections,
        signature=signature,
    )


def render_json(report: dict[str, Any]) -> bytes:
    """Canonical bytes of the authoritative artifact."""
    return canonical_bytes(report)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def _flatten(value: Any, indent: int = 0) -> list[tuple[int, str, str]]:
    """Flatten a section into ``(indent, key, value)`` lines for the PDF."""
    lines: list[tuple[int, str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append((indent, str(key), ""))
                lines.extend(_flatten(item, indent + 1))
            else:
                lines.append((indent, str(key), _scalar(item)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.extend(_flatten(item, indent + 1))
            else:
                lines.append((indent, "-", _scalar(item)))
    else:
        lines.append((indent, "", _scalar(value)))
    return lines


def _scalar(value: Any) -> str:
    if value is None:
        return NONE_RECORDED
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_pdf(report: dict[str, Any]) -> bytes:
    """Render the human-readable, non-authoritative PDF."""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas

    buffer = io.BytesIO()
    # pageCompression=0 keeps the text streams readable, which makes the PDF
    # greppable and lets a reviewer confirm what it says without a viewer.
    page = pdf_canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
    width, height = A4
    left = 18 * mm
    cursor = height - 20 * mm
    bottom = 20 * mm

    def newline(step: float = 4.6 * mm) -> None:
        nonlocal cursor
        cursor -= step
        if cursor < bottom:
            page.showPage()
            cursor = height - 20 * mm

    def draw(text: str, *, size: int = 9, mono: bool = False, indent: int = 0) -> None:
        page.setFont("Courier" if mono else "Helvetica", size)
        page.drawString(left + indent * 5 * mm, cursor, text[:200])
        newline()

    page.setFont("Helvetica-Bold", 15)
    page.drawString(left, cursor, f"Sanctum Forensics Report - {report['case_id']}")
    newline(8 * mm)

    page.setFont("Helvetica-Bold", 8)
    for line in _wrap(PDF_DISCLAIMER, 96):
        page.setFont("Helvetica-Bold", 8)
        page.drawString(left, cursor, line)
        newline(3.8 * mm)
    newline(3 * mm)

    # The report's own key order, not SECTION_ORDER. A drive erasure, a file
    # erasure and a recovery are different documents with different sections,
    # and every builder emits its sections in the order it wants them read.
    for name, section in report["sections"].items():
        page.setFont("Helvetica-Bold", 11)
        page.drawString(left, cursor, _SECTION_TITLES.get(name, name.title()))
        newline(5.5 * mm)
        if not section:
            draw(NONE_RECORDED, indent=1)
            continue
        for indent, key, value in _flatten(section, indent=1):
            mono = key in _MONOSPACE_KEYS or (
                isinstance(value, str) and len(value) == 64 and value.isalnum()
            )
            label = f"{key}: " if key and key != "-" else ("- " if key else "")
            width = 92 if mono else 104
            for offset, chunk in enumerate(_wrap(f"{label}{value}", width)):
                draw(chunk, mono=mono, indent=indent + (1 if offset else 0))
        newline(2 * mm)

    _draw_signature_qr(page, report, left, cursor)
    page.showPage()
    page.save()
    return buffer.getvalue()


def _draw_signature_qr(
    page: Any, report: dict[str, Any], left: float, cursor: float
) -> None:
    """Draw a QR code carrying the detached signature, when one is present."""
    signature = report.get("signature") or {}
    payload = signature.get("sig_b64")
    if not payload:
        return
    try:
        from reportlab.graphics import renderPDF
        from reportlab.graphics.barcode import qr
        from reportlab.graphics.shapes import Drawing
    except ImportError:  # pragma: no cover - reportlab always ships these
        logger.warning("qr_unavailable")
        return
    code = qr.QrCodeWidget(payload)
    bounds = code.getBounds()
    drawing = Drawing(90, 90, transform=[90.0 / (bounds[2] - bounds[0]), 0, 0,
                                         90.0 / (bounds[3] - bounds[1]), 0, 0])
    drawing.add(code)
    renderPDF.draw(drawing, page, left, max(cursor - 95, 20))


def write_report(report: dict[str, Any], out_dir: Path | str) -> tuple[Path, Path]:
    """Write both artifacts, returning ``(json_path, pdf_path)``."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    case_id = str(report["case_id"])
    json_path = directory / f"{case_id}.forensic.json"
    pdf_path = directory / f"{case_id}.forensic.pdf"
    json_path.write_bytes(render_json(report))
    pdf_path.write_bytes(render_pdf(report))
    logger.info("report_written", json=str(json_path), pdf=str(pdf_path))
    return json_path, pdf_path
