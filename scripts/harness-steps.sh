# Step bookkeeping for hardware-validation.sh. Sourced, never executed.
#
# Split out so it can be tested without a device. The Phase A run that produced
# results-20260904T163645Z printed "COMPLETE" over a wipe that covered 512 bytes
# of a 7.76 GB stick: the erase engine had already written "purge_achieved":
# false, the verifier had already written "passed": false, the report step had
# already crashed, and the console said none of it. Every function here exists
# so that a failure has to be printed and has to reach the exit status.
#
# The caller defines say/note/warn. Fallbacks are defined here only so the file
# can be sourced on its own by the test suite.

declare -F note >/dev/null || note() { printf '   %s\n' "$*"; }
declare -F warn >/dev/null || warn() { printf '   ! %s\n' "$*"; }

#: One entry per failed phase, "label: reason". The run's exit status.
HARNESS_FAILURES=()

harness_reset() { HARNESS_FAILURES=(); }

# Record a failed phase and say so on the console.
harness_fail() {
    local label="$1" reason="$2"
    HARNESS_FAILURES+=("$label: $reason")
    warn "$label FAILED: $reason"
}

# Print a step's captured stderr. A traceback in a file nobody reads is the
# same as no traceback: A.7 crashed on a key path and the console stayed blank.
harness_dump_err() {
    local err="$1"
    [[ -s "$err" ]] || return 0
    note "--- stderr: $err ---"
    while IFS= read -r line; do note "| $line"; done < "$err"
    note "--- end stderr ---"
}

# Judge one step. Non-zero exit is a failure; so is a zero-byte output file,
# because a step that exits 0 and writes nothing has measured nothing.
harness_check() {
    local label="$1" rc="$2" out="$3" err="$4"
    if [[ "$rc" -ne 0 ]]; then
        harness_fail "$label" "exit $rc"
        harness_dump_err "$err"
        return 1
    fi
    if [[ ! -s "$out" ]]; then
        harness_fail "$label" "exit 0 but $out is zero bytes"
        harness_dump_err "$err"
        return 1
    fi
    # Non-empty is not the same as usable. A step that writes a log line to
    # stdout ahead of its JSON exits 0 and produces a non-empty file that
    # json.loads refuses, and every reader downstream then reports an empty
    # field over a value that was computed correctly. The KEY step did exactly
    # that for 33 minutes of a --full stage:
    #
    #   2026-09-05 15:12:08 [info  ] signing_key_created  path=.../key.pem
    #   {"fingerprint": "27:9F:14:..."}
    #
    # A step whose output cannot be parsed has not succeeded, whatever its exit
    # status said. Checked here so it stops the run at the step that produced
    # it rather than surfacing as an empty string somewhere later.
    if [[ "$out" == *.json ]] && ! harness_json_parses "$out"; then
        harness_fail "$label" "exit 0 but $out is not parsable JSON"
        note "     first line: $(head -c 200 "$out" | head -1)"
        harness_dump_err "$err"
        return 1
    fi
    return 0
}

# True when the file holds one JSON document and nothing else.
harness_json_parses() {
    "$PY" - "$1" <<'PYTHON' 2>/dev/null
import json
import sys

try:
    json.loads(open(sys.argv[1]).read())
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0)
PYTHON
}

# Run one command, capture stdout/stderr to files, and judge it.
#   harness_step <label> <out.json> <err file> <argv...>
harness_step() {
    local label="$1" out="$2" err="$3"
    shift 3
    "$@" > "$out" 2> "$err"
    harness_check "$label" "$?" "$out" "$err"
}

harness_failure_count() { printf '%s' "${#HARNESS_FAILURES[@]}"; }

# Write the failures where the write-up can find them, always - an empty list
# is the evidence that nothing failed, and is worth as much as a full one.
harness_write_failures() {
    local path="$1" first=1
    {
        printf '{"failed_phases": ['
        for item in "${HARNESS_FAILURES[@]}"; do
            [[ $first -eq 1 ]] || printf ', '
            first=0
            printf '"%s"' "${item//\"/\\\"}"
        done
        printf '], "failures": %s}\n' "${#HARNESS_FAILURES[@]}"
    } > "$path"
}

