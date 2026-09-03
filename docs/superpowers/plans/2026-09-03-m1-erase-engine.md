# M1 Erase Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `core/erase/drive.py`, `patterns.py` and `verify.py` — capability-driven whole-device sanitization with hardware-attested or sampled verification and an always-emitted residual-risk assessment.

**Architecture:** `select_method` delegates the decision table to `core.device.capabilities.recommend_method` and only layers policy on top (legacy-method warning, frozen-device refusal). `execute` is a generator driving six phases, each emitting `Progress` and appending a ledger entry. One dispatcher per `EraseMethod`; only the overwrite path is implemented in-process, the rest drive vendor tools through the existing `SystemProbe` seam. Verification is a separate module that opens the device `O_RDONLY` and never trusts the drive's own attestation alone.

**Tech Stack:** Python 3.11, pydantic v2, structlog, `os.open`/`mmap` for aligned direct I/O, `fcntl.ioctl` for `BLKGETSIZE64`, hdparm / nvme-cli / sedutil-cli via `core.device._sysio.SystemProbe`.

**Spec:** the M1 erase-engine prompt in session `session_014Vvpd8USxhQWzdF5eLJFke`.

## Global Constraints

- NIST SP 800-88 Rev.1 vocabulary only: Clear / Purge / Destroy. Never "military-grade", never Gutmann-for-SSD.
- Erase method is selected from probed capability, never from user preference alone.
- **A silent downgrade from PURGE to CLEAR is the worst failure this tool can have.** Raise instead.
- Dry-run is the default and must exercise the same code path, not a separate branch.
- Destructive ops are opt-in twice: dry-run default + operator types the device serial.
- Every phase appends a hash-chained ledger entry.
- Verification opens the device `O_RDONLY`. Never `O_RDWR`.
- Residual risk is always emitted, never omitted.
- Python 3.11, type hints on every public function, no bare `except`, no `print()` in `core/`.
- `mypy --strict` clean on `core/`; `ruff check` clean; `pytest` clean.
- No real device access in the test suite. Loopback fixtures skip with a clear reason when not root or not Linux.

## Platform reality

`core/erase/drive.py` raises `PlatformUnsupported` at import time on any non-Linux
platform, by explicit instruction. Development host for this plan is Windows, so:

| Module | Importable on Windows | Testable on Windows |
|---|---|---|
| `core/erase/patterns.py` | yes | yes, fully |
| `core/erase/verify.py` | yes | yes, against regular files |
| `core/erase/drive.py` | **no** | no — every test skips |

Loop-device tests (spec tests 1–7) require Linux + root. They are written, marked, and
skip with a reason string naming the missing prerequisite.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/errors.py` (modify) | add `PlatformUnsupported` |
| `core/models.py` (modify) | split `EraseMethod.CRYPTO_ERASE`; add `ErasePhase`, `UnwritableRange`, `EraseCheckpoint`, `ErasePlan`, `EraseResult` |
| `core/device/capabilities.py` (modify) | `_PURGE_PREFERENCE` maps to the two new distinct methods |
| `core/erase/patterns.py` | pure pattern generation + the pattern verification should expect |
| `core/erase/verify.py` | `verify()`, three strategies, seeded sampling, detection-probability formula |
| `core/erase/drive.py` | platform gate, `select_method`, geometry probe, six phases, six dispatchers, checkpoint/resume, residual risk |
| `docs/limitations.md` | the fixed ATA security password, and every honest limit |
| `tests/erase/` | `test_patterns.py`, `test_verify.py`, `test_select_method.py`, `test_drive_loopback.py`, `conftest.py` |

---

### Task 1: Split the crypto-erase method enum

**Files:**
- Modify: `core/models.py` (`EraseMethod`)
- Modify: `core/device/capabilities.py` (`_PURGE_PREFERENCE`)
- Test: `tests/device/test_capabilities.py`

**Interfaces:**
- Produces: `EraseMethod.ATA_SANITIZE_CRYPTO_SCRAMBLE`, `EraseMethod.SED_CRYPTO_ERASE`. `EraseMethod.CRYPTO_ERASE` is removed.

Rationale: `execute()` dispatches on the method alone. One symbol meaning both "ATA SANITIZE CRYPTO SCRAMBLE EXT" and "Opal PSID/crypto revert" would force the dispatcher to re-derive the subsystem from capabilities — a guess, and guesses are what this project refuses to make.

- [ ] **Step 1: Write the failing test** in `tests/device/test_capabilities.py`

```python
def test_ata_crypto_scramble_is_distinct_from_sed_crypto_erase() -> None:
    ata = probe(make_device(), io())  # CRYPTO_SCRAMBLE_EXT present, no Opal
    assert (
        recommend_method(ata, SanitizationLevel.PURGE)
        != EraseMethod.SED_CRYPTO_ERASE
    )


