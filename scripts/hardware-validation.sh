#!/usr/bin/env bash
# First contact with real removable media.
#
# Everything Sanctum has been tested against so far is a loop device or an
# image file. Both are perfect media: no vendor firmware, no USB bridge, no
# wear levelling, no controller deciding on its own where a write lands. This
# script is the run that finds out which of the tool's assumptions survive
# contact with a real stick.
#
# It is deliberately destructive and it says so at every gate. Phase A ends
# with the target device holding nothing.
#
#   ./scripts/hardware-validation.sh --device /dev/sdX \
#       --i-understand-this-destroys-data --phase a
#
# Results land in docs/validation/results-<timestamp>/ as JSON, one file per
# step, written as each step finishes. A crash halfway through therefore still
# leaves everything measured up to that point - which matters, because the
# steps most likely to crash are the ones whose results are most interesting.
set -uo pipefail

# NOT set -e. A step that fails is a finding to record, not a reason to abandon
# the run: the whole point is to learn what real hardware does, and half of
# that is what it does when something goes wrong.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"

DEVICE=""
PHASE="a"
CONFIRMED=0
ALLOW_FIXED=0
ALLOW_LARGE=0
MAX_SANE_BYTES=$((137438953472))   # 128 GiB. A larger "USB stick" is probably a disk.
OUT_ROOT="$REPO/docs/validation"
FS_KIND="fat32"

usage() {
    sed -n '3,19p' "${BASH_SOURCE[0]}" | sed -n 's/^#\( \|$\)//p'
    cat <<'USAGE'

Options:
  --device PATH                        Target block device, e.g. /dev/sdb.
  --i-understand-this-destroys-data    Required. Nothing runs without it.
  --phase a|b|all                      A: erase. B: recovery. Default: a.
  --filesystem fat32|exfat             Phase B filesystem. Default: fat32.
  --allow-fixed                        Permit a non-removable device.
  --allow-large                        Permit a device over 128 GiB.
  --out DIR                            Results directory root.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device) DEVICE="$2"; shift 2 ;;
        --i-understand-this-destroys-data) CONFIRMED=1; shift ;;
        --phase) PHASE="$2"; shift 2 ;;
        --filesystem) FS_KIND="$2"; shift 2 ;;
        --allow-fixed) ALLOW_FIXED=1; shift ;;
        --allow-large) ALLOW_LARGE=1; shift ;;
        --out) OUT_ROOT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done

# --------------------------------------------------------------------------
# Output plumbing
# --------------------------------------------------------------------------

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$OUT_ROOT/results-$STAMP"
LOG=""

say()  { printf '\n\033[1m== %s\033[0m\n' "$*" | tee -a "${LOG:-/dev/null}"; }
note() { printf '   %s\n' "$*" | tee -a "${LOG:-/dev/null}"; }
warn() { printf '\033[33m   ! %s\033[0m\n' "$*" | tee -a "${LOG:-/dev/null}"; }
die()  { printf '\033[31m\nREFUSED: %s\033[0m\n' "$*" >&2; exit 1; }

now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN{printf "%.2f", b-a}'; }

# Step bookkeeping: exit-status checks, the failure list, and the reporters that
# print what a step actually concluded. Sourced rather than inlined so it can be
# tested without a device; see tests/scripts/test_harness_steps.py.
. "$REPO/scripts/harness-steps.sh"

# --------------------------------------------------------------------------
# Safety gates. Every one of these exists because getting it wrong destroys
# something that is not a test fixture.
# --------------------------------------------------------------------------

[[ -n "$DEVICE" ]] || { usage; die "no --device given"; }
[[ -b "$DEVICE" ]] || die "$DEVICE is not a block device"

if [[ $CONFIRMED -ne 1 ]]; then
    die "this script destroys every byte on $DEVICE. Pass --i-understand-this-destroys-data."
fi

KERNEL_NAME="$(basename "$(readlink -f "$DEVICE")")"
SYS="/sys/block/$KERNEL_NAME"
[[ -d "$SYS" ]] || die "$DEVICE has no /sys/block entry; is it a partition rather than a disk?"

