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

Streaming, and liveness
-----------------------
``run_erase`` and ``acquire_image`` are hours long. They are served as *streams*
(:data:`STREAMING_OPERATIONS`): the daemon writes one progress frame per record
the engine yields and one terminal frame at the end, so the API sees a wipe
move while it is moving rather than after it has stopped. See :mod:`helper.rpc`
for the frames.

There is no wall-clock deadline on an operation, because there is no honest one
to pick: a full overwrite of a 7.76 GB stick is minutes and a three-pass
overwrite of a 4 TB disk is most of a day, and any fixed number that admits the
second is useless against the first. What replaces it is a **liveness** rule,
and it is worth being exact about what that rule can and cannot see:

* A **progress** frame proves the engine moved. The client's deadline is reset
  by it, so an operation that is merely slow is never failed.
* A **heartbeat** frame (every :data:`HEARTBEAT_SECONDS`) proves this process is
  alive and this socket is writable, and proves *nothing* about the engine. It
  exists because whole phases legitimately yield nothing - a sampled verify of
  a multi-terabyte disk is quiet for minutes - and a client that failed those
  would be the 30-second bug again with a larger number. Every heartbeat
  carries ``since_progress_seconds`` so a stall is **reported** rather than
  guessed at.
* Therefore: a client detects a **dead or wedged helper process**, a closed
  socket, and a crashed daemon. It does **not** detect an engine blocked in an
  uninterruptible kernel call while this process still schedules - that shows
  up as ``since_progress_seconds`` climbing without bound, which is a fact on
  the operator's screen and not a verdict the tool invents.

Cancellation is cooperative and travels on the connection the operation is
running on: the client sends a cancel frame, the daemon notices it between
progress records, and closes the engine's generator at its next yield. Nothing
kills a thread mid-write. What a cancelled erase leaves behind is written to
the ledger by :func:`core.erase.drive.execute` itself, in the same entry that
says the device is now partially sanitized.
"""

from __future__ import annotations

import errno
import os
import select
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog
from core.errors import SanctumError

from helper import rpc

__all__ = [
    "SOCKET_PATH",
    "SOCKET_MODE",
    "OPERATIONS",
    "STREAMING_OPERATIONS",
    "HelperDaemon",
    "HelperClient",
    "InProcessHelper",
]

logger = structlog.get_logger(__name__)

SOCKET_PATH = "/run/sanctum/helper.sock"
SOCKET_MODE = 0o600

#: Seconds a peer may take to send one complete request frame before it is
#: dropped. This is a deadline on an *idle* connection, not on an operation:
#: once a streaming operation is under way the daemon is writing, not reading,
#: and this no longer applies.
READ_TIMEOUT_SECONDS = 30

#: Seconds a client waits for *any* frame - progress or heartbeat - before it
#: declares the helper dead. It is deliberately many multiples of
#: :data:`HEARTBEAT_SECONDS`, so a scheduling hiccup on a loaded box cannot
#: fail a running wipe; only a helper that has stopped speaking can.
CLIENT_IDLE_TIMEOUT_SECONDS = 120.0

#: Seconds a client waits to reach the socket at all. Connecting is either
#: immediate or hopeless, so this stays short.
CONNECT_TIMEOUT_SECONDS = 10.0

#: Seconds between heartbeat frames during a streaming operation. Read the
#: module docstring for what a heartbeat asserts - it is less than it looks.
HEARTBEAT_SECONDS = 5.0

Handler = Callable[[dict[str, Any]], dict[str, Any]]

#: Request fields naming a path the helper may write to or read from. Each is
#: resolved and confined to the daemon's state directory before any handler
#: sees it; see :meth:`HelperDaemon.apply_policy`.
_CONFINED_PATH_PARAMS = ("ledger_root", "dest")

#: A streaming handler yields one JSON-ready progress record at a time and
#: returns the same result dict its batch counterpart returns.
StreamHandler = Callable[
    [dict[str, Any]], Generator[dict[str, Any], None, dict[str, Any]]
]


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


def _owner_uid(params: dict[str, Any]) -> int | None:
    """The operator uid :meth:`HelperDaemon.apply_policy` stamped on a request.

    Absent when the daemon is unconfined - the in-process helper, and the test
    suite - where the writing process is the operator already and there is
    nothing to hand over.
    """
    value = params.get("owner_uid")
    return int(value) if isinstance(value, int) else None


def _drain(
    generator: Generator[dict[str, Any], None, dict[str, Any]],
) -> dict[str, Any]:
    """Run a streaming handler to completion and batch its progress.

    The batch entry in :data:`OPERATIONS` is the streaming handler driven by
    this, rather than a second implementation, so the two cannot disagree about
    what an operation does. It is what a caller that does not stream gets.

    **It is not what the socket serves.** A long wipe yields hundreds of
    thousands of records, and a single reply carrying all of them exceeds
    :data:`helper.rpc.MAX_FRAME_BYTES`; the socket path streams and never
    builds this list.
    """
    progress: list[dict[str, Any]] = []
    while True:
        try:
            progress.append(next(generator))
        except StopIteration as stop:
            answer: dict[str, Any] = stop.value
            return {**answer, "progress": progress}


def _op_run_erase(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a sanitization job, returning its progress in one batch."""
    return _drain(_stream_run_erase(params))


