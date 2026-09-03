"""JSON-RPC 2.0 framing for the helper socket.

Transport-agnostic encode/decode helpers shared by the daemon and its clients.
No I/O here. Deferred to M1.
"""

from __future__ import annotations

from typing import Any

__all__ = ["encode_request", "decode_request", "encode_response", "decode_response"]


def encode_request(method: str, params: dict[str, Any], *, req_id: int) -> bytes:
    """Serialize a JSON-RPC request frame."""
    raise NotImplementedError


def decode_request(frame: bytes) -> tuple[int, str, dict[str, Any]]:
    """Parse a request frame into ``(req_id, method, params)``."""
    raise NotImplementedError


def encode_response(
    req_id: int, *, result: dict[str, Any] | None = None, error: str | None = None
) -> bytes:
    """Serialize a JSON-RPC response frame (exactly one of result/error)."""
    raise NotImplementedError


def decode_response(frame: bytes) -> dict[str, Any]:
    """Parse a response frame, raising on a JSON-RPC error object."""
    raise NotImplementedError