# Gate 1: partitions, not disks. Erasing /dev/sdb1 leaves the partition table
# and half the operator's assumptions intact, which is worse than refusing.
if [[ "$DEVICE" =~ [0-9]$ ]] && [[ -e "/sys/class/block/$KERNEL_NAME/partition" ]]; then
    die "$DEVICE is a partition. Point this at the whole disk."
fi

# Gate 2: the running system. The tool refuses this itself; the harness refuses
# it earlier so a bug in the tool cannot be the only thing standing in the way.
ROOT_SRC="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
ROOT_DISK="$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null | head -1 || true)"
if [[ -n "$ROOT_DISK" && "$ROOT_DISK" == "$KERNEL_NAME" ]]; then
    die "$DEVICE holds the running root filesystem."
fi

# Gate 3: anything mounted. Not unmounted automatically: if the operator did
# not know it was mounted, they do not yet know what is on it.
MOUNTS="$(lsblk -nro MOUNTPOINTS "$DEVICE" | grep -v '^$' || true)"
if [[ -n "$MOUNTS" ]]; then
    echo "$MOUNTS" | while read -r point; do echo "   mounted at: $point"; done
    die "$DEVICE has mounted filesystems. Unmount them and confirm you know what they are."
fi

# Gate 4: removable. A fixed disk reached this far by mistake far more often
# than on purpose.
REMOVABLE="$(cat "$SYS/removable" 2>/dev/null || echo 0)"
if [[ "$REMOVABLE" != "1" && $ALLOW_FIXED -ne 1 ]]; then
    die "$DEVICE is not removable ($SYS/removable = $REMOVABLE). Pass --allow-fixed if you are certain."
fi

# Gate 5: size. A 2 TB "USB stick" is a disk somebody plugged in.
SIZE_BYTES="$(lsblk -bdno SIZE "$DEVICE")"
if [[ "$SIZE_BYTES" -gt "$MAX_SANE_BYTES" && $ALLOW_LARGE -ne 1 ]]; then
    die "$DEVICE is $((SIZE_BYTES / 1073741824)) GiB, above the 128 GiB sanity limit. Pass --allow-large if intended."
fi

# --------------------------------------------------------------------------
# Identity, and the second gate: type the serial.
#
# The same two-gate shape the tool itself enforces. The flag says "I meant to
# run a destructive script"; the serial says "I meant to run it against THIS
# device", and only the second one distinguishes the target from the drive
# beside it.
# --------------------------------------------------------------------------

MODEL="$(lsblk -dno MODEL "$DEVICE" | xargs || true)"
SERIAL="$(lsblk -dno SERIAL "$DEVICE" | xargs || true)"
[[ -n "$SERIAL" ]] || SERIAL="$(udevadm info --query=property --name="$DEVICE" 2>/dev/null | sed -n 's/^ID_SERIAL_SHORT=//p')"
TRANSPORT="$(lsblk -dno TRAN "$DEVICE" | xargs || true)"

cat <<IDENTITY

  ------------------------------------------------------------------
   TARGET       $DEVICE
   model        ${MODEL:-<unknown>}
   serial       ${SERIAL:-<unknown>}
   size         $((SIZE_BYTES / 1048576)) MiB ($SIZE_BYTES bytes)
   transport    ${TRANSPORT:-<unknown>}
   removable    $REMOVABLE
  ------------------------------------------------------------------

IDENTITY

# A loud look at what is about to be destroyed. Reported rather than refused:
# validating against a spare installer stick is a perfectly reasonable thing to
# do, and refusing it outright would be the harness overruling the operator.
# But nobody should discover afterwards that this is what it was.
CONTENT_WARN=""
for part in "$DEVICE"?*; do
    [[ -b "$part" ]] || continue
    LABEL="$(lsblk -dno LABEL "$part" 2>/dev/null | xargs || true)"
    FSTYPE="$(lsblk -dno FSTYPE "$part" 2>/dev/null | xargs || true)"
    [[ -n "$LABEL$FSTYPE" ]] && echo "   contains: $part  ${FSTYPE:-?}  ${LABEL:-<no label>}"
    case "$LABEL" in
        CCCOMA*|*Windows*|*WINPE*|*UBUNTU*|*FEDORA*|*DEBIAN*|*ARCH*|*MINT*)
            CONTENT_WARN="yes" ;;
    esac
