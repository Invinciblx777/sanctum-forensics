"""JSON-RPC 2.0 framing for the helper socket.

Transport-agnostic encode/decode. No I/O here, so the framing rules can be
tested without a socket and the daemon and its client cannot drift apart.

**Newline-delimited JSON, one frame per line.** A length prefix would be
marginally faster and would let a malformed length stall a reader forever; a
newline cannot, because a frame that never terminates simply never parses. The
encoders therefore reject any payload that would embed a raw newline, which
``json.dumps`` guarantees by escaping them.

Two rules the daemon depends on:

* **A request names a method, never a command.** ``method`` is a lookup key in
  a static allowlist. No shell string, no path to an executable, no argv ever
  crosses this boundary.
* **An error response carries a message, never a traceback.** A traceback from
  a root process tells an unprivileged caller about the filesystem layout and
  the code path it took to fail.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "encode_request",
    "decode_request",
    "encode_response",
    "decode_response",
    "MAX_FRAME_BYTES",
    "RpcError",
]

#: Longest frame the daemon will read. A caller that sends more is dropped
#: rather than allowed to exhaust memory in a root process.
MAX_FRAME_BYTES = 1 * 1024 * 1024

JSONRPC_VERSION = "2.0"


class RpcError(Exception):
    """A JSON-RPC error returned by the peer, raised on the client side."""

    def __init__(self, message: str, *, remediation: str = "", kind: str = "") -> None:
        super().__init__(message)
        self.message = message
        #: Carried through verbatim from the core exception, so the operator
        #: reads the same sentence the library author wrote.
        self.remediation = remediation
        #: The exception class name, so a caller can branch without parsing prose.
        self.kind = kind


def encode_request(method: str, params: dict[str, Any], *, req_id: int) -> bytes:
    """Serialize a JSON-RPC request frame."""
    if not isinstance(method, str) or not method:
        raise ValueError("an RPC method name must be a non-empty string")
    frame = json.dumps(
        {"jsonrpc": JSONRPC_VERSION, "id": req_id, "method": method, "params": params},
        separators=(",", ":"),
        sort_keys=True,
    )
    return frame.encode("utf-8") + b"\n"


def decode_request(frame: bytes) -> tuple[int, str, dict[str, Any]]:
    """Parse a request frame into ``(req_id, method, params)``.

    Raises:
        ValueError: the frame is not a well-formed JSON-RPC request.
    """
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError(f"request frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        payload = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise ValueError(f"request frame is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("a JSON-RPC request must be an object")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise ValueError(f"unsupported jsonrpc version: {payload.get('jsonrpc')!r}")

    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError("a JSON-RPC request must name a method")
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("params must be an object, never a positional array")
    req_id = payload.get("id")
    if not isinstance(req_id, int):
        raise ValueError("a JSON-RPC request must carry an integer id")
    return req_id, method, params


def encode_response(
    req_id: int,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    remediation: str = "",
    kind: str = "",
) -> bytes:
    """Serialize a JSON-RPC response frame (exactly one of result/error)."""
    if (result is None) == (error is None):
        raise ValueError("a response carries exactly one of result or error")
    if error is not None:
        body: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "error": {
                "code": -32000,
                "message": error,
                "data": {"remediation": remediation, "kind": kind},
            },
        }
    else:
        body = {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}
    text = json.dumps(body, separators=(",", ":"), sort_keys=True)
    return text.encode("utf-8") + b"\n"


def decode_response(frame: bytes) -> dict[str, Any]:
    """Parse a response frame, raising on a JSON-RPC error object.

    Raises:
        RpcError: the peer returned an error, with its remediation attached.
        ValueError: the frame is not a well-formed JSON-RPC response.
    """
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError(f"response frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        payload = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response frame is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("a JSON-RPC response must be an object")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise ValueError(f"unsupported jsonrpc version: {payload.get('jsonrpc')!r}")

    if "error" in payload:
        error = payload["error"] or {}
        data = error.get("data") or {}
        raise RpcError(
            str(error.get("message") or "the helper returned an unspecified error"),
            remediation=str(data.get("remediation") or ""),
            kind=str(data.get("kind") or ""),
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("a JSON-RPC result must be an object")
    return result
