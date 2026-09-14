# Architecture

One rule shapes the whole layout: **a layer may only make claims it can evidence.**
Everything below follows from it — why carving cannot open a device for writing, why
one process holds root and the rest hold none, why every operation ends in a ledger
entry before it is considered done.

## Layers

| Layer | Package | Privilege | Network | Notes |
|-------|---------|-----------|---------|-------|
| Models | `core.models` | none | no | Pydantic v2 data contracts, shared everywhere. |
| Errors | `core.errors` | none | no | Every error carries a `remediation`. |
| Device | `core.device` | via helper | no | Enumerate, probe capability, detect HPA/DCO, safety guard. Read-only. |
| Erase | `core.erase` | via helper | no | Whole-drive (M1) and file/folder (M2). Destructive, dry-run default. |
| Carve | `core.carve` | via helper (read-only) | no | Acquire, undelete, signature, structure, validate, score, classify. |
| Ledger | `core.ledger` | none | no | Hash-chained append-only audit log. Entry N embeds SHA-256 of N-1. |
| Report | `core.report` | none | no | Render + detached-sign + independent verify. States honest limits. |
| Helper | `helper` | root | no | The single privilege boundary. Unix socket, allowlisted JSON-RPC. |
| API | `api` | none | localhost | FastAPI, non-blocking, streams job progress over SSE. |
| UI | `ui` | none | localhost | Vite + React + TypeScript, fully bundled. |

## Invariants

Each of these is enforced by a test, not by convention.

- **Method selection flows from probed `DeviceCapabilities`, never user preference
  alone.** `core/erase/drive.py:select_method` is a decision table over what the probe
  returned; the UI preselects what the engine would have chosen on its own.
- **Destructive ops require two gates**: dry-run cleared *and* an operator-typed serial
  the server re-reads from the device itself.
- **Nothing under `core.carve` opens a device or image `O_RDWR`.**
  `core/carve/evidence.py` declares no write method at all and opens `O_RDONLY`.
- **Core layers never touch the network.** There is no base-URL constant anywhere in
  `ui/src/lib/api.ts`, which makes the offline property a grep rather than an audit.
- **Every operation appends a ledger entry before it is considered done.**
  `core/erase/drive.py:execute` refuses to start without a sink.
- **An unknown is never reported as a negative.** Every field a platform may be unable
  to determine is `T | None`, and `None` means "could not determine" — see the class
  docstring on `FileInspection` in `core/models.py`.

## How a job flows

```
UI            POST /jobs/erase-drive          (dry_run, typed_serial)
 │
API           api/routes/jobs.py              builds the job, opens the Ledger,
 │                                            hands the generator to the registry
 │
Registry      api/jobs.py                     runs the generator on a worker thread,
 │                                            publishes Progress to SSE subscribers
 │
Engine        core/erase/drive.py             yields Progress, records each phase
 │                                            through core/erase/sink.py
 │
Ledger        core/ledger/chain.py            appends; entry N carries SHA-256 of N-1
 │
Report        core/report/render.py           nine sections, canonical JSON, Ed25519
                                              detached signature, PDF rendering
```

The registry is the only component that knows a job is asynchronous. Engines are plain
generators, which is what makes them testable without an event loop and resumable from
a ledger checkpoint after a crash.

## Privilege split

The API, the UI and every core layer run as an ordinary user. One process runs as root:
`helper/daemon.py`, reached over a `0600` Unix socket with `SO_PEERCRED` checking, taking
JSON-RPC calls whose `method` must be a key in a static allowlist. No shell string is ever
accepted from a caller and the daemon never spawns a shell.

Full threat model in [`privilege-boundary.md`](privilege-boundary.md).

## Why the ledger is a hash chain and not a blockchain

The theme is "Blockchain & Cybersecurity" and the honest engineering answer is a
hash-chained append-only log with an anchoring interface, not a chain we cannot run
offline. Entry N contains the SHA-256 of entry N-1, so insertion, deletion and mutation
are all detectable and `verify()` names the first broken sequence number rather than
returning a bare invalid. Periodic Merkle roots can be published through
`core/ledger/anchor.py`, whose default implementation records plainly that no external
witness was configured.

This is the defensible version. A distributed ledger adds a network dependency to a tool
whose entire operating environment is air-gapped, and it would not make a single claim
in the report more true.
