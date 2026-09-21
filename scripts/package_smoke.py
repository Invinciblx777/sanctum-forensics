"""Drive an *installed* Sanctum the way a user would, and check what it does.

Run against the packaged executable - the AppImage, the installed
``Sanctum.exe``, the ``.app`` binary - never against a source checkout. The
point is to prove that the thing a user double-clicks works with no developer
environment behind it:

1. it starts, on a private loopback port, and answers ``/health``;
2. it refuses every request that does not carry this launch's session cookie,
   and refuses a request addressed to a non-loopback ``Host``;
3. it discovers devices through the platform adapter and assesses each one;
4. it erases a folder of scratch files **inside a temporary directory this
   script created**, and never anything else;
5. it issues a signed certificate for that erase and verifies it;
6. it stops when asked.

No device is opened and no whole-drive operation is requested. The scratch
directory is this script's own; if the app erased anything outside it, step 4
fails loudly.

    python scripts/package_smoke.py dist/Sanctum-0.0.0-x86_64.AppImage \\
        --out package-smoke-Linux.json
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PASSPHRASE = "package smoke test passphrase"


class Client:
    """Tiny HTTP client that keeps the session cookie, with no dependencies."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPRedirectHandler(),
        )

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        host: str | None = None,
        cookies: bool = True,
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        if host:
            request.add_header("Host", host)
        opener = self.opener if cookies else urllib.request.build_opener()
        try:
            with opener.open(request, timeout=30) as response:  # noqa: S310
                raw = response.read().decode("utf-8", "replace")
                return response.status, (
                    json.loads(raw) if raw.startswith(("{", "[")) else raw
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return exc.code, (json.loads(raw) if raw.startswith(("{", "[")) else raw)
        except urllib.error.URLError as exc:
            return 0, str(exc)


def wait_for_url(url_file: Path, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if url_file.is_file() and url_file.read_text(encoding="utf-8").strip():
            return url_file.read_text(encoding="utf-8").strip()
        time.sleep(0.5)
    raise TimeoutError(f"the app never wrote its session URL to {url_file}")


def _finish(client: Client, job_id: str, timeout_s: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        code, status = client.request(f"/jobs/{job_id}")
        if (
            code == 200
            and isinstance(status, dict)
            and status.get("state") != "running"
        ):
            return status
        time.sleep(0.3)
    raise TimeoutError(f"job {job_id} did not finish")


def run(executable: Path, argv_extra: list[str]) -> dict[str, Any]:
    scratch = Path(tempfile.mkdtemp(prefix="sanctum-smoke-"))
    state = scratch / "state"
    victim = scratch / "victim"
    keep = scratch / "keep"
    (victim / "nested").mkdir(parents=True)
    keep.mkdir()
    (keep / "untouched.txt").write_text("this file is outside the erase target")
    for index in range(3):
        (victim / f"file{index}.bin").write_bytes(os.urandom(4096))
    (victim / "nested" / "deep.bin").write_bytes(os.urandom(2048))
    url_file = scratch / "session-url.txt"

    environment = dict(os.environ)
    environment.update(
        {
            "SANCTUM_URL_FILE": str(url_file),
            "SANCTUM_STATE_DIR": str(state),
            "SANCTUM_BROWSER": "1",
        }
    )
    # To a file, not a pipe: the app logs steadily, and a pipe nobody reads
    # fills its buffer and blocks the very process being tested.
    log_path = scratch / "app.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(  # noqa: S603 - argv list, no shell
        [str(executable), *argv_extra],
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = "") -> None:
        checks.append(
            {"check": name, "result": "PASS" if ok else "FAIL", "detail": detail}
        )

    try:
        url = wait_for_url(url_file, timeout_s=120)
        base = url.split("/session/")[0]
        client = Client(base)

        code, _ = client.request("/health", cookies=False)
        record("health refused without the session cookie", code == 401, code)

        code, _ = client.request(url[len(base) :])
        record("session link accepted", code in (200, 303), code)

        code, health = client.request("/health")
        record("health answers with the cookie", code == 200, health)
        record(
            "session protection is on",
            isinstance(health, dict) and health.get("session_protected") is True,
        )
        record(
            "launcher mode",
            isinstance(health, dict) and health.get("launcher") is True,
        )
        record(
            "UI bundle served from the package",
            isinstance(health, dict) and health.get("ui_bundled") is True,
        )

        code, _ = client.request("/health", host="evil.example")
        record("non-loopback Host refused", code == 400, code)

        code, status = client.request("/platform")
        ok = code == 200 and isinstance(status, dict) and status.get("operations")
        record("platform matrix served", bool(ok), code)
        operations = (
            {row["operation"]: row for row in (status.get("operations") or [])}
            if isinstance(status, dict)
            else {}
        )
        record(
            "every capability row has a source",
            bool(operations) and all(row["source"] for row in operations.values()),
        )
        family = (
            (status.get("platform") or {}).get("family")
            if isinstance(status, dict)
            else ""
        )
        if family != "linux":
            record(
                "whole-drive unsupported off Linux",
                all(
                    operations[name]["status"] == "UNSUPPORTED"
                    for name in ("whole_drive_clear", "whole_drive_purge")
                    if name in operations
                ),
                {
                    k: v["status"]
                    for k, v in operations.items()
                    if k.startswith("whole_drive")
                },
            )

        code, devices = client.request("/devices")
        rows = devices.get("devices", []) if isinstance(devices, dict) else []
        record(
            "device discovery through the package",
            code == 200 and bool(rows),
            len(rows),
        )
        record(
            "every device assessed",
            all("assessment" in row and "normalized" in row for row in rows),
        )
        record(
            "protected devices are NOT AVAILABLE",
            all(
                row["assessment"]["headline"] == "NOT AVAILABLE"
                for row in rows
                if row["normalized"]["system_device"] or row["normalized"]["mounted"]
            ),
        )

        code, accepted = client.request(
            "/jobs/erase-files",
            method="POST",
            body={
                "paths": [str(victim)],
                "dry_run": False,
                "confirm": True,
                "recursive": True,
            },
        )
        record("erase accepted", code == 200 and isinstance(accepted, dict), accepted)
        job_id = accepted["job_id"] if isinstance(accepted, dict) else ""
        status = _finish(client, job_id)
        record(
            "erase completed", status.get("state") == "complete", status.get("error")
        )
        record("target folder is gone", not victim.exists())
        record(
            "nothing outside the target was touched",
            (keep / "untouched.txt").is_file()
            and (keep / "untouched.txt").read_text().startswith("this file is outside"),
        )
        records = (status.get("result") or {}).get("records") or []
        record("one record per path", len(records) >= 5, len(records))
        verification = [
            (item.get("verification") or {}).get("passed") for item in records
        ]
        record(
            "verification is never invented",
            all(value in (True, False, None) for value in verification),
            verification,
        )

        code, report = client.request(
            f"/reports/{job_id}",
            method="POST",
            body={"case_id": "", "operator": "", "key_passphrase": PASSPHRASE},
        )
        record("certificate issued", code == 200 and isinstance(report, dict), code)
        code, check = client.request(f"/reports/{job_id}/verify")
        record(
            "certificate verifies",
            code == 200 and isinstance(check, dict) and check.get("passed") is True,
            check if code != 200 else check.get("passed"),
        )

        code, _ = client.request("/app/quit", method="POST", cookies=False)
        record("quit refused without the session", code == 401, code)
        client.request("/app/quit", method="POST")
        try:
            process.wait(timeout=30)
            record("app exited on quit", True, process.returncode)
        except subprocess.TimeoutExpired:
            record("app exited on quit", False, "still running")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
        log.close()
        output = log_path.read_text(encoding="utf-8", errors="replace")
        shutil.rmtree(scratch, ignore_errors=True)

    return {
        "executable": str(executable),
        "size_bytes": executable.stat().st_size if executable.is_file() else 0,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": checks,
        "result": "PASS" if all(c["result"] == "PASS" for c in checks) else "FAIL",
        "app_output_tail": output[-4000:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--arg", action="append", default=[], help="extra argument for the executable"
    )
    args = parser.parse_args(argv)

    if not args.executable.exists():
        print(f"no such executable: {args.executable}", file=sys.stderr)
        return 2
    evidence = run(args.executable, args.arg)
    if args.out:
        args.out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    for check in evidence["checks"]:
        print(f"  {check['result']:<5} {check['check']} {check['detail']}")
    print(evidence["result"])
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
