"""Sanctum-side steps of the hardware validation run. Emits JSON, prints nothing else.

Split out of ``hardware-validation.sh`` so the shell handles what shells are
good at - safety gates, process timing, driving PhotoRec - and this handles what
needs the venv: enumerating through the real probes, running the real erase
engine against real media, and computing recall against known hashes.

Every subcommand writes one JSON object to stdout and exits non-zero only on a
failure the run cannot continue past. A step that *measures a disappointing
result* still exits zero: a recall of 0.0 is a finding, not an error, and a
harness that aborted on it would hide exactly what it exists to surface.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MIB = 1024 * 1024


def configure_logging() -> None:
    """Send every log line to stderr, leaving stdout for JSON alone.

    structlog's default writes to stdout, which interleaves human-readable log
    lines with the JSON this script emits and makes the result unparsable.
    Found by running the harness rather than by reading it: the first carve
    step produced perfectly good output that ``json.loads`` refused.

    The log lines are kept, not silenced. On a run against real hardware the
    ledger and progress logs are half the evidence about what actually
    happened, and a harness that discarded them to keep its own output tidy
    would be optimising for the wrong reader.
    """
    import logging

    import structlog

    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")


# --------------------------------------------------------------------------
# Phase A.1 - enumerate, and cross-check against the system's own tools
# --------------------------------------------------------------------------


def cmd_enumerate(args: argparse.Namespace) -> int:
    """What Sanctum reports about the device, beside what lsblk and hdparm say.

    The comparison is the point. Sanctum parses ``lsblk`` and ``hdparm`` output
    itself, so a disagreement means either a parsing bug or a field that means
    something different from what was assumed - and both are the kind of thing
    that only shows up against real hardware with a real vendor string.
    """
    from core.device import capabilities, hidden_areas
    from core.device.enumerate import get_device
    from core.errors import SanctumError

    result: dict[str, Any] = {"step": "enumerate", "path": args.device}

    device = get_device(args.device)
    result["sanctum"] = {"device": device.model_dump(mode="json")}

    try:
        caps = capabilities.probe(device)
        result["sanctum"]["capabilities"] = caps.model_dump(mode="json")
        result["sanctum"]["achievable_levels"] = sorted(
            level.value for level in caps.achievable_levels
        )
    except SanctumError as exc:
        result["sanctum"]["capabilities"] = None
        result["sanctum"]["capability_error"] = exc.message

    try:
        hidden = hidden_areas.detect_hidden_areas(device)
        result["sanctum"]["hidden_areas"] = hidden.model_dump(mode="json")
    except SanctumError as exc:
        result["sanctum"]["hidden_areas"] = None
        result["sanctum"]["hidden_area_error"] = exc.message

    # -- the independent view ---------------------------------------------
    ground: dict[str, Any] = {}
    lsblk = subprocess.run(
        [
            "lsblk", "-Jbdno",
            "NAME,PATH,MODEL,SERIAL,SIZE,ROTA,TRAN,TYPE,RM,PTTYPE",
            args.device,
        ],
        capture_output=True, text=True, check=False,
    )
    if lsblk.returncode == 0:
        try:
            ground["lsblk"] = json.loads(lsblk.stdout)["blockdevices"][0]
        except (ValueError, KeyError, IndexError) as exc:
            ground["lsblk_error"] = f"unparsable: {exc}"
    else:
        ground["lsblk_error"] = lsblk.stderr.strip()

    hdparm = subprocess.run(
        ["hdparm", "-I", args.device], capture_output=True, text=True, check=False
    )
    ground["hdparm_returncode"] = hdparm.returncode
    ground["hdparm_stdout"] = hdparm.stdout
    ground["hdparm_stderr"] = hdparm.stderr.strip()

    udev = subprocess.run(
        ["udevadm", "info", "--query=property", f"--name={args.device}"],
        capture_output=True, text=True, check=False,
    )
    ground["udev"] = {
        key: value
        for key, _, value in (
            line.partition("=") for line in udev.stdout.splitlines()
        )
        if key in {
            "ID_MODEL", "ID_SERIAL_SHORT", "ID_VENDOR", "ID_BUS", "ID_FS_LABEL"
        }
    }
    result["ground_truth"] = ground

    # -- disagreements -----------------------------------------------------
    # Reported, never reconciled. A harness that quietly preferred one source
    # would destroy the only signal this step produces.
    disagreements: list[dict[str, str]] = []
    lsblk_row = ground.get("lsblk", {})
    udev_row = ground.get("udev", {})

    def compare(field: str, sanctum: Any, other: Any, source: str) -> None:
        if other in (None, "") or sanctum == other:
            return
        disagreements.append(
            {
                "field": field,
                "sanctum": str(sanctum),
                source: str(other),
            }
        )

    compare("model", device.model, lsblk_row.get("model"), "lsblk")
    compare("serial", device.serial, lsblk_row.get("serial"), "lsblk")
    compare("size_bytes", device.size_bytes, lsblk_row.get("size"), "lsblk")
    compare("rotational", device.rotational, lsblk_row.get("rota"), "lsblk")
    compare("serial", device.serial, udev_row.get("ID_SERIAL_SHORT"), "udev")
    compare("model", device.model, udev_row.get("ID_MODEL"), "udev")
    result["disagreements"] = disagreements

    emit(result)
    return 0


# --------------------------------------------------------------------------
# Phase A.4 / A.5 - erase and verify
# --------------------------------------------------------------------------


def _ledger(root: Path) -> Any:
    from core.ledger.chain import Ledger

    return Ledger(
        root, tool_version="sanctum-forensics/0.0.0", pubkey_fingerprint=""
    )


def cmd_erase(args: argparse.Namespace) -> int:
    """Run the real erase engine against real media.

    ``--dry-run`` exercises the identical code path and writes nothing, which
    is what step A.4 hashes around to prove.
    """
    from core.device import capabilities
    from core.device.enumerate import get_device
    from core.erase.drive import ChainLedgerSink, execute
    from core.errors import SanctumError
    from core.models import EraseJob, SanitizationLevel

    device = get_device(args.device)
    job = EraseJob(
        job_id=args.job_id,
        device=device,
        level=SanitizationLevel(args.level),
        dry_run=args.dry_run,
        confirmed_serial=device.serial,
        method=None,
    )

    started = time.monotonic()
    progress: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "step": "erase",
        "dry_run": args.dry_run,
        "job_id": args.job_id,
    }

    try:
        generator = execute(
            job,
            capabilities.probe(device),
            ledger=ChainLedgerSink(_ledger(Path(args.ledger_root))),
        )
        while True:
            try:
                progress.append(next(generator).model_dump(mode="json"))
            except StopIteration as stop:
                result["result"] = stop.value.model_dump(mode="json")
                break
    except SanctumError as exc:
        result["error"] = exc.message
        result["error_kind"] = type(exc).__name__
        result["remediation"] = exc.remediation
    except OSError as exc:
        result["error"] = str(exc)
        result["error_kind"] = type(exc).__name__

    elapsed = time.monotonic() - started
    written = int((result.get("result") or {}).get("bytes_written") or 0)
    result["elapsed_seconds"] = round(elapsed, 3)
    result["throughput_mib_per_sec"] = (
        round(written / MIB / elapsed, 2) if elapsed > 0 and written else 0
    )
    # Phases in the order they were emitted, so the report can show that all
    # six ran rather than only that the job finished.
    result["phases"] = list(dict.fromkeys(item["phase"] for item in progress))
    result["progress_records"] = len(progress)
    emit(result)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Read the medium back and report what verification actually established."""
    from core.device.enumerate import get_device
    from core.erase.verify import VerifyConfig, verify
    from core.models import EraseMethod

    device = get_device(args.device)
    started = time.monotonic()
    outcome = verify(
        device,
        EraseMethod(args.method),
        config=VerifyConfig(full_read_max_bytes=args.full_read_max),
    )
    emit(
        {
            "step": "verify",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "result": outcome.model_dump(mode="json"),
        }
    )
    return 0


