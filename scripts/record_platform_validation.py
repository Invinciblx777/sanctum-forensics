"""Run the platform test suites on this host and record what actually passed.

    python scripts/record_platform_validation.py            # all suites
    python scripts/record_platform_validation.py file_erase # one suite

Writes ``core/platform/validation_record.json``, which the capability matrix
reads: a suite that is not recorded as ``PASS`` on a platform leaves every
capability it backs at UNVERIFIED on that platform. The record is only ever
written by this script from a real pytest run on the platform it names -
never by hand, and never for a platform other than the one it runs on.

Entries for other platforms are preserved, so a CI matrix can run this once
per OS and merge the three files (``--merge a.json b.json ...``).

Hardware validation is a separate section (``hardware``) and is **not**
written by this script: a suite passing on a CI runner is not a USB stick
sanitized. Those entries are added from ``docs/validation/`` runs on
designated test media, and default to ``NOT RUN``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "core" / "platform" / "validation_record.json"

#: suite -> pytest paths. Kept small and meaningful: each suite backs a named
#: group of capability rows.
SUITES: dict[str, list[str]] = {
    "file_erase": ["tests/erase/files", "tests/platform"],
    "recovery": ["tests/carve"],
    "whole_drive": ["tests/erase", "tests/device", "tests/helper"],
    "api": ["tests/api"],
}
#: whole_drive includes tests/erase, which contains the file suite too; the
#: file tests are excluded there so the two suites measure different code.
_EXCLUDE = {"whole_drive": ["tests/erase/files"]}


def _family() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


def _commit() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{head}{'+dirty' if dirty else ''}" if head else "unknown"


def _runner() -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return (
            f"GitHub Actions {os.environ.get('RUNNER_OS', '')} runner, run "
            f"{os.environ.get('GITHUB_RUN_ID', '?')}"
        ).strip()
    return f"developer machine ({platform.system()} {platform.release()})"


def run_suite(name: str) -> dict[str, object]:
    """Run one suite with pytest and summarise its JUnit XML."""
    paths = SUITES[name]
    with tempfile.TemporaryDirectory() as scratch:
        junit = Path(scratch) / "junit.xml"
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            *paths,
        ]
        for excluded in _EXCLUDE.get(name, []):
            argv.append(f"--ignore={excluded}")
        completed = subprocess.run(argv, cwd=ROOT, check=False)
        counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        try:
            tree = ET.parse(junit)
        except (OSError, ET.ParseError):
            return {
                "state": "FAIL",
                "reason": "pytest produced no report",
                "exit_code": completed.returncode,
            }
        for suite in tree.getroot().iter("testsuite"):
            tests = int(suite.get("tests", 0))
            failures = int(suite.get("failures", 0))
            errors = int(suite.get("errors", 0))
            skipped = int(suite.get("skipped", 0))
            counts["failed"] += failures
            counts["errors"] += errors
            counts["skipped"] += skipped
            counts["passed"] += tests - failures - errors - skipped
    ok = completed.returncode == 0 and counts["failed"] == 0 and counts["errors"] == 0
    return {
        "state": "PASS" if ok and counts["passed"] > 0 else "FAIL",
        "counts": counts,
        "exit_code": completed.returncode,
        "paths": paths,
        "runner": _runner(),
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}"[:160],
        "commit": _commit(),
        "date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _load(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _skeleton() -> dict[str, object]:
    return {
        "note": (
            "Written by scripts/record_platform_validation.py from real pytest "
            "runs. Do not edit by hand. A missing entry means NOT RUN."
        ),
        "suites": {},
        "hardware": {},
    }


def merge(paths: list[Path], out: Path) -> None:
    merged = _load(out) or _skeleton()
    suites = merged.setdefault("suites", {})
    assert isinstance(suites, dict)
    for path in paths:
        other = _load(path)
        for fam, entries in (other.get("suites") or {}).items():  # type: ignore[union-attr]
            suites.setdefault(fam, {}).update(entries)
    out.write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("suites", nargs="*", help=", ".join(SUITES))
    parser.add_argument("--out", type=Path, default=RECORD)
    parser.add_argument("--merge", nargs="+", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.merge:
        merge(args.merge, args.out)
        return 0

    unknown = sorted(set(args.suites) - set(SUITES))
    if unknown:
        parser.error(
            f"unknown suite(s): {', '.join(unknown)}; known: {', '.join(SUITES)}"
        )

    record = _load(args.out) or _skeleton()
    suites = record.setdefault("suites", {})
    assert isinstance(suites, dict)
    family = _family()
    failed = False
    for name in args.suites or list(SUITES):
        result = run_suite(name)
        suites.setdefault(family, {})[name] = result
        failed |= result["state"] != "PASS"
        print(f"{family} {name}: {result['state']} {result.get('counts')}")
    args.out.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
