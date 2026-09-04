"""Root-privileged helper daemon: the privilege boundary of Sanctum Forensics.

Privilege boundary
------------------
Everything that needs ``CAP_SYS_RAWIO`` or raw device access (ATA/NVMe
pass-through, HPA/DCO probing, block-device reads for acquisition) runs *here*,
as root, and nowhere else. The API and the UI run unprivileged and talk to this
daemon over a Unix domain socket. **The browser process never holds raw access
to anything.**

The trust rules are deliberately narrow:

* The socket lives at ``/run/sanctum/helper.sock`` with mode ``0600``, owned by
  root. Only root, or an explicitly configured operator uid, may connect.
* Every connection is authenticated with ``SO_PEERCRED``. A uid that does not
  match the configured operator uid is dropped **before any request is read**,
  so an unauthorised peer never reaches the parser.
* Requests are JSON-RPC. The ``method`` field is looked up in
  :data:`OPERATIONS`, a static allowlist mapping a name to a typed handler. A
  method name that is not in the allowlist is rejected. **A shell string is
  never accepted from the caller and the daemon never spawns a shell.**
* Handlers take structured parameters only and return structured results.
* An error crossing the socket carries a message and a remediation, never a
  traceback: a traceback from a root process tells an unprivileged caller about
  the filesystem layout and the code path it took to fail.

Destructive operations keep both of their gates on this side of the boundary.
``run_erase`` refuses unless ``dry_run`` is explicitly false *and* the typed
serial matches the device the helper itself re-reads. The API cannot talk the
helper out of either check, which is the point of putting them here rather than
in the request handler.
"""

from __future__ import annotations

import errno
import os
import socket
import struct
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from core.errors import SanctumError

from helper import rpc

__all__ = [
    "SOCKET_PATH",
    "SOCKET_MODE",
    "OPERATIONS",
    "HelperDaemon",
    "HelperClient",
    "InProcessHelper",
]

logger = structlog.get_logger(__name__)

SOCKET_PATH = "/run/sanctum/helper.sock"
SOCKET_MODE = 0o600

#: Seconds a peer may take to send one complete frame before it is dropped.
READ_TIMEOUT_SECONDS = 30

Handler = Callable[[dict[str, Any]], dict[str, Any]]


# --------------------------------------------------------------------------
# Handlers. Structured parameters in, structured results out.
# --------------------------------------------------------------------------


def _op_enumerate_devices(params: dict[str, Any]) -> dict[str, Any]:
    """List host block devices with their capability and hidden-area reports.

    One round trip rather than three per device: the UI's device list needs all
    three, and a per-device probe would mean N+1 socket calls for a screen that
    refreshes.
    """
    from core.device import capabilities, hidden_areas
    from core.device.enumerate import enumerate_devices

    include_virtual = bool(params.get("include_virtual", False))
    found: list[dict[str, Any]] = []
    for device in enumerate_devices(include_virtual=include_virtual):
        entry: dict[str, Any] = {"device": device.model_dump(mode="json")}
        try:
            probed = capabilities.probe(device)
            entry["capabilities"] = probed.model_dump(mode="json")
        except SanctumError as exc:
            entry["capabilities"] = None
            entry["capability_error"] = exc.message
        try:
            hidden = hidden_areas.detect_hidden_areas(device)
            entry["hidden_areas"] = hidden.model_dump(mode="json")
        except SanctumError as exc:
            entry["hidden_areas"] = None
            entry["hidden_area_error"] = exc.message
        found.append(entry)
    return {"devices": found}


def _op_probe_capabilities(params: dict[str, Any]) -> dict[str, Any]:
    """Probe sanitization capability for one device."""
    from core.device import capabilities
    from core.device.enumerate import get_device

    device = get_device(str(params["path"]))
    return {
        "device": device.model_dump(mode="json"),
        "capabilities": capabilities.probe(device).model_dump(mode="json"),
    }


def _op_detect_hidden_areas(params: dict[str, Any]) -> dict[str, Any]:
    """Probe HPA/DCO for one device."""
    from core.device import hidden_areas
    from core.device.enumerate import get_device

    device = get_device(str(params["path"]))
    return {
        "hidden_areas": hidden_areas.detect_hidden_areas(device).model_dump(mode="json")
    }


