"""api.routes package.

One router module per resource is added here in later milestones
(devices, erase, carve, report). :func:`all_routers` is the single point the
app factory calls to mount them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

__all__ = ["all_routers"]


def all_routers() -> list[APIRouter]:
    """Return every router to mount on the application."""
    raise NotImplementedError
