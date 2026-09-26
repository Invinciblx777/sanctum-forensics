"""The PyInstaller spec collects what static analysis cannot see.

reportlab.graphics.barcode imports its symbologies through exec() of a string.
A build that missed them failed every certificate PDF in the packaged app
(found by scripts/package_smoke.py --isolated on 2026-09-25), while the source
tree - where the modules are simply importable - never noticed.
"""

from __future__ import annotations

import importlib
from pathlib import Path

SPEC = Path(__file__).resolve().parents[1] / "packaging" / "sanctum.spec"


def test_the_barcode_symbologies_are_collected() -> None:
    assert 'collect_submodules("reportlab.graphics.barcode")' in SPEC.read_text()


def test_the_qr_widget_the_certificate_draws_is_importable_here() -> None:
    importlib.import_module("reportlab.graphics.barcode.code128")
    qr = importlib.import_module("reportlab.graphics.barcode.qr")
    assert hasattr(qr, "QrCodeWidget")
