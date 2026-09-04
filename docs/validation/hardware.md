# Hardware validation — first contact with real removable media

**Status: HARNESS READY, RUN NOT PERFORMED.**

Everything Sanctum has been measured against so far is a loop device or an
image file. Both are perfect media: no vendor firmware, no USB bridge, no wear
levelling, no controller deciding on its own where a write lands. This document
is where the first run against real hardware gets written up.

It contains no hardware numbers yet, because the run could not be performed on
this host. The four reasons are below, each with what it blocks and what would
unblock it. **Nothing in this document is estimated, extrapolated or inferred
from the synthetic runs.** Every results table is empty and stays empty until a
real run fills it.

---

## Why the run did not happen

| # | Blocker | Blocks | Unblocks with |
|---|---|---|---|
| 1 | `sudo` requires a password this session does not have | **Everything.** Raw device access is root-only | An operator running the script directly |
| 2 | PhotoRec is not installed | Phase A steps 3 and 6 | `sudo dnf install testdisk` |
| 3 | No SD card is attached | **All of Phase B** | Plugging one in |
| 4 | The only removable device is a Windows installer | Phase A | A scratch USB stick |

### 1. No raw device access

```
$ dd if=/dev/sda of=/dev/null bs=512 count=1
dd: failed to open '/dev/sda': Permission denied

$ id
uid=1000(v0idsai) gid=1000(v0idsai) groups=1000(v0idsai),10(wheel)

$ ls -l /dev/sda
brw-rw----. 1 root disk 8, 0 /dev/sda
```

The account is in `wheel` but not in `disk`, and `sudo` prompts for a password.
Every step of Phase A and Phase B needs to open a block device, so nothing can
run. This is correct system configuration, not a fault.

### 2. PhotoRec is not installed

```
$ command -v photorec
(nothing)

$ dnf repoquery testdisk
testdisk-7.2
```

It is one `dnf install` away, and that also needs `sudo`.

This matters more than it looks. The brief is right that the after-count proves
nothing without the before-count — but the failure mode is worse than that. If
PhotoRec is missing at step 6, "0 files recovered" and "nobody looked" produce
the same number. The harness therefore writes
`{"skipped": "photorec is not installed"}` rather than a zero, so the
distinction survives into this document.

### 3. No SD card, so Phase B has no target

```
$ ls /dev/mmcblk*
no mmcblk devices

$ ls /sys/block/
loop0  nvme0n1  nvme1n1  sda  zram0
```

`nvme0n1` and `nvme1n1` are the machine's own disks — `/` is on `nvme1n1p1`.
`sda` is the USB stick discussed below. There is no removable card reader
device of any kind, so **Phase B cannot run at all**, and the synthetic-versus-
real comparison that is the entire point of Phase B has no data.

### 4. The only removable device is Windows installation media

```
$ lsblk -f /dev/sda
NAME   FSTYPE LABEL                    SIZE
sda                                    7.2G
└─sda1 ntfs   CCCOMA_X64FRE_EN-US_DV9  7.2G   /run/media/v0idsai/...

$ udevadm info --name=/dev/sda | grep ID_
ID_VENDOR=TOSHIBA
ID_MODEL=TransMemory
ID_SERIAL_SHORT=B103B9C19DE1CCC1BD535ACB
ID_BUS=usb

$ ls /run/media/v0idsai/CCCOMA_X64FRE_EN-US_DV9/
autorun.ico  autorun.inf  System Volume Information
```

`CCCOMA_X64FRE_EN-US_DV9` is the volume label Microsoft's Media Creation Tool
writes. Together with `autorun.inf` this is a Windows installer USB stick.

Phase A ends with the target holding nothing. **Running it here destroys that
installer**, and a bootable installer is not obviously a scratch device.
This was not done. The harness prints a warning for exactly this case and
still permits it — refusing outright would be the harness overruling an
operator who may well have a spare installer they no longer need — but nobody
should discover afterwards that this is what the target was.

---

## What *was* verified

The harness itself, which is worth separating from the hardware run it has not
yet performed. All of the following was executed on this host.

### Safety gates, against real devices

