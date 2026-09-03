"""Hash-chain construction and verification for the audit ledger.

Entry N binds to entry N-1: ``entry.prev_entry_hash`` is the SHA-256 of the
canonical serialization of entry N-1, and ``entry.entry_hash`` is the SHA-256 of
entry N including that link. Deferred to M4.
"""

from __future__ import annotations

from core.models import LedgerEntry

__all__ = ["compute_entry_hash", "make_entry", "verify_chain"]


def compute_entry_hash(entry: LedgerEntry) -> str:
    """Return the SHA-256 hex digest of ``entry``'s canonical serialization."""
    raise NotImplementedError


def make_entry(
    *,
    seq: int,
    actor: str,
    operation: str,
    params_hash: str,
    result_hash: str,
    prev_entry_hash: str,
) -> LedgerEntry:
    """Build the next ledger entry, filling timestamps and ``entry_hash``."""
    raise NotImplementedError


def verify_chain(entries: list[LedgerEntry]) -> None:
    """Raise :class:`LedgerChainBroken` at the first broken link, else return."""
    raise NotImplementedError
