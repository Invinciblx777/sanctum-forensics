"""The PyInstaller spec collects what static analysis cannot see.

reportlab.graphics.barcode imports its symbologies through exec() of a string.
A build that missed them failed every certificate PDF in the packaged app
(found by scripts/package_smoke.py --isolated on 2026-09-25), while the source
tree - where the modules are simply importable - never noticed.

core.carve.signature.SIGNATURE_DB_PATH is computed from __file__, which
resolves correctly in source (repo root, testkit/signatures.yaml exists) and
in the frozen app (sys._MEIPASS, where it does not: the spec's own excludes
list names "testkit"). Every /jobs/carve request in every packaged build -
Windows, confirmed directly against an installed exe, 2026-09-27; the same
__file__ arithmetic applies identically on Linux and macOS - failed with
EvidenceIntegrityError("signature table not found: ...") before this data
file was added. package_smoke.py never exercises /jobs/carve, so no CI run
or prior packaged-checks claim ever caught it.
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


def test_the_carve_engines_signature_table_is_collected() -> None:
    text = SPEC.read_text()
    assert '"testkit" / "signatures.yaml"' in text
    assert 'datas.append((str(SIGNATURES), "testkit"))' in text


def test_signature_db_path_is_exactly_where_the_spec_bundles_it() -> None:
    from core.carve.signature import SIGNATURE_DB_PATH

    repo_root = Path(__file__).resolve().parents[1]
    assert SIGNATURE_DB_PATH == repo_root / "testkit" / "signatures.yaml"