done
if [[ -n "$CONTENT_WARN" ]]; then
    echo
    warn "This looks like OS installation media. Erasing it destroys the installer."
fi

echo
read -r -p "   Type the serial (${SERIAL:-<unknown>}) to proceed: " TYPED
if [[ "$TYPED" != "$SERIAL" ]]; then
    die "typed serial does not match. Nothing was written."
fi

mkdir -p "$RUN_DIR"
LOG="$RUN_DIR/run.log"
: > "$LOG"

note "results -> $RUN_DIR"
{
    echo "device=$DEVICE"
    echo "model=$MODEL"
    echo "serial=$SERIAL"
    echo "size_bytes=$SIZE_BYTES"
    echo "transport=$TRANSPORT"
    echo "removable=$REMOVABLE"
    echo "started_utc=$STAMP"
    echo "kernel=$(uname -r)"
    echo "host=$(uname -n)"
} > "$RUN_DIR/target.env"

# --------------------------------------------------------------------------
# Tool availability. Recorded, never assumed: a missing PhotoRec turns step 6
# from "zero files recovered" into "nobody looked", and those are not the same
# result.
# --------------------------------------------------------------------------

TOOLS_JSON="$RUN_DIR/00-tools.json"
{
    echo '{'
    printf '  "photorec": "%s",\n' "$(command -v photorec || echo MISSING)"
    printf '  "ewfacquire": "%s",\n' "$(command -v ewfacquire || echo MISSING)"
    printf '  "ewfverify": "%s",\n' "$(command -v ewfverify || echo MISSING)"
    printf '  "hdparm": "%s",\n' "$(command -v hdparm || echo MISSING)"
    printf '  "mkfs_vfat": "%s",\n' "$(command -v mkfs.vfat || echo MISSING)"
    printf '  "mkfs_exfat": "%s",\n' "$(command -v mkfs.exfat || echo MISSING)"
    printf '  "sanctum_python": "%s",\n' "$PY"
    printf '  "euid": %s\n' "$(id -u)"
    echo '}'
} > "$TOOLS_JSON"

HAVE_PHOTOREC=1
command -v photorec >/dev/null || { HAVE_PHOTOREC=0; warn "photorec not installed: the before/after recovery counts cannot be measured"; }
[[ -x "$PY" ]] || die "no venv interpreter at $PY. Run 'make install' first."
[[ "$(id -u)" -eq 0 ]] || warn "not running as root: raw device access will fail"

LEDGER="$RUN_DIR/ledger"
KEYS="$RUN_DIR/keys"
REPORTS="$RUN_DIR/reports"
WORK="$RUN_DIR/work"
mkdir -p "$LEDGER" "$KEYS" "$REPORTS" "$WORK"
export SANCTUM_KEY_PASSPHRASE="${SANCTUM_KEY_PASSPHRASE:-hardware-validation}"

# --------------------------------------------------------------------------
# PhotoRec. Run identically before and after, so the two counts are comparable.
# --------------------------------------------------------------------------

run_photorec() {
    local label="$1" outdir="$RUN_DIR/photorec-$1"
    if [[ $HAVE_PHOTOREC -ne 1 ]]; then
        echo '{"skipped": "photorec is not installed"}' > "$RUN_DIR/photorec-$label.json"
        return 0
    fi
    mkdir -p "$outdir"
    local start; start="$(now)"
    photorec /log /d "$outdir/recup" \
        /cmd "$DEVICE" partition_none,fileopt,everything,enable,search \
        >"$outdir/photorec.out" 2>&1
    local rc=$? elapsed; elapsed="$(since "$start")"
    if [[ $rc -ne 0 ]]; then
        # A non-zero PhotoRec is "nobody looked", not "nothing was recoverable",
        # and the two support opposite conclusions about the wipe.
        harness_fail "A.$([[ "$label" == before ]] && echo 3 || echo 6) photorec $label" "exit $rc"
        harness_dump_err "$outdir/photorec.out"
    fi
    # PhotoRec writes report.xml and its own log beside the recovered files;
    # neither is a recovered file and counting them would inflate both numbers
    # by the same amount and the "after" number by infinitely more. The search
    # is confined to the recup directories for the same reason: the previous
    # count swept up this function's own photorec.out and reported 199 where
    # PhotoRec had recovered 198.
    local count
    count="$(find "$outdir" -path "$outdir/recup*" -type f \
        ! -name 'report.xml' ! -name '*.log' 2>/dev/null | wc -l)"
    {
        printf '{"label": "%s", "returncode": %d, "elapsed_seconds": %s, ' "$label" "$rc" "$elapsed"
        printf '"files_recovered": %d, "command": "photorec /log /d %s/recup /cmd %s partition_none,fileopt,everything,enable,search"}\n' \
            "$count" "$outdir" "$DEVICE"
    } > "$RUN_DIR/photorec-$label.json"
    note "photorec $label: $count files in ${elapsed}s"
}