def _stream_run_erase(
    params: dict[str, Any],
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    """Execute a sanitization job. Both gates are enforced here, not upstream.

    ``dry_run`` defaults to True when the key is absent. An API that forgot to
    forward the flag therefore simulates rather than wipes, which is the
    failure direction that costs nothing.

    Progress is *yielded*, not collected: the caller is a socket that wants to
    write each record as the engine produces it. Closing this generator asks the
    engine to stop at its next yield; what that leaves on the device is recorded
    by :func:`core.erase.drive.execute` before the exception propagates.
    """
    from core.device import capabilities, guard
    from core.device.enumerate import get_device
    from core.erase.drive import ChainLedgerSink, execute
    from core.ledger.chain import Ledger
    from core.models import EraseJob, SanitizationLevel

    device = get_device(str(params["path"]))
    dry_run = bool(params.get("dry_run", True))
    typed_serial = str(params.get("typed_serial") or "")

    if not dry_run:
        # Re-checked against the device this process just read from the host,
        # not against whatever the caller believed. A stale UI cannot authorise
        # a wipe of a device that was swapped since the page loaded.
        #
        # The check is delegated rather than restated. A second copy of the rule
        # lived here and required an exact serial match, so it rejected the
        # /dev/disk/by-id path that `assert_serial_confirmed` accepts for a
        # device reporting no serial - and accepted the empty string, which then
        # failed downstream naming a different gate. A serial-less stick could
        # not be erased through the product at all. One implementation, called
        # from both sides of the boundary, cannot drift like that.
        guard.assert_serial_confirmed(device, typed_serial)

    ledger = Ledger(
        Path(str(params["ledger_root"])),
        tool_version=str(params.get("tool_version", "sanctum-forensics/0.0.0")),
        pubkey_fingerprint=str(params.get("pubkey_fingerprint", "")),
        # Set by HelperDaemon.apply_policy, never by the caller. Without it a
        # root-run wipe leaves 0600 root-owned blobs in a chain the
        # unprivileged API has to read back to issue the certificate.
        owner_uid=_owner_uid(params),
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
    while True:
        try:
            record = next(generator)
        except StopIteration as stop:
            return {"result": stop.value.model_dump(mode="json")}
        yield record.model_dump(mode="json")


def _op_acquire_image(params: dict[str, Any]) -> dict[str, Any]:
    """Read-only acquisition, returning its progress in one batch."""
    return _drain(_stream_acquire_image(params))


def _stream_acquire_image(
    params: dict[str, Any],
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    """Read-only acquisition of a device or image to a destination file."""
    from core.carve.acquire import AcquireOptions, acquire
    from core.ledger.chain import Ledger

    ledger_root = params.get("ledger_root")
    ledger = (
        Ledger(
            Path(str(ledger_root)),
            tool_version=str(params.get("tool_version", "sanctum-forensics/0.0.0")),
            pubkey_fingerprint=str(params.get("pubkey_fingerprint", "")),
            owner_uid=_owner_uid(params),
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
    while True:
        try:
            record = next(generator)
        except StopIteration as stop:
            return {"record": stop.value.model_dump(mode="json")}
        yield record.model_dump(mode="json")


#: Static allowlist. The only operations the daemon will ever perform.
OPERATIONS: dict[str, Handler] = {
    "enumerate_devices": _op_enumerate_devices,
    "probe_capabilities": _op_probe_capabilities,
    "detect_hidden_areas": _op_detect_hidden_areas,
    "run_erase": _op_run_erase,
    "acquire_image": _op_acquire_image,
}

#: The subset of :data:`OPERATIONS` served incrementally. A name here must also
#: be in ``OPERATIONS``; the allowlist is one list, not two, and
#: ``test_the_operation_allowlist_is_closed`` holds that.
STREAMING_OPERATIONS: dict[str, StreamHandler] = {
    "run_erase": _stream_run_erase,
    "acquire_image": _stream_acquire_image,
}


# --------------------------------------------------------------------------
# Daemon
# --------------------------------------------------------------------------


class HelperDaemon:
    """Serves :data:`OPERATIONS` over the Unix socket.

    ``state_dir`` is the one directory tree this daemon will write into. It is
    fixed when the daemon starts and never comes from a request, which is the
    whole of its value: a request body naming ``ledger_root`` or ``dest`` can
    ask a **root** process to create files, and without a confinement fixed
    out-of-band that is an arbitrary-write primitive with no authentication in
    front of it. With it, the worst a caller can name is a path inside the
    directory the operator chose when they typed the sudo command.

    ``None`` means unconfined, and is for the in-process helper only - there the
    "daemon" holds no privilege the caller did not already have, so there is
    nothing to confine.
    """

    def __init__(
        self,
        *,
        operator_uid: int,
        socket_path: str = SOCKET_PATH,
        state_dir: Path | str | None = None,
    ) -> None:
        self.operator_uid: int = operator_uid
        self.socket_path: str = socket_path
        self.state_dir: Path | None = Path(state_dir).resolve() if state_dir else None
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
                if self._serve_stream_frame(conn, frame):
                    # A streamed operation owns the connection for its whole
                    # duration and consumes whatever the client sent during it,
                    # so anything still buffered here is stale by definition.
                    buffer = b""
                    continue
                try:
                    conn.sendall(self.handle_frame(frame))
                except OSError as exc:
                    # The peer left before reading its answer. That ends this
                    # connection and nothing else: an unhandled EPIPE here
                    # propagated out of serve_forever and took the whole daemon
                    # down, so any client that hung up could stop the helper.
                    logger.info("helper_reply_undelivered", error=str(exc))
                    return

    def _streaming_method(self, frame: bytes) -> tuple[int, str, dict[str, Any]] | None:
        """``(req_id, method, params)`` when ``frame`` asks for a stream."""
        try:
            req_id, method, params = rpc.decode_request(frame)
        except ValueError:
            return None
        if method not in STREAMING_OPERATIONS:
            return None
        return req_id, method, params

    def _serve_stream_frame(self, conn: socket.socket, frame: bytes) -> bool:
        """Serve ``frame`` as a stream if it names one. True when it did."""
        request = self._streaming_method(frame)
        if request is None:
            return False
        req_id, method, params = request
        logger.info("helper_dispatch", method=method, stream=True)
        self._serve_stream(conn, req_id, method, params)
        return True

    def _serve_stream(
        self, conn: socket.socket, req_id: int, method: str, params: dict[str, Any]
    ) -> None:
        """Run a streaming operation, writing one frame per record produced.

        Three things end it: the engine finishes (terminal result frame), the
        engine raises (terminal error frame), or the client asks to stop - by
        sending a cancel frame or by going away. A client that went away is
        treated exactly like one that cancelled, because from the device's point
        of view they are the same event and continuing to wipe for a caller that
        will never read the answer is not a service to anyone.
        """
        send_lock = threading.Lock()
        last_progress = time.monotonic()
        stop_beating = threading.Event()

        def send(payload: bytes) -> None:
            with send_lock:
                conn.sendall(payload)

        def beat() -> None:
            while not stop_beating.wait(HEARTBEAT_SECONDS):
                try:
                    send(
                        rpc.encode_heartbeat(
                            req_id,
                            {
                                "method": method,
                                "since_progress_seconds": round(
                                    time.monotonic() - last_progress, 1
                                ),
                            },
                        )
                    )
                except OSError:
                    return

        try:
            allowed = self.apply_policy(params)
        except (SanctumError, OSError, ValueError, KeyError, TypeError) as exc:
            # A refused path is an answer, not a reason to stop serving. An
            # exception escaping here would propagate out of serve_forever and
            # take the root helper down for every other operator on the box.
            self._send_quietly(
                lambda payload: conn.sendall(payload),
                self._error_frame(req_id, method, exc),
            )
            return

        generator = STREAMING_OPERATIONS[method](allowed)
        heartbeat = threading.Thread(
            target=beat, name=f"helper-heartbeat-{req_id}", daemon=True
        )
        heartbeat.start()
        try:
            while True:
                try:
                    record = next(generator)
                except StopIteration as stop:
                    self._send_quietly(
                        send, rpc.encode_response(req_id, result=stop.value)
                    )
                    return
                except (SanctumError, OSError, ValueError, KeyError, TypeError) as exc:
                    self._send_quietly(send, self._error_frame(req_id, method, exc))
                    return
                last_progress = time.monotonic()
                try:
                    send(rpc.encode_progress(req_id, record))
                except OSError:
                    self._stop_engine(generator, method, reason="client disconnected")
                    return
                if self._peer_asked_to_stop(conn):
                    self._stop_engine(generator, method, reason="client cancelled")
                    self._send_quietly(
                        send,
                        rpc.encode_response(
                            req_id,
                            error=f"{method} was cancelled by the operator.",
                            remediation=(
                                "The device is partially processed. The ledger "
                                "entry for this job records what was done and "
                                "what was not."
                            ),
                            kind="Cancelled",
                        ),
                    )
                    return
        finally:
            stop_beating.set()
            heartbeat.join(timeout=HEARTBEAT_SECONDS)

    @staticmethod
    def _send_quietly(send: Callable[[bytes], None], payload: bytes) -> None:
        """Write a terminal frame, tolerating a peer that already left."""
        try:
            send(payload)
        except OSError as exc:
            logger.info("helper_terminal_frame_undelivered", error=str(exc))

    @staticmethod
    def _stop_engine(
        generator: Generator[dict[str, Any], None, dict[str, Any]],
        method: str,
        *,
        reason: str,
    ) -> None:
        """Ask the engine to stop at its next yield, never mid-write."""
        logger.info("helper_stream_stopping", method=method, reason=reason)
        try:
            generator.close()
        except (SanctumError, OSError, ValueError, RuntimeError) as exc:
            logger.warning("helper_stream_stop_failed", method=method, error=str(exc))

    @staticmethod
    def _peer_asked_to_stop(conn: socket.socket) -> bool:
        """True when the client sent a cancel frame or closed the connection.

        Polled with a zero timeout between progress records, so it costs one
        ``select`` per record and never blocks a running erase.
        """
        try:
            readable, _, _ = select.select([conn], [], [], 0)
        except OSError:
            return True
        if not readable:
            return False
        try:
            chunk = conn.recv(65536)
        except (TimeoutError, BlockingIOError):
            return False
        except OSError:
            return True
        if not chunk:
            return True
        return any(
            rpc.is_cancel_frame(part)
            for part in chunk.split(b"\n")
            if part.strip()
        )

    @staticmethod
    def _error_frame(req_id: int, method: str, exc: BaseException) -> bytes:
        """One failure, framed the same way whatever produced it."""
        if isinstance(exc, SanctumError):
            # The remediation crosses the boundary verbatim. The operator reads
            # the sentence the library author wrote, not one the API invented.
            logger.info("helper_operation_refused", method=method, error=exc.message)
            return rpc.encode_response(
                req_id,
                error=exc.message,
                remediation=exc.remediation,
                kind=type(exc).__name__,
            )
        logger.warning("helper_operation_failed", method=method, error=str(exc))
        return rpc.encode_response(
            req_id,
            error=f"{method} failed: {exc}",
            remediation="Check the helper log for the failing operation.",
            kind=type(exc).__name__,
        )

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
        except (SanctumError, OSError, ValueError, KeyError, TypeError) as exc:
            return self._error_frame(req_id, method, exc)
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
        return handler(self.apply_policy(params))

    # -- confinement and ownership ----------------------------------------

    def apply_policy(self, params: dict[str, Any]) -> dict[str, Any]:
        """Confine every path in ``params`` and say who owns what gets written.

        Applied here rather than inside the handlers, and applied on **both**
        dispatch paths, so there is one place that decides what a request is
        allowed to touch. A handler cannot forget to call it, and a handler
        added later inherits it.

        Two changes are made to the request:

        * every path parameter is resolved and checked against
          :attr:`state_dir`; anything landing outside it is refused before the
          handler runs, so a caller cannot aim a root process at ``/etc``;
        * ``owner_uid`` is added, so the ledger hands the operator the files it
          creates. It is taken from ``--operator-uid`` and is **not** readable
          from the request: a caller cannot ask for a file to be given to
          somebody else.
        """
        base = self.state_dir
        if base is None:
            return params
        confined = dict(params)
        for field in _CONFINED_PATH_PARAMS:
            value = confined.get(field)
            if value is None or value == "":
                continue
            confined[field] = str(self._confine(base, str(value), field))
        confined["owner_uid"] = self.operator_uid
        return confined

    @staticmethod
    def _confine(base: Path, candidate: str, field: str) -> Path:
        """Resolve ``candidate`` and refuse it unless it is inside ``base``.

        Symlinks are resolved *before* the comparison, so a link planted inside
        the state directory cannot point a root write out of it. That is the
        check that makes this a boundary rather than a string-prefix test.
        """
        requested = Path(candidate)
        target = (base / requested if not requested.is_absolute() else requested)
        resolved = target.resolve()
        if resolved != base and base not in resolved.parents:
            raise PermissionError(
                f"{field}={candidate!r} resolves to {resolved}, which is outside "
                f"the helper's state directory {base}. The helper runs as root "
                "and will not write outside the directory it was started with."
            )
        return resolved


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------


class HelperClient:
    """Talks to a :class:`HelperDaemon` over its Unix socket.

    The read deadline is on **silence, not on completion**. An operation may
    run for hours; what the client refuses to wait through is a helper that has
    stopped speaking, which it learns from the absence of any frame - progress
    or heartbeat - for :data:`CLIENT_IDLE_TIMEOUT_SECONDS`. The module docstring
    states exactly what that does and does not detect.
    """

    def __init__(
        self,
        socket_path: str = SOCKET_PATH,
        *,
        idle_timeout: float = CLIENT_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self.socket_path = socket_path
        self.idle_timeout = idle_timeout
        self._next_id = 0

    def _connect(self) -> socket.socket:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(CONNECT_TIMEOUT_SECONDS)
        try:
            conn.connect(self.socket_path)
        except OSError:
            conn.close()
            raise
        conn.settimeout(self.idle_timeout)
        return conn

    @staticmethod
    def _read_frame(conn: socket.socket, buffer: bytes) -> tuple[bytes, bytes]:
        """Return the next complete frame and whatever followed it."""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                raise OSError("the helper closed the connection without replying")
            buffer += chunk
            if len(buffer) > rpc.MAX_FRAME_BYTES:
                raise OSError("the helper sent an oversized response frame")
        frame, rest = buffer.split(b"\n", 1)
        return frame, rest

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one request and return its result, discarding any progress.

        Raises:
            rpc.RpcError: the helper refused, with its remediation attached.
            OSError: the socket could not be reached.
        """
        generator = self.call_stream(method, params)
        while True:
            try:
                next(generator)
            except StopIteration as stop:
                answer: dict[str, Any] = stop.value
                return answer

    def call_stream(
        self, method: str, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        """Send one request, yield each progress record, return the result.

        Closing this generator sends a cancel frame on the same connection the
        operation is running on and then drops the socket, so a helper that
        misses the frame still learns from the broken pipe. Either way the
        engine stops at its next yield rather than mid-write.

        Raises:
            rpc.RpcError: the helper refused, with its remediation attached.
            TimeoutError: no frame of any kind arrived within the idle deadline.
            OSError: the socket could not be reached.
        """
        self._next_id += 1
        req_id = self._next_id
        conn = self._connect()
        try:
            conn.sendall(rpc.encode_request(method, params, req_id=req_id))
            buffer = b""
            while True:
                try:
                    frame, buffer = self._read_frame(conn, buffer)
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"the helper sent no frame for {self.idle_timeout:g}s "
                        f"during {method}; it is not slow, it has stopped "
                        "speaking. Whatever it was doing to the device may "
                        "still be running: check the helper log and the "
                        "ledger before doing anything else."
                    ) from exc
                kind, payload = rpc.decode_frame(frame)
                if kind == "heartbeat":
                    continue
                if kind == "progress":
                    yield payload
                    continue
                return payload
        except GeneratorExit:
            try:
                conn.sendall(rpc.encode_cancel(req_id))
            except OSError:
                pass
            raise
        finally:
            conn.close()


@contextmanager
def _rpc_errors(method: str) -> Iterator[None]:
    """Convert core exceptions to :class:`rpc.RpcError`, as the socket does.

    So a caller cannot tell the two transports apart by the exception type it
    has to catch, which is the only way the in-process path can stay a faithful
    stand-in for the daemon.
    """
    try:
        yield
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
        with _rpc_errors(method):
            return self._daemon._dispatch(method, params)

    def call_stream(
        self, method: str, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        """Stream an operation in this process, yielding as the engine yields.

        Streaming is not simulated here: a method in
        :data:`STREAMING_OPERATIONS` is driven record by record, so the
        in-process configuration shows a wipe moving exactly as the socket one
        does, and closing this generator stops the engine the same way. A method
        that is not streamable produces no records and returns its one result.
        """
        handler = STREAMING_OPERATIONS.get(method)
        if handler is None:
            return self.call(method, params)
        with _rpc_errors(method):
            generator = handler(params)
            try:
                while True:
                    try:
                        record = next(generator)
                    except StopIteration as stop:
                        answer: dict[str, Any] = stop.value
                        return answer
                    yield record
            finally:
                # Explicit, for the same reason api/routes/jobs.py closes this
                # generator explicitly: on cancellation this is what reaches the
                # engine, and leaving it to the collector would mean an erase
                # kept writing until the frame happened to be freed.
                generator.close()
