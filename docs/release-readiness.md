# Release readiness: Module 1, cross-platform

What is true, what is not, and where the evidence is. Every line here points
at something a reader can open: a CI run, an evidence file in
`core/platform/validation_record.json`, or a document.

Statuses: **DONE** (evidence exists), **PARTIAL** (done for part of the
scope, named), **NOT DONE** (and why).

_Evidence: `platform-ci` run 35679982678 on the
`release/cross-platform-validation` branch (2026-09-22) - gate, three
platform jobs and three package jobs all green - plus the runs before it for
the defects each one found._

## The gate

| # | Condition | Status | Evidence |
|---|---|---|---|
| 1 | Windows CI actually runs | DONE | `platform-ci / platform (windows-latest)` - full Python suite, adapter smoke, file-erase suite |
| 2 | macOS CI actually runs | DONE | `platform-ci / platform (macos-14)` |
| 3 | Linux regression stays green | DONE | `gate / full suite` and `platform (ubuntu-latest)` |
| 4 | Windows device discovery executes | DONE | `platform-smoke-Windows.json`: disks enumerated from the Storage module, the system disk recognised and refused |
| 5 | macOS device discovery executes | DONE | `platform-smoke-macOS.json`: APFS containers traced to their physical stores, boot disk refused |
| 6 | Windows file/folder erase tests run | DONE | `validation-Windows.json`, suite `file_erase` |
| 7 | macOS file/folder erase tests run | DONE | `validation-macOS.json`, suite `file_erase` |
| 8 | Windows junction / reparse protections validated | DONE | `tests/platform/test_windows_filesystem.py` creates a real junction on the runner and proves the erase stays inside the named root |
| 9 | macOS APFS limitation behaviour validated | DONE | `tests/platform/test_macos_filesystem.py`: the erase runs, the verification is `not_possible` with a reason, and a residual finding is recorded |
| 10 | Windows installer built | DONE in CI | `package (windows-latest)`: `SanctumSetup.exe` (15.0 MB) built, installed silently to `%LOCALAPPDATA%\Programs\Sanctum`, the installed app driven through 24 of 24 checks, then uninstalled and the directory confirmed gone |
| 11 | macOS DMG built | DONE in CI | `package (macos-14)`: `Sanctum.dmg` (44.0 MB) built and mounted, `Sanctum.app` driven through 24 of 24 checks |
| 12 | Linux package still works | DONE | AppImage + `.deb` built locally and in CI; `package-smoke-Linux.json`: 24 of 24 checks |
| 13 | Capability states reflect real evidence | DONE | every row carries `source`; file-erase rows stay UNVERIFIED until `validation_record.json` records a passing suite for that platform |
| 14 | `validation_record.json` holds real platform results | DONE | suites plus per-feature rows (platform, OS, architecture, commit, tests, result, date, evidence, limitations) |
| 15 | No fake platform checkmarks | DONE | `tests/platform/test_assessment_and_matrix.py` pins that a Linux pass does not lift a Windows row, and that whole-drive is UNSUPPORTED off Linux |
| 16 | Security regression tests pass | DONE | `tests/platform/test_boundary_and_security.py`: missing, wrong, stale and cross-origin sessions; non-loopback Host; quit refusal |
| 17 | Packaged session-token protection verified | DONE | `package-smoke-*.json`: 401 without the cookie, 303 on the session link, 400 for a foreign Host |
| 18 | Dev-server exposure explicitly controlled | DONE | `python -m api.main` mints a token per start and prints the URL; `SANCTUM_DEV_INSECURE=1` is the only way off, and says so |
| 19 | Documentation matches the implementation | DONE | `docs/platform-support.md`, `docs/packaging.md`, `docs/validation/platform-matrix.md`, `docs/validation/hardware-platform-matrix.md` |
| 20 | Whole-drive Linux-only scope documented | DONE | `docs/platform-support.md`, `docs/limitations.md`; the app refuses with the reason on Windows and macOS |
| 21 | Hardware limitations documented | DONE | `docs/validation/hardware-platform-matrix.md` separates VALIDATED, CI-VALIDATED, NOT YET VALIDATED and UNSUPPORTED |
| 22 | Build artifacts reproducible/documented | PARTIAL | every build records version, commit, platform, architecture, date, Python and Node (`packaging/build_info.py`), honours `SOURCE_DATE_EPOCH`, and publishes SHA-256 sums. Bit-for-bit reproducibility is **not** claimed: PyInstaller embeds timestamps and the wheels are not pinned by hash. |
| 23 | Full test suite passes | DONE | development host 1644 passed / 34 skipped / 0 failed; the same suite green on all three runners |
| 24 | UI platform screen works | DONE | rows with a plain-English *Why?*, screenshotted from the real server; `ui/tests/platform.test.ts` |
| 25 | Demo runs offline | DONE | no network call at run time; `tests/api/test_offline_serving.py` and the CSP; the packaged app was driven with the runner's network unused |

## Not in scope, and why

| Item | Status | Reason |
|---|---|---|
| Windows whole-drive sanitization | NOT DONE, deliberate | No validated raw-disk engine. `\\.\PhysicalDriveN` plus `IOCTL_STORAGE_PROTOCOL_COMMAND` could reach it; writing one without hardware to validate against would be the one component where being wrong destroys the wrong disk. The app refuses with that reason. |
| macOS whole-drive sanitization | NOT DONE, deliberate | Same, plus: internal Apple storage is purged by macOS's own *Erase All Content and Settings*, which this app can neither perform nor verify. It names that path instead. |
| Firmware Purge on hardware | NOT DONE | Needs a drive that reports ATA SANITIZE or NVMe sanitize, and a person to lose the data on it. |
| Code signing and notarization | NOT DONE | No certificates. The remaining commands are in `docs/packaging.md`. |
| Physical-device validation on Windows and macOS | NOT DONE | No such machine was available; CI runners have no removable media. `docs/validation/hardware-platform-matrix.md` lists exactly what a person with a Windows box, a Mac and a disposable stick would close. |

## The claim this supports

Sanctum is one cross-platform application with native capability adapters for
Linux, Windows and macOS. Device assessment and file and folder sanitization
are platform-aware and were executed on all three operating systems in CI;
whole-drive sanitization remains limited to the validated Linux pathway, and
unsupported or unverified operations are refused with a reason rather than
silently downgraded.
