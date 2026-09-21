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
* **A window.** A native webview (WebView2 on Windows, WKWebView on macOS)
  when ``pywebview`` is bundled; otherwise the default browser, with the
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
    """A native webview if one is bundled, otherwise the default browser."""
    if os.environ.get("SANCTUM_BROWSER") != "1":
        try:
            import webview  # type: ignore[import-not-found]

            webview.create_window(
                TITLE, url, width=1280, height=860, min_size=(960, 640)
            )
            webview.start()
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


def main() -> int:
    """Start the API on a private port and open the window."""
    multiprocessing.freeze_support()
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
