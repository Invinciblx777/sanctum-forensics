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
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog

from api.jobs import JobRegistry

__all__ = [
    "AppServices",
    "HelperTransport",
    "default_services",
    "signing_key_limitations",
]

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

    ``call_stream`` is what the job routes use for anything long. It yields one
    progress record at a time and returns the operation's result, and closing it
    cancels the operation - which is why a wipe's progress bar moves while the
    wipe is running and why the Cancel button reaches the engine.
    """

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    def call_stream(
        self, method: str, params: dict[str, Any]
    ) -> Generator[dict[str, Any], None, dict[str, Any]]: ...


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
        """Acquired images. Read-only input to everything downstream."""
        return self.state_dir / "evidence"

    @property
    def recovered_dir(self) -> Path:
        """Objects carved out of evidence. Derived output, never input.

        Separate from :attr:`evidence_dir` deliberately, and not merely for
        tidiness: evidence is what the tool reads and must not modify, and
        recovered objects are what it writes. Confining a carve's ``out_dir``
        to the evidence directory would let a recovery job write files into the
        tree holding the image it is reading, which is the one place a forensic
        tool must never create anything.
        """
        return self.state_dir / "recovered"

    def prepare(self) -> None:
        """Create the directories the API writes into."""
        for directory in (
            self.state_dir,
            self.ledger_root,
            self.reports_dir,
            self.evidence_dir,
            self.recovered_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def signing_key_limitations(services: AppServices) -> list[str]:
    """What ``/health`` says about the report check that ties a key to a chain.

    ``fingerprint_matches_genesis`` compares a report's signing key with the key
    the chain recorded at genesis. A chain started before any key existed
    records none, and the check is SKIP - not PASS - on every report that chain
    will ever carry. Nothing else surfaces that: the job routes deliberately do
    not mint a key, and the SKIP is only visible to someone reading check lines.

    Two states, because they have different remedies. Neither creates a key.
    """
    from core.ledger.chain import NO_SIGNING_KEY, genesis_fingerprint
    from core.report.sign import key_file_for

    key_dir = services.key_dir or (services.state_dir / "keys")
    recorded = genesis_fingerprint(services.ledger_root)
    if recorded == NO_SIGNING_KEY:
        return [
            "CHAIN_WITHOUT_KEY_FINGERPRINT: the ledger chain at "
            f"{services.ledger_root} was started before any signing key "
            "existed, so its genesis records no fingerprint and "
            "fingerprint_matches_genesis is SKIP, not PASS, on every report it "
            "carries. Creating a key now does not repair this chain. To tie "
            "reports to their chain, archive this ledger directory and start a "
            "new chain after the key exists."
        ]
    if recorded is None and not key_file_for(key_dir).is_file():
        return [
            f"NO_SIGNING_KEY: no report signing key exists under {key_dir} and "
            "no ledger chain has been started. The first job will start the "
            "chain with no key fingerprint in its genesis, and "
            "fingerprint_matches_genesis will then be SKIP on every report that "
            "chain carries. Create the key before the first job: "
            ".venv/bin/python scripts/hardware_validation.py keygen "
            f"--key-dir {key_dir}"
        ]
    return []


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
