"""Acceptance: every stub raises NotImplementedError. None returns None silently.

When a milestone implements a module, its lines move out of this file into that
module's own tests.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from core.carve import (
    acquire,
    classify,
    fsaware,
    score,
    signature,
    structure,
    validate,
)
from core.erase import files
from core.models import Device, EraseJob
from helper import daemon, rpc

#: Modules whose milestone has landed. Their behaviour is covered by their own
#: tests (tests/device/, tests/erase/), so the stub gate below no longer applies.
#: core.erase.drive is listed because its Linux-only import guard makes it
#: unimportable here; tests/erase/ covers it on Linux.
IMPLEMENTED = (
    "core.device",
    "core.erase.patterns",
    "core.erase.verify",
    "core.erase.drive",
    "core.ledger",
    "core.report",
)


def _thunks(
    device: Device, job: EraseJob, candidate: object, entry: object
) -> dict[str, Callable[[], object]]:
    img = object()  # ReadableImage is a Protocol; a stub never inspects it
    return {
        "erase.erase_paths": lambda: files.erase_paths(["/tmp/x"], job_id="j"),
        "carve.open_readonly": lambda: acquire.open_readonly("/dev/sdz"),
        "carve.acquire_image": lambda: acquire.acquire_image("/dev/sdz", "/tmp/e.E01"),
        "carve.undelete": lambda: fsaware.undelete(img),  # type: ignore[arg-type]
        "carve.carve_signatures": lambda: signature.carve_signatures(
            img  # type: ignore[arg-type]
        ),
        "carve.carve_structures": lambda: structure.carve_structures(
            img  # type: ignore[arg-type]
        ),
        "carve.validate_candidate": lambda: validate.validate_candidate(
            candidate, img  # type: ignore[arg-type]
        ),
        "carve.score_candidate": lambda: score.score_candidate(
            candidate  # type: ignore[arg-type]
        ),
        "carve.classify_candidate": lambda: classify.classify_candidate(
            candidate  # type: ignore[arg-type]
        ),
        "helper.rpc.encode_request": lambda: rpc.encode_request("m", {}, req_id=1),
        "helper.rpc.decode_request": lambda: rpc.decode_request(b"{}"),
        "helper.rpc.encode_response": lambda: rpc.encode_response(1, result={}),
        "helper.rpc.decode_response": lambda: rpc.decode_response(b"{}"),
    }


def _drain(value: object) -> object:
    """Force a generator stub to execute its body."""
    if isinstance(value, Iterator):
        return next(value)
    return value


def test_every_core_stub_raises_not_implemented(
    sample_device: Device,
    sample_erase_job: EraseJob,
    sample_candidate: object,
    sample_ledger_entry: object,
) -> None:
    thunks = _thunks(
        sample_device, sample_erase_job, sample_candidate, sample_ledger_entry
    )
    for name, thunk in thunks.items():
        try:
            _drain(thunk())
        except NotImplementedError:
            continue
        raise AssertionError(f"{name} did not raise NotImplementedError")


def test_helper_daemon_methods_raise() -> None:
    d = daemon.HelperDaemon(operator_uid=0)
    with pytest.raises(NotImplementedError):
        d.serve_forever()
    with pytest.raises(NotImplementedError):
        d._dispatch("enumerate_devices", {})


def test_helper_operation_handlers_raise() -> None:
    assert set(daemon.OPERATIONS) == {
        "enumerate_devices",
        "probe_capabilities",
        "detect_hidden_areas",
        "run_erase",
        "acquire_image",
    }
    for handler in daemon.OPERATIONS.values():
        with pytest.raises(NotImplementedError):
            handler({})


def test_api_factory_raises() -> None:
    from api.main import create_app
    from api.routes import all_routers

    with pytest.raises(NotImplementedError):
        create_app()
    with pytest.raises(NotImplementedError):
        all_routers()


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