# The last word. Returns non-zero if any phase failed, so the caller's exit
# status carries it.
harness_summary() {
    if [[ ${#HARNESS_FAILURES[@]} -eq 0 ]]; then
        note "all phases completed with no recorded failure"
        return 0
    fi
    warn "${#HARNESS_FAILURES[@]} phase(s) FAILED:"
    for item in "${HARNESS_FAILURES[@]}"; do note "  - $item"; done
    return 1
}

# Count matching lines without the "0\n0" that `grep -c ... || echo 0` produces.
# grep -c prints its count and *then* exits 1 when the count is zero, so the
# fallback fired on top of a perfectly good 0 and A.1 logged "exit 0; 0\n0
# disagreement(s)".
count_matches() {
    local n
    n="$(grep -c -- "$1" "$2" 2>/dev/null || true)"
    printf '%s' "${n:-0}"
}

# Print every field of a VerificationResult, and fail the phase when it did not
# pass. A.5 printed "strategy=full_read" over passed=false and 7388 failing
# offsets, which is the one result this project must never lose.
harness_report_verification() {
    local label="$1" json="$2" text
    text="$("$PY" - "$json" <<'PYTHON'
import json
import sys

document = json.loads(open(sys.argv[1]).read() or "{}")
result = document.get("result") or {}
if not result:
    print("verification: NO RESULT IN OUTPUT")
    raise SystemExit(2)
offsets = result.get("failed_offsets") or []
print(
    "verification: passed={passed} strategy={strategy} "
    "bytes_checked={bytes_checked} sample_count={sample_count} "
    "confidence_bp={confidence_bp}".format(
        passed=result.get("passed"),
        strategy=result.get("strategy"),
        bytes_checked=result.get("bytes_checked"),
        sample_count=result.get("sample_count"),
        confidence_bp=result.get("confidence_bp"),
    )
)
if offsets:
    print(
        f"failed_offsets: {len(offsets)} "
        f"(min {min(offsets)}, max {max(offsets)})"
    )
else:
    print("failed_offsets: 0")
print(f"hw_attested: {result.get('hw_attested')}")
print(f"note: {result.get('probability_note')}")
raise SystemExit(0 if result.get("passed") else 1)
PYTHON
)"
    local rc=$?
    while IFS= read -r line; do note "$line"; done <<< "$text"
    [[ $rc -eq 0 ]] || harness_fail "$label" "verification did not pass"
    return $rc
}

# Print what the erase engine concluded about residual risk, and fail the phase
# when the write did not cover the device. The engine had already written
# "level": "high" and bytes_written: 512; the console said "wipe finished".
harness_report_erase() {
    local label="$1" json="$2" expected_bytes="$3" text
    text="$("$PY" - "$json" "$expected_bytes" <<'PYTHON'
import json
import sys

document = json.loads(open(sys.argv[1]).read() or "{}")
expected = int(sys.argv[2] or 0)
result = document.get("result") or {}
if not result:
    print("erase: NO RESULT IN OUTPUT")
    if document.get("error"):
        print(f"erase error: {document['error']}")
    raise SystemExit(2)

written = int(result.get("bytes_written") or 0)
risk = result.get("residual_risk") or {}
share = (written / expected * 100) if expected else 0.0
print(
    f"erase: method={result.get('method')} level={result.get('level')} "
    f"passes={result.get('passes')} hw_attested={result.get('hw_attested')}"
)
print(f"bytes_written: {written} of {expected} ({share:.2f}% of the device)")
print(
    f"residual risk: {risk.get('level')} "
    f"purge_achieved={risk.get('purge_achieved')}"
)
print(f"residual notes: {risk.get('notes')}")
for item in result.get("limitations") or []:
    print(f"limitation: {item}")
if expected and written < expected:
    print("SHORT WRITE: the erase did not cover the whole device")
    raise SystemExit(1)
raise SystemExit(0)
PYTHON
)"
    local rc=$?
    while IFS= read -r line; do note "$line"; done <<< "$text"
    if [[ $rc -eq 1 ]]; then
        harness_fail "$label" "the erase covered less than the whole device"
    elif [[ $rc -ne 0 ]]; then
        harness_fail "$label" "the erase produced no result"
    fi
    return $rc
}

# Print the tamper/restore table. A.7 crashed before it could produce one and
# the console showed nothing at all, which reads exactly like a step that was
# never reached. The table is the whole point of the step: an altered report
# has to be detected, and a restored one has to verify again.
report_tamper_table() {
    local json="$1" text rc
    text="$("$PY" - "$json" <<'PYTHON'
import json
import sys

document = json.loads(open(sys.argv[1]).read() or "{}")
if "verify_as_written" not in document:
    print("report: NO TAMPER RESULT IN OUTPUT")
    raise SystemExit(2)

print(f"report json: {document.get('json_path')} ({document.get('json_bytes')} bytes)")
print(f"report pdf:  {document.get('pdf_path')} ({document.get('pdf_bytes')} bytes)")
tamper = document.get("tamper") or {}
print(
    f"tampered one byte at offset {tamper.get('offset')}: "
    f"{tamper.get('original_byte')} -> {tamper.get('tampered_byte')}"
)

expected = {"verify_as_written": True, "verify_tampered": False, "verify_restored": True}
bad = []
for key, should_pass in expected.items():
    stage = document.get(key) or {}
    ok = bool(stage.get("ok"))
    verdict = "ok" if ok is should_pass else "UNEXPECTED"
    print(f"{stage.get('label', key):<18} ok={ok!s:<5} {verdict}")
    for check in stage.get("checks") or []:
        status = check.get("status") or "-"
        print(
            f"    {check['name']:<28} passed={check['passed']!s:<5} "
            f"applicable={check['applicable']!s:<5} status={status:<18} "
            f"{check['detail']}"
        )
    if ok is not should_pass:
        bad.append(key)

raise SystemExit(1 if bad else 0)
PYTHON
)"
    rc=$?
    while IFS= read -r line; do note "$line"; done <<< "$text"
    if [[ $rc -eq 1 ]]; then
        harness_fail "A.7 report" "the tamper/restore cycle did not behave as required"
    elif [[ $rc -ne 0 ]]; then
        harness_fail "A.7 report" "the report step produced no tamper result"
    fi
    return $rc
}

