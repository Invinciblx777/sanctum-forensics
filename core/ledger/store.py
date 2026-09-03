"""Append-only persistence for the audit ledger.

Two stores, deliberately separate:

:class:`BlobStore`
    Content-addressed. Full operation parameters and results are large and
    variable, so they live here under their own SHA-256 and the chain holds only
    the digest. That keeps every ledger entry a fixed shape while still proving
    exactly what was recorded: change a blob and its hash stops matching the
    entry that names it.

:class:`LedgerStore`
    The chain itself, one canonical JSON object per line. Opened
    ``O_APPEND | O_CREAT | O_WRONLY``, written in a single ``os.write``, then
    ``fsync``ed; the containing directory is ``fsync``ed when the file is
    created. Nothing here seeks, truncates or rewrites - any code path that
    opens the chain for writing at an offset is a bug.

**Crash is not tampering.** A process killed mid-append leaves a partial final
line. :meth:`LedgerStore.read` reports that as ``incomplete_tail`` and keeps the
complete lines, so verification can say "the chain is intact and the last write
did not finish" instead of accusing someone of forgery. Confusing the two in a
forensic tool is a credibility problem, so they are separate states everywhere.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from core.ledger._filelock import file_lock

__all__ = ["BlobStore", "LedgerStore", "ChainRead", "CHAIN_FILENAME"]

logger = structlog.get_logger(__name__)

CHAIN_FILENAME = "chain.jsonl"
_LOCK_FILENAME = ".chain.lock"
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_NEWLINE = b"\n"
#: Windows opens in text mode by default, which would translate every "\n" the
#: chain writes into "\r\n" and change the bytes that were hashed.
_BINARY = getattr(os, "O_BINARY", 0)


def _require_hex(digest: str) -> str:
    if not _HEX64.match(digest):
        raise ValueError(
            f"{digest!r} is not a 64-character lowercase hex SHA-256 digest"
        )
    return digest


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry so a newly created file survives a power cut."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # Windows cannot open a directory this way; nothing to flush.
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class BlobStore:
    """Content-addressed store for operation params and results."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.blobs = self.root / "blobs"

    def _path_for(self, digest: str) -> Path:
        return self.blobs / digest[:2] / digest

    def put(self, payload: bytes) -> str:
        """Store ``payload`` and return its SHA-256 hex digest. Idempotent."""
        digest = hashlib.sha256(payload).hexdigest()
        target = self._path_for(digest)
        if target.exists():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{digest}.partial")
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _BINARY, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(staging, target)
        _fsync_dir(target.parent)
        return digest

    def get(self, digest: str) -> bytes | None:
        """Return the blob for ``digest``, or ``None`` if it is not stored."""
        path = self._path_for(_require_hex(digest))
        try:
            return path.read_bytes()
        except OSError:
            return None

    def has(self, digest: str) -> bool:
        """Whether ``digest`` is present."""
        return self._path_for(_require_hex(digest)).exists()


@dataclass(frozen=True)
class ChainRead:
    """What one read of the chain file found."""

    lines: list[bytes]
    incomplete_tail: bool
    partial_tail: bytes | None = None


class LedgerStore:
    """The append-only chain file."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.directory = self.root / "ledger"
        self.path = self.directory / CHAIN_FILENAME
        self.lock_path = self.directory / _LOCK_FILENAME

    def read(self) -> ChainRead:
        """Read every complete line, reporting a partial final line separately."""
        try:
            raw = self.path.read_bytes()
        except OSError:
            return ChainRead(lines=[], incomplete_tail=False)
        if not raw:
            return ChainRead(lines=[], incomplete_tail=False)

        complete, partial = raw, b""
        if not raw.endswith(_NEWLINE):
            cut = raw.rfind(_NEWLINE)
            if cut == -1:
                complete, partial = b"", raw
            else:
                complete, partial = raw[: cut + 1], raw[cut + 1 :]

        lines = complete.split(_NEWLINE)[:-1] if complete else []
        return ChainRead(
            lines=lines,
            incomplete_tail=bool(partial),
            partial_tail=partial or None,
        )

    def append(self, payload: bytes) -> None:
        """Append one canonical line. Refuses if the previous write did not finish.

        Raises:
            ValueError: ``payload`` contains a newline, which would split one
                logical entry across two lines.
            RuntimeError: The chain ends in a partial line. Appending past it
                would bury evidence of the interrupted write.
        """
        if _NEWLINE in payload:
            raise ValueError(
                "a ledger payload may not contain a newline; canonical JSON "
                "escapes them, so this indicates a caller bypassed canon"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        with file_lock(self.lock_path):
            existing = self.read()
            if existing.incomplete_tail:
                raise RuntimeError(
                    f"{self.path} ends in an incomplete line of "
                    f"{len(existing.partial_tail or b'')} bytes, left by a write "
                    "that did not finish. Refusing to append past it. "
                    "Recover by archiving the truncated file for the record, "
                    "then starting a new chain; do not edit it in place."
                )
            is_new = not self.path.exists()
            fd = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY | _BINARY,
                0o600,
            )
            try:
                os.write(fd, payload + _NEWLINE)
                os.fsync(fd)
            finally:
                os.close(fd)
            if is_new:
                _fsync_dir(self.directory)
        logger.debug("ledger_line_appended", path=str(self.path), size=len(payload))