# --------------------------------------------------------------------------
# Phase A.7 - report, tamper, verify
# --------------------------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> int:
    """Generate the signed report, tamper one byte, verify, restore, verify."""
    from datetime import UTC, datetime

    from core.report.render import build_report, write_report
    from core.report.sign import (
        fingerprint,
        load_or_create_key,
        public_key_of,
        sign_report,
    )
    from core.report.verify_report import verify_report_file

    ledger_root = Path(args.ledger_root)
    ledger = _ledger(ledger_root)
    erase = json.loads(Path(args.erase_json).read_text()) if args.erase_json else {}
    verification = (
        json.loads(Path(args.verify_json).read_text()) if args.verify_json else {}
    )
    payload = erase.get("result") or {}

    key = load_or_create_key(Path(args.key_dir))
    finger = fingerprint(public_key_of(key))

    excerpt = [
        json.loads(entry.model_dump_json())
        for entry in ledger.entries()
        if ledger.params_of(entry).get("job_id") == args.job_id
        or entry.operation == "GENESIS"
    ]

    fields: dict[str, Any] = {
        "case_id": args.case_id,
        "operator": args.operator,
        "generated_at": datetime.now(UTC),
        "tool_version": "sanctum-forensics/0.0.0",
        "device": payload.get("device") or {},
        "method": payload.get("plan") or {},
        "hidden_areas": payload.get("hidden_areas") or {},
        "verification": verification.get("result") or {},
        "residual_risk": payload.get("residual_risk") or {},
        "limitations": list(payload.get("limitations") or []),
        "ledger_excerpt": excerpt,
        "chain_verification": ledger.verify(),
        "pubkey_fingerprint": finger,
    }
    unsigned = build_report(**fields)
    signature = sign_report(unsigned, key)
    signed = build_report(**fields, signature=signature)
    json_path, pdf_path = write_report(signed, Path(args.out_dir))

    result: dict[str, Any] = {
        "step": "report",
        "json_path": str(json_path),
        "pdf_path": str(pdf_path),
        "json_bytes": json_path.stat().st_size,
        "pdf_bytes": pdf_path.stat().st_size,
        "pubkey_fingerprint": finger,
        "ledger_excerpt_entries": len(excerpt),
    }

    def check(label: str) -> dict[str, Any]:
        outcome = verify_report_file(json_path, ledger_root=ledger_root)
        return {
            "label": label,
            "ok": outcome.ok,
            "checks": [
                {
                    "name": item.name.value,
                    "passed": item.passed,
                    "applicable": item.applicable,
                    "detail": item.detail,
                }
                for item in outcome.checks
            ],
        }

    result["verify_as_written"] = check("as written")

    # -- tamper exactly one byte ------------------------------------------
    original = json_path.read_bytes()
    # A byte inside the payload, not in the signature block: flipping a byte of
    # the signature itself would prove only that a corrupt signature fails to
    # parse, which is a much weaker claim than "an altered report is detected".
    marker = original.find(b'"operator"')
    offset = marker + 20 if marker != -1 else len(original) // 2
    tampered = bytearray(original)
    tampered[offset] = tampered[offset] ^ 0x01
    json_path.write_bytes(bytes(tampered))
    result["tamper"] = {
        "offset": offset,
        "original_byte": original[offset],
        "tampered_byte": tampered[offset],
        "context": original[max(offset - 30, 0) : offset + 30].decode(
            "utf-8", "replace"
        ),
    }
    result["verify_tampered"] = check("one byte flipped")

    json_path.write_bytes(original)
    result["verify_restored"] = check("restored")

    emit(result)
    return 0