hash_device() {
    # The whole device, so "the dry run wrote nothing" is a claim about every
    # byte rather than about a sample.
    dd if="$DEVICE" bs=4M status=none 2>/dev/null | sha256sum | cut -d' ' -f1
}

# ==========================================================================
# PHASE A - erase
# ==========================================================================

phase_a() {
    say "PHASE A.1 - enumerate and cross-check"
    harness_step "A.1 enumerate" \
        "$RUN_DIR/a1-enumerate.json" "$RUN_DIR/a1-enumerate.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" enumerate --device "$DEVICE"
    note "$(count_matches '"field"' "$RUN_DIR/a1-enumerate.json") disagreement(s) with lsblk/udev"
    lsblk -O "$DEVICE" > "$RUN_DIR/a1-lsblk-O.txt" 2>&1
    hdparm -I "$DEVICE" > "$RUN_DIR/a1-hdparm-I.txt" 2>&1

    say "PHASE A.2 - write a known pattern and recognisable files"
    local start; start="$(now)"
    # 0xA5 across the whole device first: a byte that is neither 0x00 nor 0xFF,
    # so "the wipe left zeros" cannot be confused with "the wipe did nothing to
    # a device that was already zeroed".
    #
    # Written by the harness rather than by `tr | dd`, which never assembled a
    # full block: dd reading a pipe takes whatever is buffered, so bs=4M bought
    # nothing and 7.76 GB took 1899.76s at 4.08 MB/s. The pattern writer uses
    # the erase path's own geometry and buffer, so this throughput and the
    # erase throughput describe the same device rather than two pipelines.
    harness_step "A.2 pattern" "$RUN_DIR/a2-pattern.json" "$RUN_DIR/a2-pattern.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" pattern --device "$DEVICE" \
        --byte 0xA5
    sync
    note "pattern written in $(since "$start")s"
    note "pattern: $(harness_json_field "$RUN_DIR/a2-pattern.json" bytes_written) bytes at $(harness_json_field "$RUN_DIR/a2-pattern.json" throughput_mib_per_sec) MiB/s, buffer $(harness_json_field "$RUN_DIR/a2-pattern.json" buffer_bytes) bytes, O_DIRECT=$(harness_json_field "$RUN_DIR/a2-pattern.json" o_direct)"

    # Checked, not discarded: without the planted files A.3's before-count is
    # meaningless, and a run whose before-count is meaningless proves nothing
    # about its after-count.
    harness_clear_write_block "$DEVICE" "A.2 write block" || return 1
    if ! parted -s "$DEVICE" mklabel msdos mkpart primary fat32 1MiB 100% \
            > "$RUN_DIR/a2-parted.out" 2>&1; then
        harness_fail "A.2 parted" "partitioning $DEVICE failed"
        harness_dump_err "$RUN_DIR/a2-parted.out"
    fi
    sleep 1
    local part="${DEVICE}1"
    [[ -b "$part" ]] || part="$DEVICE"
    if ! mkfs.vfat -F 32 -n SANCTUMVAL "$part" > "$RUN_DIR/a2-mkfs.out" 2>&1; then
        harness_fail "A.2 mkfs" "mkfs.vfat on $part failed"
        harness_dump_err "$RUN_DIR/a2-mkfs.out"
    fi

    local mnt="$WORK/mnt"; mkdir -p "$mnt"
    if ! mount "$part" "$mnt" > "$RUN_DIR/a2-mount.out" 2>&1; then
        harness_fail "A.2 mount" "could not mount $part; no files were planted"
        harness_dump_err "$RUN_DIR/a2-mount.out"
    fi
    if mountpoint -q "$mnt"; then
        "$PY" - "$mnt" > "$RUN_DIR/a2-plant-count.txt" <<'PLANT'
import sys, io, zipfile, random
from pathlib import Path
from PIL import Image
out = Path(sys.argv[1])
rng = random.Random(20260904)
def noisy(n):
    im = Image.new("RGB", (n, n))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                for _ in range(n * n)])
    return im
