"""Shared dependencies and error translation for the routers.

The services object is stashed on ``app.state`` at creation and read back
through :func:`get_services`. That is what lets one process host two
independently configured apps, which the test suite needs and a module-level
singleton would prevent.

:func:`sanctum_error_response` is the reason the API can be thin. Every core
exception already carries a ``remediation`` written by whoever implemented the
operation, and this hands that sentence to the client **verbatim**. An API that
rewrote it would be inventing advice about a subsystem it does not implement,
and the operator would read the API author's guess instead of the library
author's instruction.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from api.deps import AppServices

__all__ = ["get_services", "sanctum_error_response", "STATUS_FOR_ERROR"]

#: HTTP status per core exception. A refusal is 409 rather than 400: the
#: request was well formed and the *state of the world* is what made it
#: unacceptable, which is what 409 means. A 400 would suggest the operator
#: mistyped something they can fix in the body.
STATUS_FOR_ERROR: dict[str, int] = {
    "ConfirmationMismatch": 409,
    "MountedRefused": 409,
    "SystemDiskRefused": 409,
    "DeviceFrozen": 409,
    "DeviceVanished": 410,
    "UnsupportedCapability": 422,
    "PlatformUnsupported": 501,
    "EvidenceIntegrityError": 422,
    "LedgerChainBroken": 500,
    "SignatureInvalid": 422,
    # The key is missing a passphrase or is unsafely permissioned. 503 rather
    # than 500: the server is fine, a precondition for this operation is not,
    # and the remediation names it.
    "KeyPassphraseMissing": 503,
    "KeyPermissionsUnsafe": 503,
}


def get_services(request: Request) -> AppServices:
    """The process's services, from ``app.state``."""
    services: AppServices = request.app.state.services
    return services


def sanctum_error_response(kind: str, message: str, remediation: str) -> HTTPException:
    """Turn a core or helper error into an HTTP error, remediation intact."""
    detail: dict[str, Any] = {
        "error": message,
        "kind": kind,
        # Verbatim. The operator reads the sentence the library author wrote.
        "remediation": remediation,
    }
    return HTTPException(status_code=STATUS_FOR_ERROR.get(kind, 400), detail=detail)