# --------------------------------------------------------------------------
# Phase B - acquire, verify the image, carve, score against known hashes
# --------------------------------------------------------------------------


def cmd_acquire(args: argparse.Namespace) -> int:
    """Image the device read-only and verify the result against its record."""
    from core.carve.acquire import AcquireOptions, acquire, verify_image

    started = time.monotonic()
    generator = acquire(
        Path(args.device),
        Path(args.dest),
        fmt=args.fmt,
        options=AcquireOptions(compression=args.compression, operator="validation"),
        ledger=_ledger(Path(args.ledger_root)) if args.ledger_root else None,
        job_id=args.job_id,
    )
    records = 0
    while True:
        try:
            next(generator)
            records += 1
        except StopIteration as stop:
            record = stop.value
            break
    elapsed = time.monotonic() - started

    produced = Path(args.dest)
    if not produced.exists() and args.fmt == "e01":
        produced = produced.with_suffix(".E01")

    integrity = verify_image(produced, record)
    emit(
        {
            "step": "acquire",
            "fmt": args.fmt,
            "dest": str(produced),
            "elapsed_seconds": round(elapsed, 3),
            "throughput_mib_per_sec": (
                round(record.bytes_read / MIB / elapsed, 2) if elapsed > 0 else 0
            ),
            "bytes_read": record.bytes_read,
            "on_disk_bytes": produced.stat().st_size if produced.exists() else 0,
            "sha256": record.sha256,
            "blake3": record.blake3,
            "bad_sectors": len(record.bad_sectors),
            "limitations": record.limitations,
            "write_blocked": record.write_blocked,
            "progress_records": records,
            "integrity": integrity.model_dump(mode="json"),
        }
    )
    return 0