for i in range(6):
    b = io.BytesIO(); noisy(160).save(b, "JPEG", quality=95)
    (out / f"photo{i:02d}.jpg").write_bytes(b.getvalue())
for i in range(3):
    b = io.BytesIO(); noisy(96).save(b, "PNG")
    (out / f"shot{i:02d}.png").write_bytes(b.getvalue())
objs = [b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>\nendobj\n"]
for i in range(3):
    o = bytearray(b"%PDF-1.4\n"); offs = []
    for ob in objs:
        offs.append(len(o)); o += ob
    x = len(o); o += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offs: o += b"%010d 00000 n \n" % off
    o += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    (out / f"doc{i:02d}.pdf").write_bytes(bytes(o))
for i in range(2):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>sanctum validation</w:t></w:r></w:p></w:body></w:document>')
        z.writestr("docProps/core.xml", "<coreProperties/>")
    (out / f"memo{i:02d}.docx").write_bytes(b.getvalue())
print(len(list(out.glob("*"))))
PLANT
        harness_step "A.2 hash-tree" \
            "$RUN_DIR/a2-plant.json" "$RUN_DIR/a2-plant.err" \
            "$PY" "$REPO/scripts/hardware_validation.py" hash-tree \
            --root "$mnt" --out "$RUN_DIR/a2-manifest.json"
        sync; umount "$mnt"
        note "planted $(harness_json_field "$RUN_DIR/a2-plant.json" files) files"
        note "  $(harness_json_field "$RUN_DIR/a2-plant.json" unique_digests) distinct digests, $(harness_json_field "$RUN_DIR/a2-plant.json" duplicate_content_files) file(s) duplicating another's content"
    fi

    say "PHASE A.3 - PhotoRec BEFORE (the number without which 'after' proves nothing)"
    run_photorec before

    say "PHASE A.4 - dry run, then the real wipe"
    local before_hash; before_hash="$(hash_device)"
    note "device sha256 before dry run: $before_hash"

    harness_step "A.4 dry run" "$RUN_DIR/a4-dryrun.json" "$RUN_DIR/a4-dryrun.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" erase --device "$DEVICE" \
        --job-id "hwval-dry" --ledger-root "$LEDGER" --key-dir "$KEYS" \
        --dry-run

    local after_dry_hash; after_dry_hash="$(hash_device)"
    note "device sha256 after dry run:  $after_dry_hash"
    {
        printf '{"before": "%s", "after_dry_run": "%s", "unchanged": %s}\n' \
            "$before_hash" "$after_dry_hash" \
            "$([[ "$before_hash" == "$after_dry_hash" ]] && echo true || echo false)"
    } > "$RUN_DIR/a4-dryrun-hashes.json"
    if [[ "$before_hash" == "$after_dry_hash" ]]; then
        note "dry run wrote zero bytes: CONFIRMED"
    else
        harness_fail "A.4 dry run" "the dry run modified the device"
    fi

    local start; start="$(now)"
    harness_step "A.4 erase" "$RUN_DIR/a4-erase.json" "$RUN_DIR/a4-erase.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" erase --device "$DEVICE" \
        --job-id "hwval-real" --ledger-root "$LEDGER" --key-dir "$KEYS"
    note "real wipe finished in $(since "$start")s"
    # What the engine concluded, not just that it returned. It had already
    # written "purge_achieved": false and 512 bytes written when the console
    # said "wipe finished" and the run went on to print COMPLETE.
    harness_report_erase "A.4 erase" "$RUN_DIR/a4-erase.json" "$SIZE_BYTES" || true

    say "PHASE A.5 - verify"
    # Against the byte the erase actually wrote, not the method's default. On a
    # controller that does not program zeros the erase writes 0xA5, and checking
    # 0x00 would fail a good wipe - while on such a controller 0x00 is also the
    # one value the flash translation layer answers for free.
    local expect_fill; expect_fill="$(harness_erase_fill "$RUN_DIR/a4-erase.json")"
    if [[ -n "$expect_fill" ]]; then
        note "verifying against $expect_fill, the fill the erase plan recorded"
        harness_step "A.5 verify" "$RUN_DIR/a5-verify.json" "$RUN_DIR/a5-verify.err" \
            "$PY" "$REPO/scripts/hardware_validation.py" verify --device "$DEVICE" \
            --expect-fill "$expect_fill"
    else
        warn "the erase plan named no fill byte; verifying against the method default"
        harness_step "A.5 verify" "$RUN_DIR/a5-verify.json" "$RUN_DIR/a5-verify.err" \
            "$PY" "$REPO/scripts/hardware_validation.py" verify --device "$DEVICE"
    fi
    harness_report_verification "A.5 verify" "$RUN_DIR/a5-verify.json" || true

    say "PHASE A.6 - PhotoRec AFTER (expect zero)"
    run_photorec after

    say "PHASE A.7 - report, tamper one byte, verify, restore"
    harness_step "A.7 report" "$RUN_DIR/a7-report.json" "$RUN_DIR/a7-report.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" report \
        --job-id "hwval-real" --ledger-root "$LEDGER" --key-dir "$KEYS" \
        --out-dir "$REPORTS" --erase-json "$RUN_DIR/a4-erase.json" \
        --verify-json "$RUN_DIR/a5-verify.json" \
        && report_tamper_table "$RUN_DIR/a7-report.json"

    say "PHASE A - done"
}

