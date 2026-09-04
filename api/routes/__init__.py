"""api.routes package.

One router module per resource. :func:`all_routers` is the single point the app
factory calls to mount them, so adding a resource means adding it here and
nowhere else.
"""

from __future__ import annotations

from fastapi import APIRouter

__all__ = ["all_routers"]


def all_routers() -> list[APIRouter]:
    """Return every router to mount on the application."""
    from api.routes import audit, devices, jobs

    return [devices.router, jobs.router, audit.router]
