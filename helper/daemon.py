"""Root-privileged helper daemon: the privilege boundary of Sanctum Forensics.

Privilege boundary
------------------
Everything that needs ``CAP_SYS_RAWIO`` or raw device access (ATA/NVMe pass-through,
HPA/DCO probing, block-device reads for acquisition) runs *here*, as root, and
nowhere else. The API and UI run unprivileged and talk to this daemon over a
Unix domain socket.

The trust rules are deliberately narrow:

* The socket lives at ``/run/sanctum/helper.sock`` with mode ``0600``, owned by
  root. Only root (or an explicitly configured operator uid) may connect.
* Every connection is authenticated with ``SO_PEERCRED``. A uid that does not
  match the configured operator uid is dropped before any request is read.
* Requests are JSON-RPC. The ``method`` field is looked up in ``OPERATIONS``,
  a static allowlist mapping a name to a typed handler. A method name that is
  not in the allowlist is rejected. **A shell string is never accepted from the
  caller and the daemon never spawns a shell.**
* Handlers take structured parameters only and return structured results.

Implementation deferred to M1. Handlers below are stubs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["SOCKET_PATH", "SOCKET_MODE", "OPERATIONS", "HelperDaemon"]

SOCKET_PATH = "/run/sanctum/helper.sock"
SOCKET_MODE = 0o600

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def _op_enumerate_devices(params: dict[str, Any]) -> dict[str, Any]:
    """Handler: list host block devices."""
    raise NotImplementedError


def _op_probe_capabilities(params: dict[str, Any]) -> dict[str, Any]:
    """Handler: probe sanitization capability for one device."""
    raise NotImplementedError


def _op_detect_hidden_areas(params: dict[str, Any]) -> dict[str, Any]:
    """Handler: probe HPA/DCO for one device."""
    raise NotImplementedError


def _op_run_erase(params: dict[str, Any]) -> dict[str, Any]:
    """Handler: execute a sanitization job (honours dry-run)."""
    raise NotImplementedError


def _op_acquire_image(params: dict[str, Any]) -> dict[str, Any]:
    """Handler: read-only acquisition of a device to an image file."""
    raise NotImplementedError


#: Static allowlist. The only operations the daemon will ever perform.
OPERATIONS: dict[str, Handler] = {
    "enumerate_devices": _op_enumerate_devices,
    "probe_capabilities": _op_probe_capabilities,
    "detect_hidden_areas": _op_detect_hidden_areas,
    "run_erase": _op_run_erase,
    "acquire_image": _op_acquire_image,
}


class HelperDaemon:
    """Serves :data:`OPERATIONS` over the Unix socket. Stub."""

    def __init__(self, *, operator_uid: int, socket_path: str = SOCKET_PATH) -> None:
        self.operator_uid: int = operator_uid
        self.socket_path: str = socket_path

    def serve_forever(self) -> None:
        """Bind the socket (mode 0600) and dispatch requests until stopped."""
        raise NotImplementedError

    def _authenticate_peer(self, conn_fd: int) -> int:
        """Return the peer uid via SO_PEERCRED; raise if it != operator_uid."""
        raise NotImplementedError

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Look ``method`` up in :data:`OPERATIONS` and call its handler."""
        raise NotImplementedError
