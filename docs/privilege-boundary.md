# Privilege boundary

Scaffold only. Authoritative description lives in the module docstring of
`helper/daemon.py`; this file expands on the threat model as M1 is implemented.

## Summary

- One privileged process: `helper/daemon.py`, running as root.
- Everything else (API, UI, CLI) runs unprivileged.
- Transport: Unix domain socket at `/run/sanctum/helper.sock`, mode `0600`.
- Peer authentication: `SO_PEERCRED`; a uid other than the configured operator
  uid is dropped before any request body is read.
- Requests: JSON-RPC. `method` must be a key in the static `OPERATIONS` allowlist.
  No shell string is ever accepted from the caller; the daemon never spawns a shell.
- Parameters and results are structured and typed.

## Not yet designed (tracked for M1)

- Rate limiting / concurrency caps per operation.
- Audit of every dispatched call into the ledger.
- Handling of a wedged privileged operation (timeout, kill, device reset).
