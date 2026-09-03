"""Detached signing of rendered reports.

Produces a detached signature over the exact rendered bytes so a third party can
verify the report independently. Deferred to M5.
"""

from __future__ import annotations

__all__ = ["sign_payload", "public_key_fingerprint"]


def sign_payload(payload: bytes, *, private_key_pem: bytes) -> str:
    """Return a detached signature (base64) over ``payload``."""
    raise NotImplementedError


def public_key_fingerprint(public_key_pem: bytes) -> str:
    """Return a stable fingerprint string for the given public key."""
    raise NotImplementedError
