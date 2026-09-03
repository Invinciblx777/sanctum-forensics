"""Independent verification of a signed report.

Four checks, each reported on its own line so a reader can see exactly which
property holds:

1. **Signature** - the detached signature is valid for these exact bytes under
   the public key embedded in the report.
2. **Fingerprint matches genesis** - that public key's fingerprint is the one
   recorded in the ledger's genesis entry, so the report was signed by the key
   the chain was started with.
3. **Chain integrity** - the ledger excerpt carried inside the report links and
   hashes correctly on its own terms.
4. **Blob availability** - the params and result blobs the excerpt references
   are present, when the store is reachable. Not applicable otherwise.

What this does not prove
------------------------
An embedded public key only proves *internal consistency*: that whoever signed
this report held the private key whose public half is printed inside it. It says
nothing about who that was. Anyone can generate a key, sign a fabricated report,
and embed their own public key; every check here would pass.

A third party must compare the fingerprint against a value published
**out-of-band** - an organisation's key listing, a printed card, a prior
communication - before the signature means anything about identity. That caveat
is printed with every result rather than left for the reader to infer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from core.ledger.chain import GENESIS_OPERATION, GENESIS_PREV_HASH, entry_hash_of
from core.ledger.store import BlobStore, LedgerStore
from core.models import LedgerEntry, Signature
from core.report.sign import verify_signature

__all__ = [
    "CheckName",
    "ReportCheck",
    "ReportVerification",
    "verify_report_file",
    "verify_report",
    "IDENTITY_CAVEAT",
]

logger = structlog.get_logger(__name__)

IDENTITY_CAVEAT = (
    "An embedded public key proves internal consistency only. It does not "
    "prove identity: a third party must compare the fingerprint above against "
    "a value published out-of-band before treating this signature as evidence "
    "of who produced the report."
)


class CheckName(StrEnum):
    """The four independently reported checks."""

    SIGNATURE = "signature"
    FINGERPRINT_MATCHES_GENESIS = "fingerprint_matches_genesis"
    CHAIN_INTEGRITY = "chain_integrity"
    BLOBS_AVAILABLE = "blobs_available"


@dataclass(frozen=True)
class ReportCheck:
    """One check, its outcome, and a one-line reason."""

    name: CheckName
    passed: bool
    detail: str
    applicable: bool = True


@dataclass
class ReportVerification:
    """Every check for one report, plus the honesty caveat."""

    checks: list[ReportCheck] = field(default_factory=list)
    caveat: str = IDENTITY_CAVEAT
    fingerprint: str = ""

    @property
    def ok(self) -> bool:
        """True only when every applicable check passed."""
        return all(check.passed for check in self.checks if check.applicable)


def _check_signature(report: dict[str, Any]) -> ReportCheck:
    raw = report.get("signature")
    if not raw:
        return ReportCheck(
            CheckName.SIGNATURE,
            False,
            "the report carries no signature block, so nothing can be verified",
        )
    try:
        signature = Signature.model_validate(raw)
    except ValueError as exc:
        return ReportCheck(
            CheckName.SIGNATURE, False, f"signature block is malformed: {exc}"
        )
    if verify_signature(report, signature):
        return ReportCheck(
            CheckName.SIGNATURE,
            True,
            f"valid {signature.alg} signature by {signature.pubkey_fingerprint}",
        )
    return ReportCheck(
        CheckName.SIGNATURE,
        False,
        "signature does not match the report contents; the report was altered "
        "after signing, or signed by a different key",
    )


def _check_fingerprint(
    report: dict[str, Any], ledger_root: Path | None
) -> ReportCheck:
    raw = report.get("signature") or {}
    claimed = str(raw.get("pubkey_fingerprint") or "")
    if not claimed:
        return ReportCheck(
            CheckName.FINGERPRINT_MATCHES_GENESIS,
            False,
            "no fingerprint in the signature block to compare",
        )

    genesis_fingerprint = _genesis_fingerprint(report, ledger_root)
    if genesis_fingerprint is None:
        return ReportCheck(
            CheckName.FINGERPRINT_MATCHES_GENESIS,
            True,
            "no genesis entry was available to compare against",
            applicable=False,
        )
    if genesis_fingerprint == claimed:
        return ReportCheck(
            CheckName.FINGERPRINT_MATCHES_GENESIS,
            True,
            f"signing key {claimed} is the key recorded in the ledger genesis",
        )
    return ReportCheck(
        CheckName.FINGERPRINT_MATCHES_GENESIS,
        False,
        f"report was signed by {claimed}, but the ledger genesis records "
        f"{genesis_fingerprint}",
    )


def _genesis_fingerprint(
    report: dict[str, Any], ledger_root: Path | None
) -> str | None:
    """Fingerprint from the genesis entry, from the store or the excerpt."""
    entries = _excerpt(report)
    genesis = next(
        (e for e in entries if e.get("operation") == GENESIS_OPERATION), None
    )
    if genesis is None or ledger_root is None:
        return None
    blob = BlobStore(ledger_root).get(str(genesis.get("params_hash") or ""))
    if blob is None:
        return None
    try:
        params: dict[str, Any] = json.loads(blob)
    except ValueError:
        return None
    value = params.get("pubkey_fingerprint")
    return str(value) if value else None


def _excerpt(report: dict[str, Any]) -> list[dict[str, Any]]:
    sections = report.get("sections") or {}
    audit = sections.get("audit_trail") or {}
    entries = audit.get("entries") or []
    return [entry for entry in entries if isinstance(entry, dict)]


def _check_chain(report: dict[str, Any]) -> ReportCheck:
    raw_entries = _excerpt(report)
    if not raw_entries:
        return ReportCheck(
            CheckName.CHAIN_INTEGRITY,
            True,
            "the report embeds no ledger excerpt",
            applicable=False,
        )

    declared = str(
        (report.get("sections") or {}).get("audit_trail", {}).get("chain_status") or ""
    )
    if declared and declared != "VALID":
        return ReportCheck(
            CheckName.CHAIN_INTEGRITY,
            False,
            f"the report itself records the chain as {declared}",
        )

    previous: LedgerEntry | None = None
    for index, raw in enumerate(raw_entries):
        try:
            entry = LedgerEntry.model_validate(raw)
        except ValueError as exc:
            return ReportCheck(
                CheckName.CHAIN_INTEGRITY,
                False,
                f"excerpt entry {index} does not parse: {exc}",
            )
        record = raw | {"ts_utc": entry.ts_utc}
        if entry_hash_of(record) != entry.entry_hash:
            return ReportCheck(
                CheckName.CHAIN_INTEGRITY,
                False,
                f"entry {entry.seq} does not hash to its recorded entry_hash; "
                "its contents were altered",
            )
        expected_prev = (
            GENESIS_PREV_HASH if previous is None else previous.entry_hash
        )
        if previous is not None or entry.seq == 0:
            if entry.prev_entry_hash != expected_prev:
                return ReportCheck(
                    CheckName.CHAIN_INTEGRITY,
                    False,
                    f"entry {entry.seq} does not link to the entry before it",
                )
        previous = entry

    span = f"{raw_entries[0].get('seq')}..{raw_entries[-1].get('seq')}"
    return ReportCheck(
        CheckName.CHAIN_INTEGRITY,
        True,
        f"all {len(raw_entries)} excerpt entries link and hash correctly ({span})",
    )


def _check_blobs(report: dict[str, Any], ledger_root: Path | None) -> ReportCheck:
    entries = _excerpt(report)
    if ledger_root is None or not LedgerStore(ledger_root).path.parent.exists():
        return ReportCheck(
            CheckName.BLOBS_AVAILABLE,
            True,
            "no blob store was reachable, so referenced content was not checked",
            applicable=False,
        )
    if not entries:
        return ReportCheck(
            CheckName.BLOBS_AVAILABLE,
            True,
            "the report embeds no ledger excerpt",
            applicable=False,
        )

    blobs = BlobStore(ledger_root)
    missing: list[str] = []
    for entry in entries:
        for key in ("params_hash", "result_hash"):
            digest = str(entry.get(key) or "")
            if digest and not blobs.has(digest):
                missing.append(f"{digest[:12]} ({key} of seq {entry.get('seq')})")
    if missing:
        return ReportCheck(
            CheckName.BLOBS_AVAILABLE,
            False,
            f"{len(missing)} referenced blob(s) missing: {', '.join(missing[:5])}",
        )
    return ReportCheck(
        CheckName.BLOBS_AVAILABLE,
        True,
        f"every blob referenced by {len(entries)} entries is present",
    )


def verify_report(
    report: dict[str, Any], *, ledger_root: Path | str | None = None
) -> ReportVerification:
    """Run all four checks over an already-loaded report."""
    root = Path(ledger_root) if ledger_root is not None else None
    signature = report.get("signature") or {}
    return ReportVerification(
        checks=[
            _check_signature(report),
            _check_fingerprint(report, root),
            _check_chain(report),
            _check_blobs(report, root),
        ],
        fingerprint=str(signature.get("pubkey_fingerprint") or ""),
    )


def verify_report_file(
    path: Path | str, *, ledger_root: Path | str | None = None
) -> ReportVerification:
    """Load a ``.forensic.json`` report and verify it.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ValueError: the file is not valid JSON.
    """
    report_path = Path(path)
    raw = report_path.read_bytes()
    loaded: dict[str, Any] = json.loads(raw)
    return verify_report(loaded, ledger_root=ledger_root)
