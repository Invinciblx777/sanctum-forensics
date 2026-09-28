# Sanctum Forensics — tutorial

How to run it and use each screen. For exhaustive detail see
[`docs/user-manual.md`](docs/user-manual.md) — this is the fast path.

## What it is

Three modules, one audit trail:

| Module | Does |
|---|---|
| **M1** Secure Drive Eraser | Whole-drive Clear or Purge, method chosen from what the device itself reports it can do |
| **M2** Secure File & Folder Eraser | File/folder/batch erase, metadata cleanse, desktop-trace sweep (thumbnails, recent-files, Trash) |
| **M3** File Carving & Recovery | Read-only acquisition → media map → undelete → carve → score |

Every destructive action is gated (backup → human approval → typed serial →
one-use server authorization → final re-check). There is **no dry-run mode** —
every erase you start is real. Read that twice before you click Erase on
anything you care about.

## 1. Install and run

Full per-OS instructions: [`INSTALL.md`](INSTALL.md). Fast path on Linux
(Fedora/Debian/Ubuntu, needs Python 3.11 — your `python3` is probably not
3.11, `devsetup.sh` handles that):

```bash
./scripts/devsetup.sh
source .venv/bin/activate
make check          # ruff + 4x mypy --strict + full pytest suite
make run             # prints http://127.0.0.1:8787/session/<token> — open it
```

That starts everything unprivileged. **Whole-device operations need the
privileged helper**, which is a second, separate process — this split is the
whole point of the security model, not a chore:

```bash
# Terminal 1 — the only root process, talks over a 0600 unix socket
sudo .venv/bin/python -m helper --operator-uid "$(id -u)" --state-dir <dir>

# Terminal 2 — the API and UI, as yourself, no sudo
SANCTUM_HELPER_SOCKET=/run/sanctum/helper.sock \
SANCTUM_STATE_DIR=<dir> \
SANCTUM_KEY_PASSPHRASE=<passphrase> \
.venv/bin/python -m api.main
```

If you skip the helper, `GET /health` reports the `HELPER_IN_PROCESS`
limitation and whole-device work runs inside the unprivileged process instead
— fine for trying out file erase and recovery, not for a real drive wipe.

Desktop window instead of a browser tab: `pip install -e ".[desktop]" &&
python -m api.desktop` (Linux uses Qt; Windows/macOS start Sanctum itself
elevated — see `INSTALL.md`).

No-device demo of the recovery pipeline: `python scripts/demo_fragmented.py`
(synthetic, 0.5s, no root needed).

## 2. Devices screen

Landing page. Every attached block device, with model, serial, size,
platform, and a **capability badge** — hover it, the tooltip is the evidence
for the badge, not decoration. `CLEAR ONLY` means the device probe (`hdparm
-I` / NVMe Identify / sysfs) found no firmware sanitize path, so Purge is
never offered on that hardware. The system disk reads `BLOCKED FOR SAFETY`
and cannot be selected. A mounted volume is refused with the mount point
named — unmount it yourself; nothing here auto-unmounts.

## 3. Sanitize a drive (M1)

Click a device → Sanitize screen. This is the gated destructive path, in
order:

1. **Preflight** re-reads the device from the OS live.
2. **Backup.** *Erase this device* → acquire (or point at) a read-only image
   of the whole device → the server hashes and sizes it. No image, no erase.
3. **Plan.** Method, level, fill byte, ETA — all derived from a live 64 MiB
   write calibration on *this* device, not a guess. If the controller
   acknowledges a zero-fill faster than it can actually program the medium,
   the tool substitutes `0xA5` and flags `CONTROLLER_WRITE_ELISION` — this is
   the finding that makes the demo worth watching, see `demo_script.md`.
4. **Approval.** Tick the acknowledgement, type the serial, *Approve* — the
   server issues a one-use authorization.
5. **Final check.** Type the serial again, *Erase*. The API re-reads the
   device, the helper re-reads it *again* at the write seam. Any drift and
   it's `BLOCKED` with a WHY BLOCKED reason — nothing written.
6. **Execute → Verify → Report.** Progress panel shows phase; verification
   reads the medium back and reports PASSED / FAILED / INCONCLUSIVE / NOT
   APPLICABLE with a strategy and probability, never a bare "done".

## 4. Erase files and folders (M2)

Pick file(s)/folder(s) → confirm → erase. Overwrites, renames through random
same-length names, truncates, unlinks, then sweeps alternate data streams,
xattrs, and desktop traces (thumbnail cache, recent-files, Trash/Recycle
Bin) it can tie to the erased path. What it could **not** remove or verify
is listed as a named finding, not silently dropped — on a copy-on-write or
journaled filesystem this list is never empty, and that's honest, not a bug.

## 5. Recover (M3)

**Acquire**: point at a device or existing image; read-only, hashed
(SHA-256 + BLAKE3) as it goes. **Recovery screen**: pick the image → the
media map draws first (zero/fill/text/structured/high-entropy regions) →
run Undelete and/or Carve. Filter results to HIGH, click a candidate to open
**Score breakdown**: six named components (header, exact length, decoder,
entropy, filesystem metadata, no-overlap) summing to an evidence score out of
10000 — not a probability. A file rebuilt from two fragments is capped at
7999, so it can never read HIGH.

## 6. Audit / Reports

*Generate signed report* on a finished job → Ed25519-signed JSON (+ PDF).
*Verify* runs five independent checks (signature, key-fingerprint-vs-genesis,
chain integrity, chain-store re-verification, blobs present) and grades the
result `VERIFIED` / `VERIFIED_WITH_LIMITATIONS` / `PARTIAL` /
`FAILED_VERIFICATION` — never rounds a limited result up to a clean pass.
*Tamper a scratch copy* flips one byte and re-verifies live: signature fails,
the other four checks still hold (they don't read from the report's own
copy). Verify from the CLI, on any machine, without the app:

```bash
sanctum verify-report <report.json> --ledger-root <ledger dir>
```

## 7. Platform / capability matrix

Shows, per platform and per capability, one of seven honest states —
`SUPPORTED` (physically validated on that device class), `IMPLEMENTED /
UNVALIDATED`, `DEVICE-DEPENDENT`, `PLATFORM-LIMITED`, `REQUIRES PRIVILEGE`,
`BLOCKED FOR SAFETY`, `NOT IMPLEMENTED` — each with its reason. This is
generated from `core/platform/capability.py`, not hand-typed, and a test
fails if the committed doc drifts from the resolver. Worth opening once just
to see what "we don't oversell this" looks like as a UI element.

## Where to go deeper

- [`INSTALL.md`](INSTALL.md) — full per-OS install, packages, troubleshooting
- [`docs/user-manual.md`](docs/user-manual.md) — every screen, every refusal
  message, cancellation behavior, the ledger format
- [`docs/validation/judge-defense-card.md`](docs/validation/judge-defense-card.md)
  — 37 adversarial questions with sourced answers; read this before any Q&A
- [`docs/limitations.md`](docs/limitations.md) — everything the tool does not
  guarantee, stated plainly
- [`README.md`](README.md) — current validation state, capability matrix link,
  what's physically proven vs. implemented-but-unvalidated