# Read one top-level field out of a step's JSON. Counting braces or grepping for
# a key name is how "planted 11 files" got printed for 14 planted files: the
# manifest was keyed by digest and two pairs of identical files collapsed.
harness_json_field() {
    local json="$1" field="$2"
    # An unreadable or unparsable file still yields "" - callers interpolate
    # this into console lines and must not die mid-sentence - but it says so on
    # stderr. Returning "" in silence is how an empty fingerprint got printed
    # over a key that had been created correctly.
    "$PY" - "$json" "$field" <<'PYTHON'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    document = json.loads(open(path).read() or "{}")
except OSError as error:
    print(f"harness_json_field: cannot read {path}: {error}", file=sys.stderr)
    document = {}
except ValueError as error:
    print(f"harness_json_field: {path} is not JSON: {error}", file=sys.stderr)
    document = {}
value = document.get(field)
if value is None:
    print(f"harness_json_field: {path} has no field {field!r}", file=sys.stderr)
print("" if value is None else value)
PYTHON
}

# The fill byte the erase actually wrote, from its own plan, or empty when the
# plan does not name one. Without this the standalone A.5 verify compares the
# medium against the method's default 0x00, and an erase that correctly wrote
# 0xA5 to a zero-eliding controller would be reported as a failed wipe.
harness_erase_fill() {
    local json="$1"
    "$PY" - "$json" <<'PYTHON'
import json
import sys

try:
    document = json.loads(open(sys.argv[1]).read() or "{}")
except (OSError, ValueError):
    document = {}
plan = ((document.get("result") or {}).get("plan")) or {}
fills = plan.get("fill_bytes") or []
print(fills[-1] if fills else "")
PYTHON
}

# Clear the block layer's read-only flag, and confirm it cleared.
#
# core.carve.acquire.apply_write_block sets BLKROSET and deliberately never
# clears it: after an acquisition the source should stay protected. Phase B is
# the case that inverts the assumption - it re-purposes an evidence device as a
# test fixture, and a second invocation (once per filesystem) opens with parted
# and mkfs on a device the first invocation left read-only. Clearing it is
# therefore an explicit, logged step rather than a side effect, and the phase
# fails if it does not take.
harness_clear_write_block() {
    local device="$1" label="$2" before after
    before="$("$PY" - "$device" <<'PYTHON'
import fcntl, os, struct, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY)
except OSError as exc:
    print(f"unreadable:{exc.errno}")
    raise SystemExit(0)
