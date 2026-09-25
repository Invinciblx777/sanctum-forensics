# Sanitize screen browser run, 2026-09-25

The real Sanitize screen, driven in Chromium (headless shell, revision 1243,
Playwright 1.63.0 from a separate venv, 1366 x 768), against the **real API app**
(real routes, workflow state machine, authorization store, ledger) over the
synthetic helper the API test suite uses. `drivers/server.py` builds it;
`drivers/sanitize.py` drives it. `results.json` holds all 42 checks (42 pass).

No physical device was opened, enumerated or probed. Device rows are synthetic
`Device` records run through the real Linux adapter and erase preview. The
"positive" erase reaches the recording helper's `run_erase` and nothing else.

What it shows: a mounted device is BLOCKED with WHY BLOCKED and cannot be
selected; the simulation banner at every simulation stage and no `/workflow/`
request during a dry run; the real erase's HUMAN APPROVAL REQUIRED, backup and
plan from the server; the approve button disabled until acknowledgement and the
exact serial are both present; a server-issued `authorization_id`; PLAN READY;
refusal rendering (BLOCKED, WHY BLOCKED, `PHYSICAL DEVICE MODIFIED: FALSE`) for a
mount after listing, a backup changed after approval, and a device identity
changed after approval; execution on the fixture; and direct API bypass attempts
(reused, missing, invented authorization) refused with exactly one write.

Reproduce: start `server.py STATE 8811` with the project venv and
`PYTHONPATH=.`, then `sanitize.py STATE OUT` with
`SANCTUM_BROWSER_EXE` set as in `../browser-2026-09-24/README.md`.
