"""The desktop entry point: one process, one window, loopback only.

What an installed Sanctum runs when the user opens it from the Start menu, the
Dock or an application launcher. It is the same API and the same UI bundle as
``python -m api.main``; what it adds is what a double-click needs and a
developer does not:

* **An ephemeral port.** The OS picks a free loopback port, so a second copy,
  another program on 8787, or a stale server cannot collide with this one.
* **A per-launch session token.** 32 random bytes, handed to the API in the
  environment and to the window in the one URL it opens. Every other process
  on the machine - another account, a page in some other browser tab - gets
  401. See :mod:`api.security`.
* **A window.** A native webview (WebView2 on Windows, WKWebView on macOS,
  Qt WebEngine on Linux) when ``pywebview`` is installed; otherwise the
  default browser, with the
  process staying alive until the user presses Quit in the sidebar.
* **Its own state directory** in the OS's per-user data location, never the
  directory the launcher happened to be started from.

It never asks for elevation. Nothing on Windows or macOS needs it, and on
Linux the privileged work stays in the separate helper daemon.
"""

from __future__ import annotations

import multiprocessing
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

__all__ = ["main", "free_loopback_port", "session_url", "wait_for_health"]

LOOPBACK = "127.0.0.1"
TITLE = "Sanctum"


def free_loopback_port() -> int:
    """A port the OS says is free on loopback right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def session_url(port: int, token: str) -> str:
    return f"http://{LOOPBACK}:{port}/session/{token}"


def wait_for_health(port: int, token: str, timeout_s: float = 30.0) -> bool:
    """Poll ``/health`` with the session cookie until it answers."""
    deadline = time.monotonic() + timeout_s
    request = urllib.request.Request(
        f"http://{LOOPBACK}:{port}/health",
        headers={"Cookie": f"sanctum_session={token}"},
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def _serve(port: int, token: str, stop: threading.Event) -> Any:
    import uvicorn

    from api.main import create_app

    app = create_app(session_token=token)
    app.state.quit_event = stop
    config = uvicorn.Config(app, host=LOOPBACK, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="sanctum-api", daemon=True)
    thread.start()
    return server


def _open_window(url: str, stop: threading.Event) -> None:
    """A native webview if one is bundled, otherwise the default browser.

    ``SANCTUM_URL_FILE`` writes the session URL to a file and opens nothing.
    It exists for the packaged smoke test, which has no desktop to open a
    window on; the file holds a token for this launch only, it is written
    with owner-only permissions, and the app still refuses every request that
    does not carry the cookie that URL sets.
    """
    url_file = os.environ.get("SANCTUM_URL_FILE", "")
    if url_file:
        target = Path(url_file)
        target.write_text(url, encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:  # pragma: no cover - filesystem without POSIX modes
            pass
        print(f"{TITLE} session URL written to {target}", file=sys.stderr)
        try:
            while not stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            stop.set()
        return
    if os.environ.get("SANCTUM_BROWSER") != "1":
        if sys.platform.startswith("linux"):
            # The `desktop` extra installs the Qt backend on Linux. Without
            # this, pywebview tries GTK first and prints a traceback when
            # PyGObject is absent before it falls back to Qt.
            os.environ.setdefault("PYWEBVIEW_GUI", "qt")
        try:
            import webview  # type: ignore[import-not-found,unused-ignore]

            webview.create_window(
                TITLE, url, width=1280, height=860, min_size=(960, 640)
            )
            # The window's own icon, for the title bar and the task switcher
            # where the platform takes it from the window (Qt and GTK). A
            # packaged Windows or macOS build carries its icon in the
            # executable instead (packaging/make_icons.py).
            icon = _window_icon()
            webview.start(icon=str(icon) if icon else None)
            stop.set()
            return
        except Exception as exc:  # noqa: BLE001 - any webview failure falls back
            print(
                f"Native window unavailable ({exc}); opening the browser.",
                file=sys.stderr,
            )
    webbrowser.open(url)
    print(
        f"{TITLE} is running at http://{LOOPBACK}:<port> for this session only. "
        "Use Quit in the sidebar, or press Ctrl+C here, to stop it.",
        file=sys.stderr,
    )
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        stop.set()


def _window_icon() -> Path | None:
    """The app icon the UI bundle ships (``ui/public/icon.png``), if built."""
    icon = Path(__file__).resolve().parents[1] / "ui" / "dist" / "icon.png"
    return icon if icon.is_file() else None


def _ensure_logging_has_somewhere_to_write(state_dir: Path) -> None:
    """Give ``sys.stdout``/``sys.stderr`` and structlog somewhere real to
    write when there is no console.

    A frozen windowed build (``console=False`` on Windows and macOS,
    ``packaging/sanctum.spec``) has no console. Opened the only way a real
    user opens it - a double-click, the Start menu, the Dock - Python leaves
    ``sys.stdout``/``sys.stderr`` as ``None``. structlog's builtin default
    logger factory falls back to ``structlog._output.stdout``, itself
    captured with ``from sys import stdout`` at *that* module's own import
    time, so reassigning ``sys.stdout`` after structlog has already been
    imported elsewhere does not reach it. The first structured log call
    anywhere then builds a ``PrintLogger(file=None)`` and crashes with
    ``TypeError: cannot create weak reference to 'NoneType' object`` before
    the server ever starts - reproduced on installed Windows hardware,
    2026-09-27. Configuring structlog explicitly, with a real file, avoids
    depending on when structlog happens to have been imported first.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(  # noqa: SIM115 - kept open for the launcher's lifetime
        log_dir / "launcher.log", "a", encoding="utf-8", buffering=1
    )

    if sys.stdout is None:
        sys.stdout = log_file
    if sys.stderr is None:
        sys.stderr = log_file

    import structlog

    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=log_file))


def main() -> int:
    """Start the API on a private port and open the window."""
    multiprocessing.freeze_support()
    from api.deps import default_state_dir

    _ensure_logging_has_somewhere_to_write(default_state_dir())
    token = secrets.token_urlsafe(32)
    port = free_loopback_port()
    os.environ["SANCTUM_SESSION_TOKEN"] = token
    os.environ["SANCTUM_LAUNCHER"] = "1"

    stop = threading.Event()
    server = _serve(port, token, stop)
    if not wait_for_health(port, token):
        print(f"{TITLE} did not start: the local API never answered.", file=sys.stderr)
        server.should_exit = True
        return 1
    _open_window(session_url(port, token), stop)
    server.should_exit = True
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
