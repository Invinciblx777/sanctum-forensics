# Release record, 2026-09-25

Population: SYNTHETIC VALIDATION and PACKAGE-IDENTITY only. **PHYSICAL VALIDATION: none.**
No physical device, and not SANCTUMREC, was opened, read, hashed, written, erased,
acquired or restored during this run; no sudo was used.

## Commits (branch `docs/readme-redesign`, not pushed)

| Commit | Content |
|---|---|
| `9d601b8` | helper re-checks the erase authorization at the write seam; API gate hardening |
| `ace1e93` | Sanitize screen drives the workflow authorization API |
| `0127172` | tests, browser evidence, audit, doc fixes |
| this record | package identity and final matrix (docs only) |

## Results on `0127172` (all rerun; none carried over)

| Check | Result |
|---|---|
| Full pytest | 1916 passed, 34 skipped |
| `tests/api/test_resume.py` alone | 9 passed |
| Workflow/gate/seam/erase-job/resume API files, one command | 68 passed |
| `tests/helper`, `tests/test_workflow.py`, `tests/ui`, dry-run test | 94 passed |
| Ruff | passed |
| mypy strict, four gates | passed (87, 1, 13, 10 files) |
| UI unit tests / `tsc -b` / build | 77 of 77 / clean / built (oxlint: warnings only) |
| Browser (fixture server, synthetic helper) | 56 of 56 |
| `scripts/demo_simulation.py`, `demo_fragmented.py` | ran; simulation marker printed |
| `scripts/validation_workflow_matrix.py` | PASS, no violations |
| Missing device (`media_benchmark.py preflight`) | refused, "No other device was substituted" |

The 34 skips are host-dependent (root/loopback, non-Linux). The earlier focused-command
setup error is a pytest 9.1.1 argument-order quirk (see `final-audit-2026-09-25.md`).

## Package identity

Built with `scripts/build-linux-portable.sh` (podman, glibc 2.31 image) from the clean tree at
`0127172aec475db16a1bfd86a028e8bb05eab067`.

| Artifact | SHA-256 |
|---|---|
| `Sanctum-0.0.0-x86_64.AppImage` | `19f9d80c4f844c114dccac3dd44ee7f7c601fe35c99e07291c68281e44ff6c2c` |
| `sanctum_0.0.0_amd64.deb` | `98f1b1aaeb700a0a9021e0ba810026fba34fc9051cdc009c4a4290ebed634774` |

Checked: both packages embed `build_info.json` with commit `0127172…`; both bundle the new
Sanitize screen (`Open workflow and verify backup` in the UI bundle) and `helper.authorization`
in the frozen executable; the AppImage's extracted binary, started with its own state directory,
answered `/health` with commit `0127172…` and `ui_bundled: true`. Source (`git rev-parse HEAD`),
the API `/health` and the packaged `/health` agree; this record's own commit is a docs-only
child of `0127172`.

**Not done:** the packaged app's device discovery, `/platform`, workflow routes and the packaged
smoke script (`scripts/package_smoke.py`) were not run, because they enumerate the host's real
block devices, which includes SANCTUMREC. The `.deb` was unpacked and inspected, not installed.
The packaged helper seam is proven by test on source, not exercised in the package.
