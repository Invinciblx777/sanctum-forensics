"""One simulation journey, discovery to certificate, on the real engine.

    .venv/bin/python scripts/demo_simulation.py [--json OUT.json] [--keep DIR]

Seven stages, each run through the application's own code, never a script
that prints what the code would have said:

    DISCOVER                two simulated media, one of them "mounted"
    PREFLIGHT               core.device.guard, the same gate the helper runs;
                            the mounted one is BLOCKED by core.workflow.derive
    PLAN                    core.erase.drive.select_method, from capability
    SIMULATED SANITIZATION  core.erase.drive.execute, a real overwrite
    SIMULATED VERIFICATION  the VERIFY phase of that same run, a real read-back
    FORENSIC REPORT         core.report.render.build_report, graded by
                            core.report.verify_report
    CERTIFICATE             Ed25519-signed JSON and PDF, then one byte of a copy
                            is changed and the verifier must reject it

**SIMULATION / NO PHYSICAL DEVICE MODIFIED.** The media are regular files this
script creates in its own temporary directory. There is no ``--device``
argument: the script cannot be pointed at a device, and it refuses to run the
engine unless the target is a regular file inside that directory. Two engine
calls are replaced because a regular file cannot answer them - the
``BLKGETSIZE64`` ioctl (the size is the file's size) and the sysfs serial
re-read (a file has no sysfs entry) - and the journey says so in its
limitations. The guard, the method selection, the overwrite, the read-back
verification, the ledger, the report builder, the signature and the verifier
are the production code, unmodified.

The signing key is generated for this run with a fixed demonstration passphrase
and deleted with the directory. A certificate signed by it proves the document
was not altered after signing; it does not identify anyone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import shutil
import sys
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import structlog

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from core.device import guard  # noqa: E402
from core.errors import SanctumError  # noqa: E402
from core.models import (  # noqa: E402
    Device,
    DeviceCapabilities,
    EraseJob,
    SanitizationLevel,
)
from core.workflow import WorkflowFacts  # noqa: E402
from core.workflow import derive as derive_workflow  # noqa: E402

BANNER = "SIMULATION / NO PHYSICAL DEVICE MODIFIED"
MIB = 1024 * 1024
MEDIUM_BYTES = 4 * MIB
SECTOR = 512
SEED = 26149
CASE_ID = "SIM-JOURNEY-001"
JOB_ID = "sim-erase-0001"
DEMO_PASSPHRASE = "simulation-only-demo-passphrase"
STAGES = (
    "DISCOVER",
    "PREFLIGHT",
    "PLAN",
    "SIMULATED SANITIZATION",
    "SIMULATED VERIFICATION",
    "FORENSIC REPORT",
    "CERTIFICATE",
)

SUBSTITUTIONS = [
    "device_geometry: a regular file has no BLKGETSIZE64, so the size is the "
    "file's own size and the block size is 512 bytes.",
    "_reread_serial: a regular file has no sysfs serial to re-read, so the "
    "serial the operator typed is checked by the guard only.",
]


class NotSimulated(RuntimeError):
    """The target is not a file this script created. Nothing was written."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _medium(path: Path, seed: int) -> None:
    """A file of seeded bytes, standing in for a stick that holds data."""
    rng = random.Random(seed)
    path.write_bytes(rng.randbytes(MEDIUM_BYTES))


def _device(path: Path, serial: str, mounted_at: list[str]) -> Device:
    return Device(
        path=str(path),
        model="SIMULATED MEDIUM (host file)",
        serial=serial,
        size_bytes=MEDIUM_BYTES,
        rotational=False,
        transport="usb",
        is_system_disk=False,
        mounted_at=mounted_at,
        pt_type=None,
        by_id_path=None,
    )


def _capabilities() -> DeviceCapabilities:
    """What a USB bridge typically lets a host see: no firmware sanitize."""
    return DeviceCapabilities(
        ata_security_erase=False,
        ata_enhanced_erase=False,
        ata_sanitize_ops=[],
        nvme_sanicap={},
        is_sed_opal=False,
        security_frozen=False,
        est_erase_seconds=1,
        achievable_levels={SanitizationLevel.CLEAR},
        limitations=[
            "Declared for the simulated medium, not probed: a USB bridge "
            "typically passes no firmware sanitize command through."
        ],
    )


