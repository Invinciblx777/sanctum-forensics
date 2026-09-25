"""The PDF rendering of a signed report: a certificate page, then the record.

The signed JSON is the authoritative artifact; this is a rendering of it for a
person. The first page answers, in one screen, what a reader of a
sanitization certificate is looking for - which medium, which level was asked
for and which was achieved, how it was verified, who ran it, and how to check
the paper against the signed file. Every value on it is read from the report;
nothing is computed that the report does not already state, except the SHA-256
of the report's own canonical bytes, which is how the paper is matched to the
file.

The headline is derived conservatively. A dry run is a simulation, not a
sanitization. A job that did not complete, a read-back that failed, or a run
with no verification recorded is said to be exactly that, in the band where a
reader looks first - never softened into a pass.

The remaining pages print every section of the report, in the report's own
order, so nothing the JSON says is missing from the paper.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Iterable
from typing import Any

__all__ = ["render_certificate_pdf"]

# Print colours: dark ink on white, one violet for what the signature attests.
_INK = (0.07, 0.08, 0.09)
_GREY = (0.36, 0.40, 0.44)
_RULE = (0.84, 0.86, 0.88)
_SEAL = (0.29, 0.25, 0.72)
_SEAL_BG = (0.93, 0.92, 1.0)
_TONES: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "success": ((0.11, 0.49, 0.33), (0.89, 0.96, 0.93)),
    "warning": ((0.54, 0.35, 0.0), (1.0, 0.95, 0.84)),
    "destructive": ((0.71, 0.14, 0.09), (0.99, 0.93, 0.92)),
    "neutral": ((0.21, 0.25, 0.29), (0.93, 0.95, 0.96)),
    "seal": (_SEAL, _SEAL_BG),
}

_KIND_TITLES = {
    "drive": (
        "Certificate of Sanitization",
        "Whole-drive erasure, recorded under NIST SP 800-88 Rev. 2",
    ),
    "files": (
        "Certificate of File Erasure",
        "File and folder erasure, with what the filesystem may still hold",
    ),
    "carve": (
        "Forensic Recovery Report",
        "Carving and recovery from an evidence image opened read-only",
    ),
    "other": ("Sanctum Forensics Report", "A signed record of one operation"),
}


def _kind(sections: dict[str, Any]) -> str:
    if "device_identity" in sections and "method" in sections:
        return "drive"
    if "scope" in sections and "results" in sections:
        return "files"
    if "evidence" in sections and "recovery" in sections:
        return "carve"
    return "other"


def _bytes(value: Any) -> str:
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return str(value)
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return (
                f"{count:,} bytes"
                if unit == "B"
                else f"{size:.2f} {unit} ({count:,} bytes)"
            )
        size /= 1024
    return f"{count:,} bytes"


def _text(value: Any, fallback: str = "not recorded") -> str:
    if value is None or value == "" or value == []:
        return fallback
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _verdict(kind: str, sections: dict[str, Any]) -> tuple[str, str, str]:
    """The headline word, its basis, and its tone. Conservative by design."""
    identity = sections.get("case_identity") or {}
    state = str(identity.get("job_state") or "")
    if state and state not in ("complete", "none recorded"):
        return (
            f"Not a certificate: the job {state}",
            "This record documents a job that did not complete. Read its limitations.",
            "destructive",
        )
    limitations = (sections.get("limitations") or {}).get("items") or []
    simulated = any(
        str(item).startswith("SIMULATION / NO PHYSICAL DEVICE MODIFIED")
        for item in limitations
    )
    if kind == "drive":
        method = sections.get("method") or {}
        check = sections.get("verification") or {}
        achieved = str(method.get("level_achieved") or "")
        requested = _text(method.get("level_requested"))
        if simulated:
            return (
                "Simulation: no device was sanitized",
                f"{_text(method.get('method'))} ran against a host file standing in "
                f"for a device, and read it back by {_text(check.get('strategy'))}. "
                "It shows the procedure, not a sanitized medium.",
                "neutral",
            )
        if achieved.startswith("NONE (dry run"):
            return (
                "Simulation: nothing was written",
                f"A dry run of {_text(method.get('method'))} for {requested}. "
                "No level was achieved and nothing was verified.",
                "neutral",
            )
        if int(check.get("bytes_checked") or 0) == 0 and not check.get("hw_attested"):
            return (
                "No read-back verification recorded",
                f"Level asked for: {requested}. Level achieved: {_text(achieved)}. "
                "Do not rely on this medium as sanitized.",
                "warning",
            )
        if not check.get("passed"):
            return (
                "Verification failed: not proven sanitized",
                f"{len(check.get('failed_offsets') or [])} sampled offsets did "
                "not read back as expected.",
                "destructive",
            )
        if not achieved:
            return (
                "Level achieved not recorded",
                "The run verified, but the record does not state the level "
                "it achieved.",
                "warning",
            )
        return (
            f"{achieved} achieved and verified",
            f"{_text(method.get('method'))}, read back by "
            f"{_text(check.get('strategy'))} over "
            f"{int(check.get('bytes_checked') or 0):,} bytes.",
            "success",
        )
    if kind == "files":
        scope = sections.get("scope") or {}
        results = sections.get("results") or {}
        checks = sections.get("erase_verification") or {}
        paths = int(scope.get("paths_requested") or 0)
        erased = int(results.get("erased") or 0)
        failed = int(results.get("failed") or 0)
        basis = (
            f"{int(checks.get('files_verified_by_physical_read') or 0)} verified "
            "by a physical read, "
            f"{int(checks.get('files_not_verifiable') or 0)} not verifiable here."
        )
        if scope.get("dry_run"):
            return ("Simulation: nothing was written", basis, "neutral")
        if failed:
            return (
                f"{erased} of {paths} erased, {failed} failed",
                basis,
                "warning",
            )
        return (f"{erased} of {paths} erased", basis, "success")
    if kind == "carve":
        recovery = sections.get("recovery") or {}
        buckets = (sections.get("confidence") or {}).get("by_bucket") or {}
        counts = ", ".join(
            f"{int(v)} {str(k).lower()}" for k, v in buckets.items() if k != "none"
        )
        return (
            "Evidence opened read-only",
            f"{int(recovery.get('candidates') or 0)} objects recovered"
            + (f": {counts} confidence." if counts else "."),
            "seal",
        )
    return ("Signed record", "", "neutral")


def _facts(
    kind: str, sections: dict[str, Any]
) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """Grouped (label, value, monospace) rows for the certificate page."""
    identity = sections.get("case_identity") or {}
    platform = identity.get("platform") or {}
    groups: list[tuple[str, list[tuple[str, str, bool]]]] = []
    if kind == "drive":
        device = sections.get("device_identity") or {}
        method = sections.get("method") or {}
        hidden = sections.get("hidden_areas") or {}
        check = sections.get("verification") or {}
        risk = sections.get("residual_risk") or {}
        groups.append(
            (
                "Media",
                [
                    ("Model", _text(device.get("model")), False),
                    ("Serial number", _text(device.get("serial")), True),
                    ("Capacity", _bytes(device.get("size_bytes")), False),
                    ("Interface", _text(device.get("transport")), False),
                    ("Persistent path", _text(device.get("by_id_path")), True),
                ],
            )
        )
        hidden_bytes = int(hidden.get("hidden_bytes") or 0)
        groups.append(
            (
                "Sanitization",
                [
                    ("Level asked for", _text(method.get("level_requested")), False),
                    ("Level achieved", _text(method.get("level_achieved")), False),
                    ("Method", _text(method.get("method")), True),
                    ("Why this method", _text(method.get("justification")), False),
                    (
                        "Hidden areas",
                        "none measured"
                        if not hidden_bytes
                        else f"{_bytes(hidden_bytes)} behind HPA/DCO; covered: "
                        f"{_text(hidden.get('covered_by_this_erase'))}",
                        False,
                    ),
                ],
            )
        )
        groups.append(
            (
                "Verification",
                [
                    ("Strategy", _text(check.get("strategy")), False),
                    (
                        "Result",
                        "passed" if check.get("passed") else "not passed",
                        False,
                    ),
                    (
                        "Bytes read back",
                        f"{int(check.get('bytes_checked') or 0):,}",
                        False,
                    ),
                    (
                        "Detection confidence",
                        f"{int(check.get('confidence_bp') or 0) / 100:.2f}%",
                        False,
                    ),
                    ("Residual risk", _text(risk.get("level")), False),
                ],
            )
        )
    elif kind == "files":
        scope = sections.get("scope") or {}
        results = sections.get("results") or {}
        findings = sections.get("residual_findings") or {}
        groups.append(
            (
                "Scope",
                [
                    (
                        "Paths requested",
                        str(int(scope.get("paths_requested") or 0)),
                        False,
                    ),
                    ("Dry run", _text(scope.get("dry_run")), False),
                    (
                        "Bytes overwritten",
                        f"{int(results.get('bytes_overwritten') or 0):,}",
                        False,
                    ),
                ],
            )
        )
        severity = findings.get("by_severity") or {}
        groups.append(
            (
                "What may survive",
                [
                    ("Residual findings", str(int(findings.get("count") or 0)), False),
                    (
                        "By severity",
                        ", ".join(f"{k} {v}" for k, v in severity.items()) or "none",
                        False,
                    ),
                ],
            )
        )
    elif kind == "carve":
        evidence = sections.get("evidence") or {}
        integrity = sections.get("acquisition_integrity") or {}
        recovery = sections.get("recovery") or {}
        ident = evidence.get("identity") or {}
        groups.append(
            (
                "Evidence",
                [
                    ("Image", _text(evidence.get("path")), True),
                    ("Size", _bytes(evidence.get("size_bytes")), False),
                    (
                        "SHA-256",
                        _text(ident.get("sha256") if isinstance(ident, dict) else None),
                        True,
                    ),
                    (
                        "Opened read-only",
                        _text(integrity.get("opened_read_only")),
                        False,
                    ),
                ],
            )
        )
        groups.append(
            (
                "Recovery",
                [
                    (
                        "Objects recovered",
                        str(int(recovery.get("candidates") or 0)),
                        False,
                    ),
                    (
                        "Rebuilt from fragments",
                        str(int(recovery.get("reassembled_from_fragments") or 0)),
                        False,
                    ),
                    (
                        "Objects written",
                        str(int(integrity.get("objects_written") or 0)),
                        False,
                    ),
                ],
            )
        )
    groups.append(
        (
            "Accountability",
            [
                ("Case", _text(identity.get("case_id")), True),
                (
                    "Run by (OS account)",
                    _text(identity.get("operator")),
                    True,
                ),
                ("Generated", _text(identity.get("generated_at")), True),
                ("Tool", _text(identity.get("tool_version")), True),
                (
                    "Host",
                    " ".join(
                        str(platform.get(k) or "") for k in ("os", "machine")
                    ).strip()
                    or "not recorded",
                    False,
                ),
            ],
        )
    )
    return groups


def _wrap(
    text: str,
    font: str,
    size: float,
    width: float,
    measure: Callable[[str, str, float], float],
) -> list[str]:
    """Wrap to ``width`` points, breaking a word that is itself too long."""
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if measure(candidate, font, size) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            while measure(word, font, size) > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and measure(word[:cut], font, size) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _flatten(value: Any, depth: int = 0) -> Iterable[tuple[int, str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                yield depth, str(key), ""
                yield from _flatten(item, depth + 1)
            else:
                yield depth, str(key), _text(item, "none recorded")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                yield from _flatten(item, depth + 1)
            else:
                yield depth, "-", _text(item, "none recorded")
    else:
        yield depth, "", _text(value, "none recorded")


def render_certificate_pdf(
    report: dict[str, Any],
    *,
    canonical: bytes,
    disclaimer: str,
    section_titles: dict[str, str],
    monospace_keys: frozenset[str],
) -> bytes:
    """Render ``report``: one certificate page, then every section."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as pdf_canvas

    sections: dict[str, Any] = report.get("sections") or {}
    kind = _kind(sections)
    title, subtitle = _KIND_TITLES[kind]
    word, basis, tone = _verdict(kind, sections)
    digest = hashlib.sha256(canonical).hexdigest()
    signature = report.get("signature") or {}
    fingerprint = str(
        report.get("pubkey_fingerprint") or signature.get("pubkey_fingerprint") or ""
    )

    buffer = io.BytesIO()
    # pageCompression=0 keeps the text streams readable, so the PDF is
    # greppable and a reviewer can confirm what it says without a viewer.
    page = pdf_canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
    page.setTitle(f"{title} - {report.get('case_id', '')}")
    page.setAuthor("Sanctum Forensics")
    width, height = A4
    left, right = 18 * mm, width - 18 * mm
    span = right - left

    def text(
        x: float,
        y: float,
        value: str,
        *,
        font: str = "Helvetica",
        size: float = 9,
        colour: tuple[float, float, float] = _INK,
    ) -> None:
        page.setFillColorRGB(*colour)
        page.setFont(font, size)
        page.drawString(x, y, value)

    # ---- certificate page ------------------------------------------------
    page.setFillColorRGB(*_SEAL)
    page.rect(0, height - 5, width, 5, stroke=0, fill=1)
    cursor = height - 20 * mm
    text(
        left, cursor, "Sanctum Forensics", font="Helvetica-Bold", size=10, colour=_SEAL
    )
    case_label = f"Case {report.get('case_id', '')}"
    text(
        right - stringWidth(case_label, "Courier", 9),
        cursor,
        case_label,
        font="Courier",
        size=9,
    )
    cursor -= 13 * mm
    text(left, cursor, title, font="Helvetica-Bold", size=22)
    cursor -= 6.5 * mm
    text(left, cursor, subtitle, size=10, colour=_GREY)
    cursor -= 9 * mm

    ink, fill = _TONES[tone]
    basis_lines = (
        _wrap(basis, "Helvetica", 9, span - 12 * mm, stringWidth) if basis else []
    )
    band = 12 * mm + 4.2 * mm * len(basis_lines)
    page.setFillColorRGB(*fill)
    page.setStrokeColorRGB(*ink)
    page.setLineWidth(1)
    page.roundRect(left, cursor - band, span, band, 3 * mm, stroke=1, fill=1)
    text(
        left + 6 * mm, cursor - 8 * mm, word, font="Helvetica-Bold", size=14, colour=ink
    )
    line_y = cursor - 12.5 * mm
    for line in basis_lines:
        text(left + 6 * mm, line_y, line, size=9, colour=_INK)
        line_y -= 4.2 * mm
    cursor -= band + 8 * mm

    column = (span - 8 * mm) / 2
    label_width = 42 * mm
    positions = [left, left + column + 8 * mm]
    column_cursor = [cursor, cursor]
    for index, (heading, rows) in enumerate(_facts(kind, sections)):
        side = 0 if column_cursor[0] >= column_cursor[1] else 1
        x = positions[side]
        y = column_cursor[side]
        text(
            x,
            y,
            heading,
            font="Helvetica-Bold",
            size=10.5,
            colour=_SEAL if index % 2 else _INK,
        )
        page.setStrokeColorRGB(*_RULE)
        page.line(x, y - 2 * mm, x + column, y - 2 * mm)
        y -= 6.5 * mm
        for label, value, mono in rows:
            font = "Courier" if mono else "Helvetica"
            lines = _wrap(value, font, 8.6, column - label_width, stringWidth)
            text(x, y, label, size=7.8, colour=_GREY)
            for line in lines:
                text(x + label_width, y, line, font=font, size=8.6)
                y -= 4.1 * mm
            y -= 1.2 * mm
        column_cursor[side] = y - 4 * mm
    cursor = min(column_cursor)

    # Signature block, pinned above the footer.
    block_top = max(min(cursor, 92 * mm), 70 * mm)
    block = 50 * mm
    page.setFillColorRGB(*_SEAL_BG)
    page.setStrokeColorRGB(*_SEAL)
    page.roundRect(left, block_top - block, span, block, 3 * mm, stroke=1, fill=1)
    x = left + 6 * mm
    y = block_top - 8 * mm
    signed = bool(signature.get("sig_b64"))
    text(
        x,
        y,
        "Digitally signed, Ed25519"
        if signed
        else "UNSIGNED: this record carries no signature",
        font="Helvetica-Bold",
        size=11,
        colour=_SEAL if signed else _TONES["destructive"][0],
    )
    y -= 6 * mm
    for label, value in (
        ("Signing key", fingerprint or "none"),
        ("Signed file", str(report.get("authoritative_artifact") or "")),
        ("SHA-256 of the signed file", digest),
    ):
        text(x, y, label, size=7.8, colour=_GREY)
        y -= 3.8 * mm
        for line in _wrap(value, "Courier", 8.2, span - 60 * mm, stringWidth):
            text(x, y, line, font="Courier", size=8.2)
            y -= 3.8 * mm
        y -= 0.8 * mm
    text(
        x,
        y,
        f"Verify: sanctum verify-report {report.get('authoritative_artifact', '')}",
        font="Courier",
        size=8,
        colour=_INK,
    )
    if signed:
        payload = (
            f"sanctum-report:v1\ncase={report.get('case_id', '')}\nsha256={digest}"
            f"\nkey={fingerprint}\nsig={signature.get('sig_b64', '')}"
        )
        code = qr.QrCodeWidget(payload)
        bounds = code.getBounds()
        size = 42 * mm
        drawing = Drawing(
            size,
            size,
            transform=[
                size / (bounds[2] - bounds[0]),
                0,
                0,
                size / (bounds[3] - bounds[1]),
                0,
                0,
            ],
        )
        drawing.add(code)
        renderPDF.draw(drawing, page, right - size - 4 * mm, block_top - block + 4 * mm)

    for index, line in enumerate(
        _wrap(disclaimer, "Helvetica-Bold", 7.5, span, stringWidth)
    ):
        text(
            left,
            14 * mm - index * 3.4 * mm,
            line,
            font="Helvetica-Bold",
            size=7.5,
            colour=_GREY,
        )
    page.showPage()

    # ---- the full record ---------------------------------------------------
    cursor = height - 20 * mm
    bottom = 18 * mm

    def advance(step: float) -> None:
        nonlocal cursor
        cursor -= step
        if cursor < bottom:
            page.showPage()
            cursor = height - 20 * mm

    text(left, cursor, "The full record", font="Helvetica-Bold", size=15)
    advance(6 * mm)
    for line in _wrap(disclaimer, "Helvetica-Bold", 8, span, stringWidth):
        text(left, cursor, line, font="Helvetica-Bold", size=8, colour=_GREY)
        advance(3.8 * mm)
    advance(3 * mm)
    for name, section in sections.items():
        advance(2 * mm)
        text(
            left,
            cursor,
            section_titles.get(name, name.replace("_", " ").title()),
            font="Helvetica-Bold",
            size=11,
            colour=_SEAL,
        )
        page.setStrokeColorRGB(*_RULE)
        page.line(left, cursor - 1.8 * mm, right, cursor - 1.8 * mm)
        advance(6 * mm)
        if not section:
            text(left + 4 * mm, cursor, "none recorded", size=8.6)
            advance(4.4 * mm)
            continue
        for depth, key, value in _flatten(section, 1):
            mono = key in monospace_keys or (len(value) == 64 and value.isalnum())
            font = "Courier" if mono else "Helvetica"
            indent = left + depth * 4.5 * mm
            label = f"{key}: " if key and key != "-" else ("- " if key else "")
            label_width = stringWidth(label, "Helvetica-Bold", 8.4) if label else 0
            lines = (
                _wrap(value, font, 8.4, right - indent - label_width, stringWidth)
                if value
                else [""]
            )
            if label:
                text(
                    indent, cursor, label, font="Helvetica-Bold", size=8.4, colour=_GREY
                )
            for line in lines:
                text(indent + label_width, cursor, line, font=font, size=8.4)
                advance(4.2 * mm)
    page.showPage()
    page.save()
    return buffer.getvalue()
