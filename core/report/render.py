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

    for name in SECTION_ORDER:
        section = report["sections"][name]
        page.setFont("Helvetica-Bold", 11)
        page.drawString(left, cursor, _SECTION_TITLES[name])
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
