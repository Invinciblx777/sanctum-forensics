"""Append-only persistence for ledger entries.

The store only appends and reads. It never rewrites or deletes an entry.
Verification of the chain lives in :mod:`core.ledger.chain`. Deferred to M4.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.models import LedgerEntry

__all__ = ["LedgerStore", "open_store"]


class LedgerStore:
    """Append-only handle over a ledger backing file."""

    def __init__(self, path: str) -> None:
        self.path: str = path

    def append(self, entry: LedgerEntry) -> None:
        """Append ``entry`` to the store durably."""
        raise NotImplementedError

    def read_all(self) -> Iterator[LedgerEntry]:
        """Yield every entry in sequence order."""
        raise NotImplementedError

    def last(self) -> LedgerEntry | None:
        """Return the most recent entry, or ``None`` if the store is empty."""
        raise NotImplementedError


def open_store(path: str) -> LedgerStore:
    """Open (creating if absent) the ledger store at ``path``."""
    raise NotImplementedError
