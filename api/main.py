"""FastAPI application factory.

The API layer is unprivileged and non-blocking: every long operation is streamed
from a background job (see :mod:`api.jobs` and :mod:`api.sse`), never run inline
in a request handler. Core layers are called through the helper daemon.

Run with: ``uvicorn api.main:create_app --factory``. Deferred to M6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["create_app"]


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    raise NotImplementedError
