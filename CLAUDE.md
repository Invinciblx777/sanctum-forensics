# Project: Sanctum Forensics (SIH 26149, NTRO)
Integrated secure data sanitization + forensic file recovery. Three modules:
M1 Secure Drive Eraser, M2 Secure File & Folder Eraser, M3 Advanced File Carving & Recovery.

## Non-negotiables
- NIST SP 800-88 Rev. 2 vocabulary everywhere (Rev. 1 was withdrawn on 2025-09-26):
  Clear / Purge / Destroy. Never claim "military-grade" or Gutmann-for-SSD. Erase
  method is selected from probed device capability, never from user preference alone.
- Destructive ops are opt-in twice: dry-run is the default, and the user types the device
  serial to confirm. Refuse any device with a mounted filesystem or the root filesystem.
- The evidence path is read-only. Carving code never opens a device or image O_RDWR.
- Every operation appends a hash-chained ledger entry. Entry N contains SHA-256 of N-1.
- If a guarantee cannot be made, the report says so. Honest limits over marketing claims.

## Style
- Python 3.11, type hints on every public function, no bare except, no print() in core/.
- Long operations are generators yielding progress dicts. No blocking calls in api/.
- Structured logging via structlog. Core layers never touch the network.
- pytest for everything, fixtures only, no real device access in the test suite.

## Vocabulary
sanitize = destroy data. carve = recover without filesystem metadata.
undelete = recover using surviving filesystem metadata.