def _require_simulated(target: Path, work: Path) -> None:
    """The last gate before the engine writes: a file, inside this run's directory."""
    resolved = target.resolve()
    if (
        not resolved.is_file()
        or resolved.is_block_device()
        or not resolved.is_relative_to(work.resolve())
        or str(resolved).startswith("/dev/")
    ):
        raise NotSimulated(
            f"{target} is not a regular file inside {work}. The simulation only "
            "writes to media it created itself. Nothing was written."
        )


def _stage(name: str, code_path: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"stage": name, "banner": BANNER, "code_path": code_path, **result}


def run(work: Path) -> dict[str, Any]:
    """Every stage, in order. Returns the journey as data."""
    from core.erase import drive
    from core.erase.drive import ChainLedgerSink, Geometry, execute, select_method
    from core.ledger.chain import Ledger
    from core.report.render import build_report, write_report
    from core.report.sign import (
        fingerprint,
        load_or_create_key,
        public_key_of,
        sign_report,
    )
    from core.report.verify_report import verify_report_file

    work.mkdir(parents=True, exist_ok=True)
    media = work / "media"
    media.mkdir()
    target_path = media / "sim-stick-A.img"
    mounted_path = media / "sim-stick-B.img"
    _medium(target_path, SEED)
    _medium(mounted_path, SEED + 1)
    target = _device(target_path, "SIM-26149-A", [])
    mounted = _device(mounted_path, "SIM-26149-B", ["/run/media/simulated/STICK-B"])
    caps = _capabilities()
    before = {"A": _sha256(target_path), "B": _sha256(mounted_path)}
    stages: list[dict[str, Any]] = []

    # ------------------------------------------------------------ DISCOVER
    stages.append(
        _stage(
            "DISCOVER",
            "core.models.Device (enumeration substituted: the media are files)",
            {
                "devices": [
                    {
                        "path": device.path,
                        "model": device.model,
                        "serial": device.serial,
                        "size_bytes": device.size_bytes,
                        "transport": device.transport,
                        "mounted_at": device.mounted_at,
                        "sha256_before": before[label],
                    }
                    for label, device in (("A", target), ("B", mounted))
                ],
                "capability": {
                    "achievable_levels": sorted(
                        level.value for level in caps.achievable_levels
                    ),
                    "firmware_sanitize": "none reported",
                    "basis": caps.limitations[0],
                },
            },
        )
    )

    # ----------------------------------------------------------- PREFLIGHT
    refusal = ""
    try:
        guard.assert_erasable(mounted)
    except SanctumError as refused:
        refusal = refused.message
    blocked = derive_workflow(
        WorkflowFacts(
            device_present=True,
            preflight_ran=True,
            preflight_safe=not refusal,
            preflight_refusal=refusal,
        )
    )
    guard.assert_erasable(target)
    guard.assert_serial_confirmed(target, target.serial)
    stages.append(
        _stage(
            "PREFLIGHT",
            "core.device.guard.assert_erasable, assert_serial_confirmed; "
            "core.workflow.derive",
            {
                "blocked_device": {
                    "serial": mounted.serial,
                    "workflow": blocked.as_dict(),
                },
                "target_device": {
                    "serial": target.serial,
                    "not_system_disk": True,
                    "not_mounted": True,
                    "typed_serial_matches": True,
                },
            },
        )
    )

    # ---------------------------------------------------------------- PLAN
    method, method_limits = select_method(target, caps, SanitizationLevel.CLEAR)
    purge_refusal = ""
    try:
        select_method(target, caps, SanitizationLevel.PURGE)
    except SanctumError as refused:
        purge_refusal = refused.message
    stages.append(
        _stage(
            "PLAN",
            "core.erase.drive.select_method",
            {
                "level": SanitizationLevel.CLEAR.value,
                "method": method.value,
                "limitations": method_limits,
                "purge": purge_refusal or "reachable",
            },
        )
    )

    # ------------------------------------- SIMULATED SANITIZATION + VERIFY
    key = load_or_create_key(work / "keys", DEMO_PASSPHRASE)
    finger = fingerprint(public_key_of(key))
    ledger_root = work / "ledger"
    ledger = Ledger(
        ledger_root,
        tool_version="sanctum-forensics/simulation",
        pubkey_fingerprint=finger,
    )
    job = EraseJob(
        job_id=JOB_ID,
        device=target,
        level=SanitizationLevel.CLEAR,
        dry_run=False,
        confirmed_serial=target.serial,
        method=None,
    )
    geometry = Geometry(
        size_bytes=MEDIUM_BYTES, logical_block_size=SECTOR, physical_block_size=SECTOR
    )
    _require_simulated(target_path, work)
    phases: list[str] = []
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(drive, "device_geometry", return_value=geometry)
        )
        stack.enter_context(mock.patch.object(drive, "_reread_serial"))
        generator = execute(job, caps, ledger=ChainLedgerSink(ledger))
        while True:
            try:
                phases.append(next(generator).phase)
            except StopIteration as stop:
                result = stop.value
                break
    after = {"A": _sha256(target_path), "B": _sha256(mounted_path)}
    verify_entry = next(
        entry
        for entry in ledger.entries()
        if entry.operation.startswith("erase.verify")
        and ledger.params_of(entry).get("job_id") == JOB_ID
    )
    verification = {
        key_: value
        for key_, value in ledger.params_of(verify_entry).items()
        if key_ != "job_id"
    }
    stages.append(
        _stage(
            "SIMULATED SANITIZATION",
            "core.erase.drive.execute (dry_run=False, against a host file)",
            {
                "phases": list(dict.fromkeys(phases)),
                "method": result.method.value,
                "bytes_written": result.bytes_written,
                "passes": result.passes,
                "target_sha256_before": before["A"],
                "target_sha256_after": after["A"],
                "target_changed": before["A"] != after["A"],
                "blocked_device_unchanged": before["B"] == after["B"],
            },
        )
    )
    stages.append(
        _stage(
            "SIMULATED VERIFICATION",
            "core.erase.verify.verify, the VERIFY phase of the same run",
            {
                "passed": verification.get("passed"),
                "strategy": verification.get("strategy"),
                "bytes_checked": verification.get("bytes_checked"),
                "residual_risk": result.residual_risk.level,
            },
        )
    )

    # ---------------------------------------------------- FORENSIC REPORT
    excerpt = [
        json.loads(entry.model_dump_json())
        for entry in ledger.entries()
        if ledger.params_of(entry).get("job_id") == JOB_ID
        or entry.operation == "GENESIS"
    ]
    fields: dict[str, Any] = {
        "case_id": CASE_ID,
        "operator": "simulation",
        "generated_at": datetime.now(UTC),
        "tool_version": "sanctum-forensics/simulation",
        "device": {
            **target.model_dump(mode="json"),
            "logical_block_size": SECTOR,
            "physical_block_size": SECTOR,
        },
        "method": result.plan.model_dump(mode="json"),
        "hidden_areas": {},
        "verification": verification,
        "residual_risk": result.residual_risk.model_dump(mode="json"),
        "limitations": [
            f"{BANNER}: the medium is a host file created by this run.",
            *(f"Substituted for a file: {item}" for item in SUBSTITUTIONS),
            *result.limitations,
        ],
        "ledger_excerpt": excerpt,
        "chain_verification": ledger.verify(),
        "pubkey_fingerprint": finger,
        "job_state": "complete",
    }
    unsigned = build_report(**fields)
    signed = build_report(**fields, signature=sign_report(unsigned, key))
    json_path, pdf_path = write_report(signed, work / "report")
    graded = verify_report_file(json_path, ledger_root=ledger_root)
    chain = ledger.verify()
    stages.append(
        _stage(
            "FORENSIC REPORT",
            "core.report.render.build_report; core.report.verify_report",
            {
                "verdict": graded.verdict.value,
                "checks": {item.name.value: item.passed for item in graded.checks},
                "chain_status": chain.status.value,
                "chain_entries": chain.entry_count,
                "limitations": fields["limitations"],
            },
        )
    )

    # -------------------------------------------------------- CERTIFICATE
    tampered = work / "report" / "tampered.forensic.json"
    body = json.loads(json_path.read_text(encoding="utf-8"))
    body["sections"]["device_identity"]["serial"] = "SIM-26149-Z"
    tampered.write_text(json.dumps(body), encoding="utf-8")
    rejected = verify_report_file(tampered, ledger_root=ledger_root)
    stages.append(
        _stage(
            "CERTIFICATE",
            "core.report.sign.sign_report (Ed25519); core.report.render.write_report",
            {
                "json_sha256": _sha256(json_path),
                "pdf_bytes": pdf_path.stat().st_size,
                "pubkey_fingerprint": finger,
                "signature_valid": graded.ok,
                "tamper_test": {
                    "changed": "device_identity.serial",
                    "verdict": rejected.verdict.value,
                    "rejected": not rejected.ok,
                },
                "signer_identity": "a key generated for this run; proves "
                "integrity, not who signed",
            },
        )
    )

    return {
        "banner": BANNER,
        "population": "SIMULATION",
        "physical_device_opened": False,
        "substitutions": SUBSTITUTIONS,
        "stages": stages,
        "report_json": str(json_path),
        "report_pdf": str(pdf_path),
    }