| Gate | Tested against | Result |
|---|---|---|
| Missing `--i-understand-this-destroys-data` | `/dev/sda` | Refused |
| Not a block device | `/etc/hosts` | Refused |
| Holds the running root filesystem | `/dev/nvme1n1` | Refused |
| Has mounted filesystems | `/dev/sda` | Refused, naming the mount point |
| Not removable | — | Not reachable: no fixed disk survives the root-filesystem gate on this host |
| Above the 128 GiB sanity limit | — | Not reachable for the same reason |
| Typed serial must match | — | Not reachable without passing the earlier gates |

The last three gates are unexercised. That is a gap in this document, not a
claim that they work.

### The Sanctum-side steps, against real filesystem images

Every subcommand of `scripts/hardware_validation.py` was run against a real
FAT32 image built by `testkit/fsimage.py` (40 MiB, 10 JPEGs, 5 deleted):

| Step | Result |
|---|---|
| `hash-tree` / `mark-deleted` | 3 files hashed, 2 marked deleted |
| `carve` | 5 candidates, 5 of 5 deleted files recovered byte-identical |
| `compare` | Produced the real-vs-synthetic row correctly |
| `acquire --fmt raw` | 41,943,040 bytes, integrity check passed |
| `acquire --fmt e01` | 41,961,613 bytes on disk (larger than source; see below), integrity passed |
| `report` | 3,748-byte JSON, 38,906-byte PDF, 3 ledger entries |
| `report` tamper cycle | See below |

The tamper cycle behaved exactly as step A.7 requires:

| Report state | `signature` | `fingerprint` | `chain` | `blobs` | overall |
|---|---|---|---|---|---|
| As written | PASS | PASS | PASS | PASS | **PASS** |
| One byte flipped at offset 2165 | **FAIL** | PASS | PASS | PASS | **FAIL** |
| Restored | PASS | PASS | PASS | PASS | **PASS** |

Only the signature check moved. The other three are independent of the report
bytes and correctly stayed put, which is the property that makes reporting them
separately worth doing.

### One harness defect found and fixed while testing it

`structlog` writes to stdout by default, so its log lines interleaved with the
JSON each subcommand emits and made the output unparsable:

```
json.decoder.JSONDecodeError: Extra data: line 1 column 5 (char 4)
```

Logging now goes to stderr. The lines are kept, not silenced: on a real run the
ledger and progress logs are half the evidence about what happened.

This was found by running the harness, not by reading it — which is the same
reason the hardware run matters.

---

## Phase A results — NOT YET MEASURED

### A.1 Enumeration cross-check

| Field | Sanctum | `lsblk -O` | `udevadm` | Agrees? |
|---|---|---|---|---|
| model | | | | |
| serial | | | | |
| size_bytes | | | | |
| transport | | | | |
| rotational | | | | |

| Probe | Result |
|---|---|
| Capability probe | |
| Achievable levels | |
| HPA present | |
| DCO present | |
| Hidden bytes | |

**Disagreements found:** _not measured_

### A.2 Known pattern and planted files

| | |
|---|---|
| Pattern byte | `0xA5` across the whole device |
| Files planted | |
| Filesystem | FAT32 |

`0xA5` rather than `0x00` on purpose: a wipe that leaves zeros over a device
that was *already* zeroed proves nothing, and `0xA5` is neither the wipe
pattern nor the erased-flash pattern.

### A.3 / A.6 PhotoRec, before and after

| Run | Files recovered | Elapsed |
|---|---|---|
| **Before wipe** | | |
| **After wipe** | | |

Both runs use the identical command, recorded in the results JSON:

```
photorec /log /d <out>/recup /cmd <device> partition_none,fileopt,everything,enable,search
```

Expected after: zero. **A non-zero after-count is the single most important
finding this whole validation can produce** and must not be explained away.

### A.4 Dry run writes nothing

| | |
|---|---|
| Device SHA-256 before dry run | |
| Device SHA-256 after dry run | |
| Unchanged | |

Hashed over the whole device, not a sample, so this is a claim about every byte.

### A.4 / A.5 Real wipe and verification