# ==========================================================================
# PHASE B - recovery
# ==========================================================================

phase_b() {
    say "PHASE B.1 - populate a $FS_KIND filesystem with known files"
    # An earlier phase-B invocation acquires from this device, and acquisition
    # sets BLKROSET and leaves it set. parted and mkfs below both need a write
    # open, so clear it first, loudly, and stop if it will not clear.
    harness_clear_write_block "$DEVICE" "B.1 write block" || return 1

    local part="${DEVICE}1"
    # Checked, not discarded. These used to fail silently and surface one step
    # later as "could not mount", which points at the wrong thing.
    if ! parted -s "$DEVICE" mklabel msdos mkpart primary fat32 1MiB 100% \
            > "$RUN_DIR/b1-parted.out" 2>&1; then
        harness_fail "B.1 parted" "partitioning $DEVICE failed"
        harness_dump_err "$RUN_DIR/b1-parted.out"
        return 1
    fi
    sleep 1
    [[ -b "$part" ]] || part="$DEVICE"
    local mkfs_rc=0
    if [[ "$FS_KIND" == "exfat" ]]; then
        mkfs.exfat -L SANCTUMVAL "$part" > "$RUN_DIR/b1-mkfs.out" 2>&1 || mkfs_rc=$?
    else
        mkfs.vfat -F 32 -n SANCTUMVAL "$part" > "$RUN_DIR/b1-mkfs.out" 2>&1 || mkfs_rc=$?
    fi
    if [[ $mkfs_rc -ne 0 ]]; then
        harness_fail "B.1 mkfs" "mkfs for $FS_KIND on $part failed"
        harness_dump_err "$RUN_DIR/b1-mkfs.out"
        return 1
    fi

    local mnt="$WORK/mnt-b"; mkdir -p "$mnt"
    if ! mount "$part" "$mnt" > "$RUN_DIR/b1-mount.out" 2>&1; then
        harness_fail "B.1 mount" "could not mount $part for phase B"
        harness_dump_err "$RUN_DIR/b1-mount.out"
        return 1
    fi
    "$PY" - "$mnt" <<'PLANTB'
import sys, io, random
from pathlib import Path
from PIL import Image
out = Path(sys.argv[1]); rng = random.Random(7)
def noisy(n):
    im = Image.new("RGB", (n, n))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                for _ in range(n * n)])
    return im
