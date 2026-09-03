"""Render a :class:`ForensicReport` to a distributable document.

Where a guarantee could not be made, the rendered report says so explicitly.
No marketing language. Deferred to M5.
"""

from __future__ import annotations

from core.models import ForensicReport

__all__ = ["render_report"]


def render_report(report: ForensicReport, *, fmt: str = "pdf") -> bytes:
    """Render ``report`` to ``fmt`` (``"pdf"`` or ``"html"``) and return the bytes."""
    raise NotImplementedError