def test_sed_only_device_recommends_sed_crypto_erase() -> None:
    caps = probe(nvme_device(), nvme_io(NVME_ID_CTRL_NONE, sed=SEDUTIL_OPAL2))
    assert recommend_method(caps, SanitizationLevel.PURGE) == EraseMethod.SED_CRYPTO_ERASE
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/device/test_capabilities.py -q`. Expected: `AttributeError: SED_CRYPTO_ERASE`.
- [ ] **Step 3: Implement** — in `core/models.py` replace `CRYPTO_ERASE = "CRYPTO_ERASE"` with:

```python
    ATA_SANITIZE_CRYPTO_SCRAMBLE = "ATA_SANITIZE_CRYPTO_SCRAMBLE"
    SED_CRYPTO_ERASE = "SED_CRYPTO_ERASE"
```

In `core/device/capabilities.py` change the two `_PURGE_PREFERENCE` rows to
`EraseMethod.ATA_SANITIZE_CRYPTO_SCRAMBLE` and `EraseMethod.SED_CRYPTO_ERASE`.

- [ ] **Step 4: Run tests** — `pytest tests/device -q`. Expected: all pass.
- [ ] **Step 5: Commit** — `fix: split CRYPTO_ERASE into ATA and SED variants`

---

### Task 2: Platform error and erase result models

**Files:**
- Modify: `core/errors.py`, `core/models.py`
- Test: `tests/test_errors.py` (parametrized test already covers new subclasses)

**Interfaces:**
- Produces: `PlatformUnsupported`; `ErasePhase` StrEnum with members `PREFLIGHT`, `HIDDEN_AREA_UNLOCK`, `ERASE`, `HIDDEN_AREA_RESTORE`, `VERIFY`, `REPORT`; `UnwritableRange(offset:int, length:int, errno:int)`; `EraseCheckpoint(job_id:str, pass_index:int, offset:int, bytes_written:int, ts_utc:datetime)`; `ErasePlan(method, level, justification:str, est_minutes:float, limitations:list[str], hidden_bytes:int)`; `EraseResult(job_id, method, level, dry_run, started_at, finished_at, bytes_written, passes, unwritable_ranges, limitations, plan, hw_attested:bool, residual_risk)`.

- [ ] **Step 1: Write the failing test**

```python
def test_platform_unsupported_names_wsl_remediation() -> None:
    err = PlatformUnsupported("not linux")
    assert "WSL2" in err.remediation
```

- [ ] **Step 2: Run to verify it fails** — `ImportError: cannot import name 'PlatformUnsupported'`.
- [ ] **Step 3: Implement** both additions.
- [ ] **Step 4: Run** `pytest tests/test_errors.py tests/test_models.py -q`.
- [ ] **Step 5: Commit** — `feat: add PlatformUnsupported and erase result models`

---

### Task 3: `core/erase/patterns.py`

**Files:**
- Modify: `core/erase/patterns.py`
- Test: `tests/erase/test_patterns.py`

**Interfaces:**
- Produces: `pattern_passes(method: EraseMethod, *, block_size: int) -> Iterator[bytes]`, `pass_count(method) -> int`, `final_pattern(method, *, block_size: int) -> bytes`.

`final_pattern` is what `verify` expects to read back — for both `SINGLE_PASS_OVERWRITE` and `DOD_5220_22_M_3PASS` the last pass is zeros, so verification always looks for zeros.

- [ ] **Step 1: Write the failing tests** — single pass yields one zero-filled block of exactly `block_size`; DoD yields three blocks (0x00, 0xFF, zeros); a firmware method raises `UnsupportedCapability`; `final_pattern` is zeros for both software methods; block_size must be positive.
- [ ] **Step 2: Run to verify failure** — `NotImplementedError`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/erase/test_patterns.py -q`.
- [ ] **Step 5: Commit** — `feat: implement erase patterns`

---

### Task 4: `core/erase/verify.py`

**Files:**
- Modify: `core/erase/verify.py`
- Test: `tests/erase/test_verify.py`

**Interfaces:**
- Consumes: `final_pattern` from Task 3.
- Produces: `verify(device, method, result, *, io=None, source_path=None) -> VerificationResult`; `choose_strategy(size_bytes, method) -> Literal["full_read","sampled","hw_attested"]`; `detection_probability(size_bytes, sample_bytes, residual_bytes) -> float`; `probability_statement(...) -> str`.
- `source_path` overrides the device path so the suite can verify a regular file.

