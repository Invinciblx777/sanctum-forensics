"""Acceptance: every stub raises NotImplementedError. None returns None silently.

When a milestone implements a module, its lines move out of this file into that
module's own tests. **Every module in the project is now implemented**, so what
remains here is the static guard: no function in ``core/`` may have a body that
is a bare ``pass`` or a bare ``return``, silently answering None to a question
it did not actually resolve.

The dynamic half of this file is gone because there are no stubs left to call.
That is the intended end state, not an omission: helper/ is covered by
tests/helper/, and api/ by tests/api/.
"""

from __future__ import annotations

import inspect
from pathlib import Path

#: Modules whose milestone has landed. Their behaviour is covered by their own
#: tests (tests/device/, tests/erase/), so the stub gate below no longer applies.
#: core.erase.drive is listed because its Linux-only import guard makes it
#: unimportable here; tests/erase/ covers it on Linux.
IMPLEMENTED = (
    "core.device",
    "core.erase.calibrate",
    "core.erase.patterns",
    "core.erase.verify",
    "core.erase.drive",
    "core.ledger",
    "core.report",
    "core.carve.evidence",
    "core.carve.acquire",
    "core.carve.signature",
    "core.carve.structure",
    "core.carve.fragmentation",
    "core.carve.validate",
    "core.carve.score",
    "core.carve.classify",
    "core.carve.fsaware",
    "core.erase.files",
    # Covered by tests/erase/files/test_free_space_gates.py and, on udisks loop
    # volumes, test_free_space_wipe_carve.py.
    "core.erase.freespace",
    "core.erase.inspect",
    "core.erase.metadata",
    "core.erase.residual",
    "core.erase.sink",
    "core.erase._platform",
)


def test_no_core_public_function_returns_none_silently() -> None:
    """Static guard: no core stub body is a bare ``pass`` or bare ``return``."""
    core_root = Path(__file__).resolve().parent.parent / "core"
    offenders: list[str] = []
    for py in core_root.rglob("*.py"):
        dotted = "core." + str(
            py.relative_to(core_root).with_suffix("")
        ).replace("\\", ".").replace("/", ".")
        if dotted.startswith(IMPLEMENTED):
            continue
        src = py.read_text(encoding="utf-8")
        if "raise NotImplementedError" not in src and "def " in src:
            # models.py / errors.py legitimately have no stubs
            if py.name not in {"models.py", "errors.py", "__init__.py"}:
                offenders.append(str(py))
    assert not offenders, f"modules with functions but no stub raise: {offenders}"


def test_inspect_finds_no_pass_only_bodies() -> None:
    """core stubs must not silently return; body must raise."""
    import core

    bad: list[str] = []
    pkg_root = Path(core.__file__).resolve().parent
    for py in pkg_root.rglob("*.py"):
        if py.name in {"__init__.py", "models.py", "errors.py"}:
            continue
        mod_name = "core." + str(py.relative_to(pkg_root).with_suffix("")).replace(
            "\\", "."
        ).replace("/", ".")
        if mod_name.startswith(IMPLEMENTED):
            continue
        mod = __import__(mod_name, fromlist=["*"])
        for _, obj in inspect.getmembers(mod):
            if inspect.isfunction(obj) and obj.__module__ == mod_name:
                src = inspect.getsource(obj)
                if "raise NotImplementedError" not in src:
                    bad.append(f"{mod_name}.{obj.__name__}")
    assert not bad, f"stub functions that do not raise NotImplementedError: {bad}"
