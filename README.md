# Sanctum Forensics

SIH 26149 (NTRO). Integrated secure data sanitization and forensic file recovery.

- **M1 Secure Drive Eraser** — capability-driven, NIST SP 800-88 Rev.1 Clear/Purge/Destroy.
- **M2 Secure File & Folder Eraser** — targeted erasure with honest filesystem caveats.
- **M3 Advanced File Carving & Recovery** — undelete + signature + structure carving.

This repository is currently **scaffold only**: structure, data contracts, and
stubs. Every function body raises `NotImplementedError`. Behaviour lands module by
module in later milestones.

See `CLAUDE.md` for the non-negotiables and `docs/architecture.md` for the layout.

## Setup

Debian/Ubuntu, Python 3.11:

```bash
./scripts/devsetup.sh
source .venv/bin/activate
```

## Gate

```bash
make lint         # ruff
make typecheck    # mypy --strict core/
make test         # pytest
./scripts/check.sh   # all three
```

`docker build .` and the Linux-only acceptance checks: `./scripts/verify-linux.sh`.
