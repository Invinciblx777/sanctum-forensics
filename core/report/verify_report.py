"""Independent verification of a signed report.

Given the rendered bytes, a detached signature and a public key, confirm the
report is authentic and unmodified. Deferred to M5.
"""

from __future__ import annotations

__all__ = ["verify_report"]


def verify_report(
    payload: bytes, signature: str, *, public_key_pem: bytes
) -> bool:
    """Return ``True`` if valid; raise :class:`SignatureInvalid` if not."""
    raise NotImplementedError