| | |
|---|---|
| Method selected | |
| Bytes written | |
| Elapsed | |
| Throughput (MiB/s) | |
| Phases recorded | |
| Verify strategy | |
| Bytes checked | |
| Sample seed | |
| Verification passed | |

### A.8 Wall-clock per phase

| Phase | Seconds |
|---|---|
| A.1 enumerate | |
| A.2 plant | |
| A.3 photorec before | |
| A.4 dry run | |
| A.4 real wipe | |
| A.5 verify | |
| A.6 photorec after | |
| A.7 report | |
| **Total** | |

---

## Phase B results — NOT YET MEASURED

No SD card is attached to this host, so none of Phase B ran.

### B.3 Acquisition

| Filesystem | Format | Bytes read | On disk | MiB/s | Integrity |
|---|---|---|---|---|---|
| FAT32 | raw | | | | |
| FAT32 | E01 | | | | |
| exFAT | raw | | | | |
| exFAT | E01 | | | | |

Expect the E01 to be **larger** than the source, not smaller: `pyewf` binds no
compression setter and libewf's default is no compression. See
`docs/limitations.md`. Reproduced on this host with the 40 MiB test image:
41,943,040 bytes in, 41,961,613 bytes out.

### B.4 Real media versus the synthetic calibration

**This table is the point of Phase B.** Every confidence number the report
prints comes from weights calibrated on synthetic images. If real-media recall
diverges, the calibration describes something other than reality and every
number inherits the error.

| Filesystem | Real deleted | Real exact | Real recall | Synthetic recall | Δ | Diverges? |
|---|---:|---:|---:|---:|---:|---|
| fat32 | | | | 95.53% | | |
| exfat | | | | 50.00% | | |

Synthetic figures from `docs/performance/calibration-filesystems.csv`. The
harness flags a divergence wider than 10 percentage points, which on a corpus
this size is not noise.

---

## Behaved differently on real hardware than on loop devices

_Nothing to report: the run has not happened._

This section exists to be filled in and is the reason the run is worth doing at
all. Candidates to watch for, from what the code assumes:

- **`hdparm -I` through a USB bridge.** Most USB-SATA bridges do not forward
  ATA pass-through, so the capability probe should report CLEAR only, with a
  limitation naming the bridge. A probe that instead reports PURGE on a USB
  stick is a serious defect.
- **`BLKROSET` on a USB device.** The acquisition path sets the block device
  read-only and reads the flag back. Whether that takes on a USB bridge is
  untested.
- **Write throughput.** Loop devices run at RAM speed. A USB 2.0 stick will be
  two orders of magnitude slower, which is the first real test of the ETA
  arithmetic and the checkpoint interval.
- **`O_DIRECT` alignment.** Real removable media sometimes reports a 512-byte
  logical block over a 4096-byte physical one. The overwrite loop aligns to the
  logical size.
- **Verification sampling.** On a 7 GiB stick the whole device is under the
  64 GiB full-read threshold, so verification should read every block rather
  than sample. The sampled path stays untested until something larger is used.
- **Device disappearing mid-wipe.** A cheap stick that overheats and re-enumerates
  is a real failure mode and exercises `DeviceVanished` for the first time.

---

## How to run it

```bash
sudo dnf install -y testdisk           # PhotoRec, for steps A.3 and A.6

# Phase A, against a SCRATCH usb stick - this destroys everything on it
sudo ./scripts/hardware-validation.sh \
    --device /dev/sdX \
    --i-understand-this-destroys-data \
    --phase a

# Phase B, against an SD card, once per filesystem
sudo ./scripts/hardware-validation.sh --device /dev/mmcblkN \
    --i-understand-this-destroys-data --phase b --filesystem fat32
sudo ./scripts/hardware-validation.sh --device /dev/mmcblkN \
    --i-understand-this-destroys-data --phase b --filesystem exfat
```

The script refuses without the flag, refuses a mounted device, refuses the
system disk, refuses a non-removable device without `--allow-fixed`, and then
asks for the device serial to be typed — the same two-gate shape the tool
itself enforces.

Results land in `docs/validation/results-<timestamp>/` as one JSON file per
step, written as each step finishes, so a crash halfway through still leaves
everything measured up to that point.

Fill this document from those files. Do not fill it from anywhere else.