try:
    print(struct.unpack("i", fcntl.ioctl(fd, 0x125E, struct.pack("i", 0)))[0])
except OSError as exc:
    print(f"unsupported:{exc.errno}")
finally:
    os.close(fd)
PYTHON
)"
    if [[ "$before" == "0" ]]; then
        note "write block: $device is already writable, nothing to clear"
        return 0
    fi
    note "write block: $device reads read-only ($before); clearing it because"
    note "  phase B re-purposes this device as a test fixture and must write to it"
    after="$("$PY" - "$device" <<'PYTHON'
import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    fcntl.ioctl(fd, 0x125D, struct.pack("i", 0))
    print(struct.unpack("i", fcntl.ioctl(fd, 0x125E, struct.pack("i", 0)))[0])
except OSError as exc:
    print(f"failed:{exc.errno}")
finally:
    os.close(fd)
PYTHON
)"
    if [[ "$after" == "0" ]]; then
        note "write block: cleared, $device now reads writable"
        return 0
    fi
    harness_fail "$label" "BLKROSET could not be cleared on $device (read back $after)"
    return 1
}

# ==========================================================================
# Resumable multi-step runs
# ==========================================================================
#
# demo-reset.sh --full is a ninety-five-minute job whose steps write to a real
# device. It crashed thirty-three minutes in, at step 3 of 7, and the only way
# to retry was to start again from the thirty-minute pattern write. A run
# declares its steps in order, records each one as it completes, and can be
# told where to pick up.
#
# Resume is explicit, never automatic. A checkpoint file says what finished; it
# cannot say whether the device still holds what that step left behind, and a
# reset that silently assumed so would be staging a demo on a guess.

HARNESS_STEPS=()
HARNESS_RESUME_INDEX=0
HARNESS_RESUME_FROM=""
HARNESS_STEP_FILE=""

#: Declare the ordered steps of a run, and where to record progress.
harness_steps_define() {
    HARNESS_STEPS=("$@")
    HARNESS_RESUME_INDEX=0
    HARNESS_RESUME_FROM=""
}

harness_step_file() { HARNESS_STEP_FILE="$1"; }

harness_steps_list() {
    local name
    for name in ${HARNESS_STEPS[@]+"${HARNESS_STEPS[@]}"}; do printf '%s\n' "$name"; done
}

#: Position of a step in the declared order, or non-zero if it is not one.
harness_step_index() {
    local want="$1" index=0 name
    for name in ${HARNESS_STEPS[@]+"${HARNESS_STEPS[@]}"}; do
        if [[ "$name" == "$want" ]]; then
            printf '%s' "$index"
            return 0
        fi
        index=$((index + 1))
    done
    return 1
}

#: Start at this step. Non-zero when the name is not a declared step, so the
#: caller can print the list rather than running a job the operator did not ask
#: for.
harness_resume_from() {
    local want="$1" index
    index="$(harness_step_index "$want")" || return 1
    HARNESS_RESUME_FROM="$want"
    HARNESS_RESUME_INDEX="$index"
    return 0
}

#: True when this step is at or after the resume point. A step name that was
#: never declared is a programming error, not a step to skip: skipping it
#: silently would drop work from a destructive run.
harness_should_run() {
    local index
    index="$(harness_step_index "$1")" || {
        harness_fail "steps" "undeclared step '$1'"
        return 1
    }
    (( index >= HARNESS_RESUME_INDEX ))
}

harness_checkpoint() {
    [[ -n "$HARNESS_STEP_FILE" ]] || return 0
    printf '%s\n' "$1" > "$HARNESS_STEP_FILE"
}

#: The step a retry should start from: the one after the last that completed.
#: Empty when every step finished.
harness_next_step() {
    local last="" index
    [[ -n "$HARNESS_STEP_FILE" && -s "$HARNESS_STEP_FILE" ]] && last="$(cat "$HARNESS_STEP_FILE")"
    if [[ -z "$last" ]]; then
        printf '%s' "${HARNESS_STEPS[0]:-}"
        return 0
    fi
    index="$(harness_step_index "$last")" || {
        printf '%s' "${HARNESS_STEPS[0]:-}"
        return 0
    }
    printf '%s' "${HARNESS_STEPS[$((index + 1))]:-}"
}