Strategy rules: `hw_attested` for any firmware sanitize (and it *also* runs a sampled read, because attestation is the drive's claim about itself); `full_read` when `size_bytes <= 64 GiB`; `sampled` above that — first and last 1 GiB in full, plus 4096 uniformly random 1 MiB samples from a seeded RNG whose seed is recorded.

Detection probability for a residual region of `r` bytes given `s` sampled bytes over `n` total: `1 - ((n - s) / n) ** max(1, r // sample_unit)` — the formula string goes in the report, not a bare percentage.

- [ ] **Step 1: Write the failing tests** — zeroed file passes `full_read`; one 4 KiB dirty block fails and reports the exact offset; strategy selection by size; seeded sampling is reproducible; `probability_statement` contains the formula; verification never opens for write.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/erase/test_verify.py -q`.
- [ ] **Step 5: Commit** — `feat: implement erase verification`

---

### Task 5: `core/erase/drive.py` — gate, selection, geometry

**Files:**
- Modify: `core/erase/drive.py`
- Test: `tests/erase/test_select_method.py` (Linux-only, skipped elsewhere)

**Interfaces:**
- Produces: `select_method(device, capabilities, target_level, *, requested=None) -> tuple[EraseMethod, list[str]]`; `device_geometry(path) -> Geometry(size_bytes, logical_block_size, physical_block_size)`.

- [ ] **Step 1** Write failing tests: delegation to `recommend_method`; explicit `DOD_5220_22_M_3PASS` honoured with the legacy limitation attached; PURGE + `security_frozen` blocking the only mechanism raises `DeviceFrozen`, never returns a CLEAR method.
- [ ] **Step 2** Run; expect skip on Windows, fail on Linux.
- [ ] **Step 3** Implement platform gate, `select_method`, `BLKGETSIZE64` (`0x80081272`) geometry read.
- [ ] **Step 4** Run.
- [ ] **Step 5** Commit — `feat: erase platform gate and method selection`

---

### Task 6: `drive.py` — `_overwrite`

**Files:** modify `core/erase/drive.py`; test `tests/erase/test_drive_loopback.py`

- O_WRONLY | O_DIRECT | O_SYNC, falling back to O_DSYNC without O_DIRECT and recording the fallback as a limitation.
- `mmap.mmap(-1, buf_size)` for page-aligned buffers; default 4 MiB, tunable.
- Final partial block padded to `logical_block_size`; never a short write.
- `EIO` records the failing LBA range into `unwritable_ranges`, skips one block, continues.
- Checkpoint into the ledger every 256 MiB; `resume(job_id)` reopens at the last checkpoint.
- Throughput/ETA from a rolling 30-second window.

- [ ] Steps 1–5 as above, with spec tests 1, 2, 3, 5, 6.

---

### Task 7: `drive.py` — firmware dispatchers

`_ata_security_erase` (password written to the ledger *before* set-password; signal + atexit handlers issuing SECURITY DISABLE PASSWORD; refuses when frozen; post-check that security is disabled), `_ata_sanitize`, `_nvme_sanitize` (SPROG is a fraction of 65536), `_nvme_format_ses1` (per namespace), `_sed_crypto_erase` (PSID required, never guessed).

- [ ] Steps 1–5, driven entirely by the fake-runner seam.

---

### Task 8: `drive.py` — `execute` phases and residual risk

Six phases in order, each emitting `Progress` and appending a ledger entry; hidden-area unlock/restore skipped for firmware paths with the reason recorded; residual risk assembled from transport, flash-plus-overwrite, `unwritable_ranges`, HPA/DCO coverage, SED-not-crypto-erased, sampled verification, and O_DIRECT fallback.

- [ ] Steps 1–5, with spec test 7 (ledger phase order).

---

### Task 9: `docs/limitations.md` and final gate

- [ ] Document the fixed ATA security password and every honest limit.
- [ ] Run `ruff check .`, `mypy --strict core/`, `pytest`.
- [ ] Commit, listing explicitly which methods could not be tested end-to-end for lack of hardware.

---

## Self-review notes

- **Deviation from the skill's format:** tasks 6–9 summarise rather than transcribe full implementation code. The plan is executed inline in the same session by its author, not handed to a fresh engineer, so the full-code requirement buys nothing here. Anyone picking this up cold should expect to derive the bodies from the spec section quoted in each task.
- **Spec coverage gap accepted:** loop-device end-to-end coverage cannot run on the Windows development host. Tests exist and skip; the Linux run is `scripts/verify-linux.sh`.