def render(journey: dict[str, Any]) -> str:
    by = {stage["stage"]: stage for stage in journey["stages"]}
    blocked = by["PREFLIGHT"]["blocked_device"]["workflow"]
    sanitize = by["SIMULATED SANITIZATION"]
    verify = by["SIMULATED VERIFICATION"]
    report = by["FORENSIC REPORT"]
    cert = by["CERTIFICATE"]
    plan = by["PLAN"]
    lines = [BANNER, ""]
    lines.append("DISCOVER")
    for device in by["DISCOVER"]["devices"]:
        mount = ", ".join(device["mounted_at"]) or "not mounted"
        lines.append(f"  {device['serial']}  {device['size_bytes']} bytes  {mount}")
    lines.append(f"  capability  {by['DISCOVER']['capability']['basis']}")
    lines.append("PREFLIGHT")
    lines.append(f"  SIM-26149-B  {blocked['state']}")
    for reason in blocked["why_blocked"]:
        lines.append(f"    WHY BLOCKED  {reason}")
    lines.append("  SIM-26149-A  passed: not system, not mounted, typed serial matches")
    lines.append("PLAN")
    lines.append(f"  {plan['level']} by {plan['method']}, selected from capability")
    lines.append(f"  PURGE  {plan['purge']}")
    lines.append("SIMULATED SANITIZATION")
    lines.append(f"  phases  {' -> '.join(sanitize['phases'])}")
    lines.append(f"  {sanitize['bytes_written']} bytes written to the host file")
    lines.append(
        f"  medium changed: {sanitize['target_changed']}; blocked medium "
        f"unchanged: {sanitize['blocked_device_unchanged']}"
    )
    lines.append("SIMULATED VERIFICATION")
    lines.append(
        f"  {verify['strategy']} over {verify['bytes_checked']} bytes: "
        + ("passed" if verify["passed"] else "FAILED")
    )
    lines.append("FORENSIC REPORT")
    lines.append(f"  verdict {report['verdict']}, chain {report['chain_status']}")
    lines.append("CERTIFICATE")
    lines.append(f"  signature valid: {cert['signature_valid']}")
    lines.append(
        f"  one field changed ({cert['tamper_test']['changed']}): "
        f"{cert['tamper_test']['verdict']}"
    )
    lines.append("")
    lines.append(BANNER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="also write the journey as JSON")
    parser.add_argument(
        "--keep",
        type=Path,
        help="a new, empty directory to keep the media, ledger and report in",
    )
    args = parser.parse_args(argv)
    # The engine logs every ledger append at debug level. The journey's own
    # output is the record; warnings, such as the mounted refusal, still show.
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    if args.keep is not None:
        if args.keep.exists() and any(args.keep.iterdir()):
            parser.error(f"{args.keep} is not empty")
        journey = run(args.keep)
    else:
        scratch = Path(tempfile.mkdtemp(prefix="sanctum-simulation-"))
        try:
            journey = run(scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    sys.stdout.write(render(journey) + "\n")
    if args.json:
        args.json.write_text(
            json.dumps(journey, indent=1, default=str) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
