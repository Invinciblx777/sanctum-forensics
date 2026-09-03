# Architecture

Scaffold only. Each section is filled in as its milestone lands.

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
| UI | `ui` | none | localhost | Vite + React + TypeScript. |

## Invariants

- Method selection flows from probed `DeviceCapabilities`, never user preference alone.
- Destructive ops require: dry-run cleared **and** operator-typed serial match.
- Nothing under `core.carve` opens a device or image `O_RDWR`.
- Core layers never touch the network.
- Every operation appends a ledger entry before it is considered done.