for i in range(10):
    b = io.BytesIO(); noisy(128 + i * 8).save(b, "JPEG", quality=95)
    (out / f"img{i:02d}.jpg").write_bytes(b.getvalue())
PLANTB
    sync
    harness_step "B.1 hash-tree" \
        "$RUN_DIR/b1-plant-$FS_KIND.json" "$RUN_DIR/b1-plant.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" hash-tree \
        --root "$mnt" --out "$RUN_DIR/b1-manifest-$FS_KIND.json"

    say "PHASE B.2 - delete a subset"
    local deleted=(img00.jpg img02.jpg img04.jpg img06.jpg img08.jpg)
    for name in "${deleted[@]}"; do rm -f "$mnt/$name"; done
    sync; umount "$mnt"
    harness_step "B.2 mark-deleted" \
        "$RUN_DIR/b2-deleted-$FS_KIND.json" "$RUN_DIR/b2-deleted.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" mark-deleted \
        --manifest "$RUN_DIR/b1-manifest-$FS_KIND.json" --names "${deleted[@]}"
    note "deleted ${#deleted[@]} of 10"

    say "PHASE B.3 - acquire to raw and to E01, verify both"
    harness_step "B.3 acquire raw" \
        "$RUN_DIR/b3-acquire-raw-$FS_KIND.json" "$RUN_DIR/b3-acquire-raw.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" acquire --device "$DEVICE" \
        --dest "$WORK/card-$FS_KIND.dd" --fmt raw --ledger-root "$LEDGER" \
        --key-dir "$KEYS" \
        --job-id "hwval-acq-raw-$FS_KIND"
    harness_step "B.3 acquire e01" \
        "$RUN_DIR/b3-acquire-e01-$FS_KIND.json" "$RUN_DIR/b3-acquire-e01.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" acquire --device "$DEVICE" \
        --dest "$WORK/card-$FS_KIND" --fmt e01 --ledger-root "$LEDGER" \
        --key-dir "$KEYS" \
        --job-id "hwval-acq-e01-$FS_KIND"

    say "PHASE B.4 - carve, and compare against the synthetic calibration"
    harness_step "B.4 carve" \
        "$RUN_DIR/b4-carve-$FS_KIND.json" "$RUN_DIR/b4-carve.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" carve \
        --image "$WORK/card-$FS_KIND.dd" \
        --manifest "$RUN_DIR/b1-manifest-$FS_KIND.json" \
        --filesystem "$FS_KIND" --out-dir "$WORK/recovered-$FS_KIND"

    harness_step "B.4 compare" \
        "$RUN_DIR/b4-compare-$FS_KIND.json" "$RUN_DIR/b4-compare.err" \
        "$PY" "$REPO/scripts/hardware_validation.py" compare \
        --carve-json "$RUN_DIR/b4-carve-$FS_KIND.json" \
        --calibration-csv "$REPO/docs/performance/calibration-filesystems.csv"

    say "PHASE B - done"
}

TOTAL_START="$(now)"
case "$PHASE" in
    a)   phase_a ;;
    b)   phase_b ;;
    all) phase_a; phase_b ;;
    *)   die "unknown phase: $PHASE" ;;
esac

printf '{"total_seconds": %s, "phase": "%s"}\n' "$(since "$TOTAL_START")" "$PHASE" \
    > "$RUN_DIR/zz-timing.json"

harness_write_failures "$RUN_DIR/zz-failures.json"

# The exit status carries the findings. The previous run printed COMPLETE over a
# wipe that covered 512 bytes and a report step that crashed, and exited 0.
if harness_summary; then
    say "COMPLETE"
    note "results: $RUN_DIR"
    note "write them up in docs/validation/hardware.md"
    exit 0
fi

say "COMPLETE WITH FAILURES"
note "results: $RUN_DIR"
note "failures: $RUN_DIR/zz-failures.json"
note "write them up in docs/validation/hardware.md"
exit 1