def cmd_carve(args: argparse.Namespace) -> int:
    """Run the whole recovery pipeline and score it against known hashes.

    The manifest is ``{"sha256": {"name": ..., "deleted": bool}}`` for every
    file that was written to the card, recorded before anything was deleted.
    Recall counts deleted files reproduced byte for byte; nothing softer, for
    the same reason ``testkit/calibrate.py`` counts nothing softer.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from api.carve_job import carve_generator

    manifest = json.loads(Path(args.manifest).read_text())
    deleted = {
        digest for digest, item in manifest.items() if item.get("deleted")
    }
    live = {digest for digest, item in manifest.items() if not item.get("deleted")}

    started = time.monotonic()
    generator = carve_generator(
        Path(args.image),
        undelete=True,
        carve_signatures=True,
        out_dir=Path(args.out_dir) if args.out_dir else None,
    )
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            outcome = stop.value
            break
    elapsed = time.monotonic() - started

    candidates = outcome["candidates"]
    found = {item["sha256"] for item in candidates}

    by_fs: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = item.get("fs_type") or args.filesystem
        row = by_fs.setdefault(
            key, {"candidates": 0, "exact": set(), "named": 0}
        )
        row["candidates"] += 1
        if item.get("original_name"):
            row["named"] += 1
        if item["sha256"] in deleted:
            row["exact"].add(item["sha256"])

    per_fs = {
        name: {
            "candidates": row["candidates"],
            "named": row["named"],
            "exact": len(row["exact"]),
            "deleted_planted": len(deleted),
            "recall_bp": (
                int(round(len(row["exact"]) * 10_000 / len(deleted)))
                if deleted
                else 0
            ),
            "precision_bp": (
                int(round(len(row["exact"]) * 10_000 / row["candidates"]))
                if row["candidates"]
                else 0
            ),
        }
        for name, row in by_fs.items()
    }

    emit(
        {
            "step": "carve",
            "image": args.image,
            "filesystem": args.filesystem,
            "elapsed_seconds": round(elapsed, 3),
            "candidates": len(candidates),
            "planted_deleted": len(deleted),
            "planted_live": len(live),
            "recovered_deleted_exact": len(deleted & found),
            "recovered_live_exact": len(live & found),
            "overall_recall_bp": (
                int(round(len(deleted & found) * 10_000 / len(deleted)))
                if deleted
                else 0
            ),
            "per_filesystem": per_fs,
            "partitions": outcome["partitions"],
            "unallocated_bytes": outcome["unallocated_bytes"],
            "limitations": outcome["limitations"],
            "buckets": {
                bucket: sum(
                    1 for item in candidates if item["bucket"] == bucket
                )
                for bucket in ("HIGH", "MEDIUM", "LOW")
            },
        }
    )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Real-media recall beside what the synthetic corpus predicted.

    This is the point of Phase B. Every confidence number the report prints is
    derived from weights calibrated on synthetic images; if real media recalls
    differently, the calibration is describing something other than reality and
    the numbers inherit the error.
    """
    synthetic: dict[str, dict[str, int]] = {}
    csv_path = Path(args.calibration_csv)
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                synthetic[row["filesystem"]] = {
                    "deleted": int(row["deleted"]),
                    "recall_bp": int(row["recall_bp"]),
                    "precision_bp": int(row["precision_bp"]),
                }

    rows: list[dict[str, Any]] = []
    for path in args.carve_json:
        measured = json.loads(Path(path).read_text())
        for name, row in measured["per_filesystem"].items():
            predicted = synthetic.get(name, {})
            recall_delta = (
                row["recall_bp"] - predicted["recall_bp"]
                if "recall_bp" in predicted
                else None
            )
            rows.append(
                {
                    "filesystem": name,
                    "real_deleted": row["deleted_planted"],
                    "real_exact": row["exact"],
                    "real_recall_bp": row["recall_bp"],
                    "real_precision_bp": row["precision_bp"],
                    "synthetic_deleted": predicted.get("deleted"),
                    "synthetic_recall_bp": predicted.get("recall_bp"),
                    "synthetic_precision_bp": predicted.get("precision_bp"),
                    "recall_delta_bp": recall_delta,
                    # A divergence wider than 10 points is not noise on a
                    # corpus this size and means the calibration table is
                    # describing synthetic media rather than real media.
                    "diverges": (
                        abs(recall_delta) > 1000 if recall_delta is not None else None
                    ),
                }
            )

    emit({"step": "compare", "rows": rows})
    return 0