def _op_run_erase(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a sanitization job. Both gates are enforced here, not upstream.

    ``dry_run`` defaults to True when the key is absent. An API that forgot to
    forward the flag therefore simulates rather than wipes, which is the
    failure direction that costs nothing.
    """
    from core.device import capabilities
    from core.device.enumerate import get_device
    from core.erase.drive import ChainLedgerSink, execute
    from core.errors import ConfirmationMismatch
    from core.ledger.chain import Ledger
    from core.models import EraseJob, SanitizationLevel

    device = get_device(str(params["path"]))
    dry_run = bool(params.get("dry_run", True))
    typed_serial = str(params.get("typed_serial") or "")

    if not dry_run and typed_serial != device.serial:
        # Re-checked against the serial this process just read from the host,
        # not against whatever the caller believed. A stale UI cannot authorise
        # a wipe of a device that was swapped since the page loaded.
        raise ConfirmationMismatch(
            f"The typed serial {typed_serial!r} does not match {device.path}, "
            f"whose serial is {device.serial!r}. Nothing was erased."
        )

    ledger = Ledger(
        Path(str(params["ledger_root"])),
        tool_version=str(params.get("tool_version", "sanctum-forensics/0.0.0")),
        pubkey_fingerprint=str(params.get("pubkey_fingerprint", "")),
    )
    job = EraseJob(
        job_id=str(params["job_id"]),
        device=device,
        level=SanitizationLevel(str(params.get("level", "CLEAR"))),
        dry_run=dry_run,
        confirmed_serial=typed_serial or device.serial,
        method=None,
    )
    generator = execute(
        job,
        capabilities.probe(device),
        ledger=ChainLedgerSink(ledger),
    )
    progress: list[dict[str, Any]] = []
    try:
        while True:
            progress.append(next(generator).model_dump(mode="json"))
    except StopIteration as stop:
        result = stop.value
    return {
        "result": result.model_dump(mode="json"),
        "progress": progress,
    }


def _op_acquire_image(params: dict[str, Any]) -> dict[str, Any]:
    """Read-only acquisition of a device or image to a destination file."""
    from core.carve.acquire import AcquireOptions, acquire
    from core.ledger.chain import Ledger

    ledger_root = params.get("ledger_root")
    ledger = (
        Ledger(
            Path(str(ledger_root)),
            tool_version=str(params.get("tool_version", "sanctum-forensics/0.0.0")),
            pubkey_fingerprint=str(params.get("pubkey_fingerprint", "")),
        )
        if ledger_root
        else None
    )
    options = AcquireOptions(
        compression=str(params.get("compression", "fast")),  # type: ignore[arg-type]
        operator=str(params.get("operator", "sanctum")),
    )
    generator = acquire(
        Path(str(params["source"])),
        Path(str(params["dest"])),
        fmt=str(params.get("fmt", "raw")),  # type: ignore[arg-type]
        options=options,
        ledger=ledger,
        job_id=str(params.get("job_id", "acquire")),
    )
    progress: list[dict[str, Any]] = []
    try:
        while True:
            progress.append(next(generator).model_dump(mode="json"))
    except StopIteration as stop:
        record = stop.value
    return {"record": record.model_dump(mode="json"), "progress": progress}


#: Static allowlist. The only operations the daemon will ever perform.
OPERATIONS: dict[str, Handler] = {
    "enumerate_devices": _op_enumerate_devices,
    "probe_capabilities": _op_probe_capabilities,
    "detect_hidden_areas": _op_detect_hidden_areas,
    "run_erase": _op_run_erase,
    "acquire_image": _op_acquire_image,
}


# --------------------------------------------------------------------------
# Daemon
# --------------------------------------------------------------------------


class HelperDaemon:
    """Serves :data:`OPERATIONS` over the Unix socket."""

    def __init__(self, *, operator_uid: int, socket_path: str = SOCKET_PATH) -> None:
        self.operator_uid: int = operator_uid
        self.socket_path: str = socket_path
        self._server: socket.socket | None = None
        self._stop = False

    # -- lifecycle ---------------------------------------------------------

    def bind(self) -> socket.socket:
        """Create the socket at mode 0600, replacing any stale one."""
        path = Path(self.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        # Mode set after bind: the umask applies during bind, so a permissive
        # umask would otherwise leave a world-writable socket for the window
        # between bind and chmod.
        os.chmod(self.socket_path, SOCKET_MODE)
        server.listen(8)
        self._server = server
        logger.info(
            "helper_listening",
            socket=self.socket_path,
            operator_uid=self.operator_uid,
            mode=oct(SOCKET_MODE),
        )
        return server

    def serve_forever(self) -> None:
        """Bind the socket and dispatch requests until stopped."""
        server = self._server or self.bind()
        self._stop = False
        while not self._stop:
            try:
                conn, _ = server.accept()
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise
            with conn:
                self.serve_connection(conn)

    def stop(self) -> None:
        """Ask :meth:`serve_forever` to return after the current connection."""
        self._stop = True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        Path(self.socket_path).unlink(missing_ok=True)

    # -- per connection ----------------------------------------------------

    def serve_connection(self, conn: socket.socket) -> None:
        """Authenticate the peer, then serve frames until it disconnects."""
        try:
            self._authenticate_peer(conn)
        except PermissionError as exc:
            logger.warning("helper_peer_rejected", reason=str(exc))
            return

        conn.settimeout(READ_TIMEOUT_SECONDS)
        buffer = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except (TimeoutError, OSError):
                return
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > rpc.MAX_FRAME_BYTES:
                logger.warning("helper_frame_too_large", size=len(buffer))
                return
            while b"\n" in buffer:
                frame, buffer = buffer.split(b"\n", 1)
                if not frame.strip():
                    continue
                conn.sendall(self.handle_frame(frame))

    def handle_frame(self, frame: bytes) -> bytes:
        """Decode one request, dispatch it, and encode the response."""
        try:
            req_id, method, params = rpc.decode_request(frame)
        except ValueError as exc:
            return rpc.encode_response(
                0, error=str(exc), remediation="Send a well-formed JSON-RPC request."
            )
        try:
            result = self._dispatch(method, params)
        except SanctumError as exc:
            # The remediation crosses the boundary verbatim. The operator reads
            # the sentence the library author wrote, not one the API invented.
            logger.info("helper_operation_refused", method=method, error=exc.message)
            return rpc.encode_response(
                req_id,
                error=exc.message,
                remediation=exc.remediation,
                kind=type(exc).__name__,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("helper_operation_failed", method=method, error=str(exc))
            return rpc.encode_response(
                req_id,
                error=f"{method} failed: {exc}",
                remediation="Check the helper log for the failing operation.",
                kind=type(exc).__name__,
            )
        return rpc.encode_response(req_id, result=result)

    def _authenticate_peer(self, conn: socket.socket) -> int:
        """Return the peer uid via ``SO_PEERCRED``; raise if it is not allowed.

        Called before a single byte of request is read. An unauthorised peer
        never reaches the parser, so a parser bug is not reachable by anyone
        who could not already run as the operator.
        """
        if sys.platform != "linux":  # pragma: no cover - Linux-only daemon
            raise PermissionError(
                "SO_PEERCRED peer authentication is Linux-only; the helper "
                "refuses to serve without it rather than serve unauthenticated."
            )
        raw = conn.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        _pid, uid, _gid = struct.unpack("3i", raw)
        if uid not in (0, self.operator_uid):
            raise PermissionError(
                f"peer uid {uid} is neither root nor the operator uid "
                f"{self.operator_uid}"
            )
        return int(uid)

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Look ``method`` up in :data:`OPERATIONS` and call its handler."""
        handler = OPERATIONS.get(method)
        if handler is None:
            raise KeyError(
                f"{method!r} is not an allowed helper operation. Allowed: "
                f"{', '.join(sorted(OPERATIONS))}"
            )
        logger.info("helper_dispatch", method=method)
        return handler(params)


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------


class HelperClient:
    """Talks to a :class:`HelperDaemon` over its Unix socket."""

    def __init__(self, socket_path: str = SOCKET_PATH) -> None:
        self.socket_path = socket_path
        self._next_id = 0

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one request and return its result.

        Raises:
            rpc.RpcError: the helper refused, with its remediation attached.
            OSError: the socket could not be reached.
        """
        self._next_id += 1
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(READ_TIMEOUT_SECONDS)
        try:
            conn.connect(self.socket_path)
            conn.sendall(rpc.encode_request(method, params, req_id=self._next_id))
            buffer = b""
            while b"\n" not in buffer:
                chunk = conn.recv(65536)
                if not chunk:
                    raise OSError("the helper closed the connection without replying")
                buffer += chunk
                if len(buffer) > rpc.MAX_FRAME_BYTES:
                    raise OSError("the helper sent an oversized response frame")
        finally:
            conn.close()
        return rpc.decode_response(buffer.split(b"\n", 1)[0])


class InProcessHelper:
    """A helper that dispatches in this process instead of over a socket.

    For development and for the test suite, where standing up a root daemon is
    neither possible nor desirable. It runs the **same** :data:`OPERATIONS`
    table through the **same** :meth:`HelperDaemon._dispatch`, so an operation
    reachable here is reachable there and the allowlist cannot diverge between
    the two paths.

    It grants no privilege. Whatever the calling process could already do is
    all it can do, so on an unprivileged box the device operations fail exactly
    as they would through the socket.
    """

    def __init__(self) -> None:
        self._daemon = HelperDaemon(operator_uid=os.getuid())

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch through the allowlist, converting errors the way RPC does."""
        try:
            return self._daemon._dispatch(method, params)
        except SanctumError as exc:
            raise rpc.RpcError(
                exc.message, remediation=exc.remediation, kind=type(exc).__name__
            ) from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise rpc.RpcError(
                f"{method} failed: {exc}",
                remediation="Check the server log for the failing operation.",
                kind=type(exc).__name__,
            ) from exc
