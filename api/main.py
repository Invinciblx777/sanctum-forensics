"""FastAPI application factory.

The API layer is unprivileged and non-blocking. Every long operation is run on
a worker thread by :mod:`api.jobs` and streamed by :mod:`api.sse`, never
executed inline in a request handler: a four-terabyte overwrite is hours long
and an HTTP request that lived that long would be dead well before the work was.

Two deployment properties, both enforced here rather than documented and hoped
for:

**It binds 127.0.0.1 and nothing else.** :func:`run` passes the loopback
address explicitly. An erase console reachable from the network is a remote
wipe primitive, and no authentication scheme this project could ship would make
that a good trade.

**It serves the UI from disk with no outbound requests.** The built bundle is
mounted as static files and every asset it needs is inside it. There is no CDN
link, no external font, no analytics beacon and no telemetry, so the whole
thing works with the ethernet unplugged - which is the state a forensic
workstation should be in and the state the venue may put it in regardless.

Run with ``python -m api.main`` or
``uvicorn api.main:create_app --factory --host 127.0.0.1``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.deps import AppServices, default_services, signing_key_limitations
from api.routes import all_routers

__all__ = ["create_app", "run", "UI_DIST", "LOOPBACK_HOST"]

logger = structlog.get_logger(__name__)

#: The only address this application is ever served on.
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

#: Where ``npm run build`` puts the bundle.
UI_DIST = Path(__file__).resolve().parents[1] / "ui" / "dist"

#: Sent on every response. `default-src 'self'` is the one that matters: it
#: makes an accidental CDN link fail loudly in the browser console rather than
#: silently work on the developer's machine and break at the venue.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def create_app(
    *,
    services: AppServices | None = None,
    state_dir: Path | None = None,
    serve_ui: bool = True,
) -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="Sanctum Forensics",
        version="0.0.0",
        description=(
            "Local-only control surface for secure sanitization and forensic "
            "recovery. Binds 127.0.0.1; every privileged operation goes through "
            "the helper daemon."
        ),
        # No external docs assets: FastAPI's default Swagger UI is loaded from
        # a CDN, which is exactly what must not happen here.
        docs_url=None,
        redoc_url=None,
    )
    app.state.services = services or default_services(state_dir=state_dir)

    for router in all_routers():
        app.include_router(router)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # A traceback never reaches the client. It would describe this host's
        # filesystem layout to whatever is on the other end of the socket.
        logger.warning(
            "api_unhandled", path=request.url.path, error=str(exc),
            kind=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(exc).__name__}: {exc}",
                "kind": type(exc).__name__,
                "remediation": "Check the API log for the failing request.",
            },
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        state: AppServices = app.state.services
        return {
            "status": "ok",
            "tool_version": state.tool_version,
            "state_dir": str(state.state_dir),
            "ui_bundled": UI_DIST.is_dir(),
            # Computed per request: the key and the chain can both come into
            # existence after startup, and the second one is permanent.
            "limitations": state.limitations + signing_key_limitations(state),
        }

    if serve_ui and UI_DIST.is_dir():
        assets = UI_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(UI_DIST / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            """Serve a bundled file, falling back to the SPA entry point.

            The fallback is what lets a deep link survive a reload: the router
            is client-side, so ``/recovery`` is not a file and must return the
            app shell rather than a 404.
            """
            candidate = (UI_DIST / path).resolve()
            if (
                candidate.is_file()
                and UI_DIST.resolve() in candidate.parents
            ):
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")

    logger.info("api_ready", ui_bundled=UI_DIST.is_dir())
    return app


def run() -> None:  # pragma: no cover - the process entry point
    """Serve on loopback only."""
    import uvicorn

    port = int(os.environ.get("SANCTUM_PORT", DEFAULT_PORT))
    uvicorn.run(
        create_app(),
        host=LOOPBACK_HOST,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    run()