def cmd_hash_tree(args: argparse.Namespace) -> int:
    """Record the SHA-256 of every file under a directory, before deletion."""
    manifest: dict[str, dict[str, Any]] = {}
    root = Path(args.root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[digest] = {
            "name": str(path.relative_to(root)),
            "size": path.stat().st_size,
            "deleted": False,
        }
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    emit({"step": "hash_tree", "root": str(root), "files": len(manifest)})
    return 0


def cmd_mark_deleted(args: argparse.Namespace) -> int:
    """Mark manifest entries as deleted, by name."""
    path = Path(args.manifest)
    manifest = json.loads(path.read_text())
    names = set(args.names)
    marked = 0
    for item in manifest.values():
        if item["name"] in names:
            item["deleted"] = True
            marked += 1
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    emit({"step": "mark_deleted", "marked": marked, "requested": len(names)})
    return 0


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    enumerate_parser = sub.add_parser("enumerate")
    enumerate_parser.add_argument("--device", required=True)
    enumerate_parser.set_defaults(handler=cmd_enumerate)

    erase_parser = sub.add_parser("erase")
    erase_parser.add_argument("--device", required=True)
    erase_parser.add_argument("--job-id", required=True)
    erase_parser.add_argument("--ledger-root", required=True)
    erase_parser.add_argument("--level", default="CLEAR")
    erase_parser.add_argument("--dry-run", action="store_true")
    erase_parser.set_defaults(handler=cmd_erase)

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--device", required=True)
    verify_parser.add_argument("--method", default="SINGLE_PASS_OVERWRITE")
    verify_parser.add_argument(
        "--full-read-max", type=int, default=64 * 1024 * MIB
    )
    verify_parser.set_defaults(handler=cmd_verify)

    report_parser = sub.add_parser("report")
    report_parser.add_argument("--job-id", required=True)
    report_parser.add_argument("--ledger-root", required=True)
    report_parser.add_argument("--key-dir", required=True)
    report_parser.add_argument("--out-dir", required=True)
    report_parser.add_argument("--case-id", default="HW-VALIDATION")
    report_parser.add_argument("--operator", default="validation")
    report_parser.add_argument("--erase-json")
    report_parser.add_argument("--verify-json")
    report_parser.set_defaults(handler=cmd_report)

    acquire_parser = sub.add_parser("acquire")
    acquire_parser.add_argument("--device", required=True)
    acquire_parser.add_argument("--dest", required=True)
    acquire_parser.add_argument("--fmt", default="raw", choices=["raw", "e01"])
    acquire_parser.add_argument("--compression", default="fast")
    acquire_parser.add_argument("--ledger-root")
    acquire_parser.add_argument("--job-id", default="acquire")
    acquire_parser.set_defaults(handler=cmd_acquire)

    carve_parser = sub.add_parser("carve")
    carve_parser.add_argument("--image", required=True)
    carve_parser.add_argument("--manifest", required=True)
    carve_parser.add_argument("--filesystem", required=True)
    carve_parser.add_argument("--out-dir")
    carve_parser.set_defaults(handler=cmd_carve)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--carve-json", nargs="+", required=True)
    compare_parser.add_argument(
        "--calibration-csv",
        default="docs/performance/calibration-filesystems.csv",
    )
    compare_parser.set_defaults(handler=cmd_compare)

    hash_parser = sub.add_parser("hash-tree")
    hash_parser.add_argument("--root", required=True)
    hash_parser.add_argument("--out", required=True)
    hash_parser.set_defaults(handler=cmd_hash_tree)

    mark_parser = sub.add_parser("mark-deleted")
    mark_parser.add_argument("--manifest", required=True)
    mark_parser.add_argument("--names", nargs="+", required=True)
    mark_parser.set_defaults(handler=cmd_mark_deleted)

    args = parser.parse_args()
    configure_logging()
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
