"""Process-wide services the routers share.

One registry, one helper client, one ledger root, resolved once at startup and
handed to routers through FastAPI's dependency system. A module-level singleton
would work and would make the whole API untestable in a single process, because
two tests could not have two ledgers.

Everything privileged goes through :attr:`AppServices.helper`. A router never
imports :mod:`core.device` or :mod:`core.erase.drive` directly - that is the
whole point of the privilege boundary, and keeping the import out of the router
is what makes the boundary checkable by grep rather than by trust.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog

from api.jobs import JobRegistry

__all__ = ["AppServices", "HelperTransport", "default_services"]

logger = structlog.get_logger(__name__)

#: Where the ledger, reports and recovered objects live when the environment
#: does not say otherwise.
DEFAULT_STATE_DIR = Path(os.environ.get("SANCTUM_STATE_DIR", "")) or (
    Path.home() / ".local" / "share" / "sanctum"
)


class HelperTransport(Protocol):
    """Whatever reaches the privileged helper.

    A protocol rather than a concrete class so the socket client and the
    in-process dispatcher are interchangeable, and so a test can substitute a
    recorder without a running daemon. Both real implementations run the same
    allowlist, so a substitution cannot widen what the API can ask for.
    """

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class AppServices:
    """Everything a router needs, resolved once."""

    registry: JobRegistry
    helper: HelperTransport
    state_dir: Path
    tool_version: str = "sanctum-forensics/0.0.0"
    #: Overridden in tests so a report can be signed without a real keyring.
    key_dir: Path | None = None
    limitations: list[str] = field(default_factory=list)

    @property
    def ledger_root(self) -> Path:
        return self.state_dir / "ledger"

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"

    @property
    def evidence_dir(self) -> Path:
        return self.state_dir / "evidence"

    def prepare(self) -> None:
        """Create the directories the API writes into."""
        for directory in (
            self.state_dir,
            self.ledger_root,
            self.reports_dir,
            self.evidence_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def default_services(
    *,
    state_dir: Path | None = None,
    helper: HelperTransport | None = None,
) -> AppServices:
    """Build the services for a normally-configured process.

    The helper defaults to the in-process dispatcher rather than the socket
    client, because a developer box has no root daemon running and a
    connection refused at import time would make the whole API unstartable.
    Both run the same operation allowlist. Set ``SANCTUM_HELPER_SOCKET`` to use
    the real daemon.
    """
    from helper.daemon import HelperClient, InProcessHelper

    socket_path = os.environ.get("SANCTUM_HELPER_SOCKET", "")
    if helper is None:
        helper = HelperClient(socket_path) if socket_path else InProcessHelper()

    limitations: list[str] = []
    if not socket_path:
        limitations.append(
            "HELPER_IN_PROCESS: no helper socket was configured, so privileged "
            "operations run with this process's own privileges rather than "
            "through the root daemon. On an unprivileged process the device "
            "operations will fail; nothing is silently escalated."
        )

    services = AppServices(
        registry=JobRegistry(),
        helper=helper,
        state_dir=Path(state_dir) if state_dir else DEFAULT_STATE_DIR,
        limitations=limitations,
    )
    services.prepare()
    logger.info(
        "api_services_ready",
        state_dir=str(services.state_dir),
        helper=type(helper).__name__,
    )
    return services
