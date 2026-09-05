# Hardware validation — first contact with real removable media

**Status: PHASE A COMPLETE AND CLEAN (2026-09-05). PHASE B NOT PERFORMED.**

Everything Sanctum has been measured against so far is a loop device or an
image file. Both are perfect media: no vendor firmware, no USB bridge, no wear
levelling, no controller deciding on its own where a write lands. This document
is where the first run against real hardware gets written up.

Phase A has now been run three times against a real USB stick, and the third run
is clean: every phase passed, no phase was recorded as failed, and the script
exited 0. Its numbers are below. Phase B still has no target on this host and
its tables stay empty.

**Nothing in this document is estimated, extrapolated or inferred from the
synthetic runs.** Every unmeasured table stays empty until a real run fills it,
and every inference that is not a measurement is labelled as one.

| Run | Date | Outcome |
|---|---|---|
| `results-20260904T163645Z` | 2026-09-04 | **Invalid.** The erase covered 512 bytes of the 7.76 GB device and the run printed `COMPLETE`. |
| `results-20260904T212839Z` | 2026-09-04 | Wipe correct, reporting wrong. A.7 failed on a chain check, the elision went undetected, and the flash caveats were missing. |
| `results-20260905T033655Z` | 2026-09-05 | **Clean.** The numbers in this document are from this run, plus a power-cycle re-verification afterwards. |

Nine defects separate the first run from the third. Every one of them was
invisible to the synthetic suite; see "The three runs, and the nine defects
between them" below.

---

## Why Phase B still has not happened

Blockers 1, 2 and 4 below were cleared by an operator: PhotoRec was installed,
a scratch USB stick was supplied, and the script was run under `sudo` directly.
Blocker 3 remains and blocks all of Phase B.

| # | Blocker | Blocks | Status |
|---|---|---|---|
| 1 | `sudo` requires a password this session does not have | Raw device access is root-only | **Cleared** — an operator ran the script |
| 2 | PhotoRec is not installed | Phase A steps 3 and 6 | **Cleared** — `testdisk` installed |
| 3 | No SD card is attached | **All of Phase B** | **Open** — no card reader on this host |
| 4 | The only removable device is a Windows installer | Phase A | **Cleared** — a scratch TransMemory stick was supplied |

The original blocker write-ups are kept below, because the conditions that
produced them are the normal ones on a fresh host.

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

The harness itself, verified before any hardware was attached and kept here
because it is separate evidence from the run. All of the following was executed
on this host, against loop devices and images.

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

## Phase A results — MEASURED

Source: `docs/validation/results-20260905T033655Z/`. Target: Toshiba
TransMemory, serial `B103B9C19DE1CCC1BD535ACB`, 7,759,462,400 bytes, USB,
removable. Total wall clock 5932.07s (98.9 min). `zz-failures.json`:
`{"failed_phases": [], "failures": 0}`.

### A.1 Enumeration cross-check

| Field | Sanctum | `lsblk -O` | `udevadm` | Agrees? |
|---|---|---|---|---|
| model | TransMemory | TransMemory | TransMemory | yes |
| serial | B103B9C19DE1CCC1BD535ACB | B103B9C19DE1CCC1BD535ACB | B103B9C19DE1CCC1BD535ACB | yes |
| size_bytes | 7759462400 | 7759462400 | — | yes |
| transport | usb | usb | usb | yes |
| rotational | True | True (`ROTA=1`) | — | yes |

**Disagreements found: 0.**

`rotational: True` for a flash stick is the kernel's own answer: this bridge
does not set the non-rotational queue flag, and Sanctum reports what the kernel
says rather than inferring from the device class. It is recorded as an agreement
because it is one. Three code paths used to read that flag as a flash test and
all three were wrong; see defect 8.

| Probe | Result |
|---|---|
| Capability probe | `ata_security_erase: false`, `ata_sanitize_ops: []`, `is_sed_opal: false`, `security_frozen: false` |
| Achievable levels | `CLEAR` only |
| HPA / DCO | **Not probed** — `probe_failed`, with the reason recorded |
| Hidden bytes | Unknown; none detected because none was looked for |

The HPA/DCO probe is skipped on USB and MMC transports by design. A bridge's
answer to a SET_MAX query describes the bridge, not the medium behind it, and
nothing in the answer says which. The report carries the limitation verbatim:

> `/dev/sda` is behind a usb bridge, where ATA pass-through is not dependable;
> HPA/DCO was not probed and hidden sectors, if any, were neither detected nor
> erased.

### A.2 Known pattern and planted files

| | |
|---|---|
| Pattern byte | `0xA5` across the whole device |
| Bytes written | 7,759,462,400 (100%) |
| Elapsed | 1910.76s |
| Throughput | **3.87 MiB/s** |
| Buffer / `O_DIRECT` | 4,194,304 bytes / yes |
| Files planted | 14 (11 distinct digests, 3 files duplicating another's content) |
| Filesystem | FAT32 |

`0xA5` rather than `0x00` on purpose: a wipe that leaves zeros over a device
that was *already* zeroed proves nothing. On this device that choice turned out
to matter far more than intended — see the throughput section.

The 14/11 split is why the manifest is keyed by path. Three of the planted PDFs
are byte-identical to each other and two of the docx files are byte-identical to
each other; a digest-keyed manifest recorded 11 entries for 14 files, and any
recall denominator taken from that count is 27% too small.

### A.4 PREFLIGHT write calibration

New in this run, and it decided everything downstream. Two timed fills over the
same 64 MiB region, non-zero first so the zero write cannot be skipped as a
no-op against matching content:

| Fill | Bytes | Seconds | MiB/s |
|---|---:|---:|---:|
| `0xA5` | 67,108,864 | 14.961 | 4.28 |
| `0x00` | 67,108,864 | 4.598 | 13.92 |

**Ratio 3.25, threshold 2.00, `elision_detected: true`.** Cost: 19.6s and
128 MiB of a device about to be erased entirely.

Consequences, all recorded in the plan and the ledger:

| | |
|---|---|
| `fill_bytes` | `["0xA5"]` — substituted for the method's default `0x00` |
| `fill_reason` | the write calibration measured this controller acknowledging a zero fill far faster than it programs the medium, so 0x00 passes were replaced with 0xA5 to force a real program |
| `est_seconds` | 1729 (28.8 min) |
| `est_basis` | measured on this device before the run: 0xA5 at 4.28 MiB/s over 67108864 bytes per sample |

The ETA was **8.3% optimistic**: the write took 1886.3s against an estimate of
1729s, because the 64 MiB sample ran at 4.28 MiB/s and the sustained
whole-device rate is 3.92 MiB/s. That is the right direction to be wrong in only
by accident, and the sample size is the reason. It is worth noting rather than
tuning away: an estimate derived from a 64 MiB sample of a 7.4 GiB write is
going to be a few percent out, and 8% is far better than the 3.6x error a
zero-rate estimate would have produced.

### A.3 / A.6 PhotoRec, before and after

Raw counts, as PhotoRec reported them:

| Run | Files recovered | Elapsed |
|---|---|---|
| **Before wipe** | 198 | 309.46s |
| **After wipe** | **0** | 563.32s |

Every recovered file hashed and compared against the A.2 manifest:

| Run | Files | Byte-identical to a planted file | All-zero 81,920-byte `.dovecot` | Other |
|---|---:|---:|---:|---:|
| Before wipe | 198 | **14** | 184 | 0 |
| After wipe | 0 | **0** | 0 | 0 |

**14 of 14 planted files recovered before sanitization, 0 after.**

#### The after-count fell to zero because the fill changed, not because the wipe improved

This must not be read as an improvement over the previous run. Both wipes that
ran to completion left nothing recoverable: run 2 also recovered 0 of 14, under
94,720 raw candidates.

PhotoRec's `dovecot` signature accepts an all-zero 80 KiB block and emits it as
a fixed-size 81,920-byte file, non-overlapping. Run 2 ended with the medium
holding `0x00`, so the count was `7,759,462,400 / 81,920 = 94,720` exactly — a
property of the medium's uniformity, not of what was recoverable. Run 3 ends
with the medium holding `0xA5`, which produces no candidates at all.

Reproduced on the validation host with the same PhotoRec build and command:

| Input | Files recovered |
|---|---:|
| 100 MiB of `0x00` | **1280** (= 104,857,600 / 81,920, exactly) |
| 100 MiB of `0xA5` | **0** |
| 81,919 bytes of `0x00` | **0** — a short block does not qualify |
| 122,880 bytes of `0x00` | **1**, of 81,920 bytes — no partial tail |

The before-run's 184 artefacts account for 15,073,280 bytes of zeros, which is
consistent with the two FAT32 file allocation tables `mkfs.vfat` wrote over the
`0xA5` pattern. That number is identical in runs 2 and 3.

Both runs use the identical command, recorded in the results JSON:

```
photorec /log /d <out>/recup /cmd <device> partition_none,fileopt,everything,enable,search
```

**The honest slide is 14 → 0, in both runs.** The raw counts, 198 → 94,720 and
198 → 0, are dominated by a carver artefact in opposite directions and neither
describes the wipe.

### A.4 Dry run writes nothing

| | |
|---|---|
| Device SHA-256 before dry run | `138b8cbee0902dcd7b036120750ac2ba0d22740e1089a1dcb2b36165506e2173` |
| Device SHA-256 after dry run | `138b8cbee0902dcd7b036120750ac2ba0d22740e1089a1dcb2b36165506e2173` |
| Unchanged | **yes** |

Hashed over the whole device, not a sample, so this is a claim about every byte.
The calibration writes happen inside the *real* erase's PREFLIGHT, after this
comparison; a dry run never calibrates, and its plan says its fill bytes are
provisional for that reason.

### A.4 / A.5 Real wipe and verification

| | |
|---|---|
| Method selected | `SINGLE_PASS_OVERWRITE` |
| Level | `CLEAR` |
| Passes / fill byte | 1 / **`0xA5`**, substituted by calibration |
| Bytes written | 7,759,462,400 of 7,759,462,400 (**100.00%**) |
| Write elapsed | 1886.3s (ledger `erase.preflight.plan` → `erase.erase.complete`) |
| Write throughput | **3.92 MiB/s** |
| Checkpoints recorded | 29 |
| Step elapsed | 2215.8s, of which 19.6s is calibration and 309.8s the engine's own read-back |
| Phases recorded | PREFLIGHT, HIDDEN_AREA_UNLOCK, ERASE, HIDDEN_AREA_RESTORE, VERIFY, REPORT |
| Verify strategy | `full_read` |
| **Expected fill** | **`0xA5`**, source `--expect-fill`, taken from the erase plan |
| Bytes checked | 7,759,462,400 |
| Sample count / seed | 0 / none — every addressable block was compared |
| Failed offsets | **0** |
| Verification passed | **yes** |
| A.5 verify elapsed | 309.11s (23.93 MiB/s read) |
| Residual level | **high**, `purge_achieved: false` |

Residual risk is `high` rather than `medium` because of the elision finding, not
because anything went wrong with the wipe. Four factors are recorded:

1. the pattern substitution and what the medium holds afterwards;
2. HPA/DCO was not probed through the bridge;
3. no firmware sanitize could be issued through the bridge;
4. overwrite on flash cannot reach remapped or over-provisioned blocks, and no
   host-side read can establish physical removal on flash.

And one structured finding:

```
CONTROLLER_WRITE_ELISION   severity HIGH   addressable false
  ratio_bp 32538   threshold_bp 20000   sample_bytes 67108864
  zero 0x00 at 14,595,162 B/s   non-zero 0xA5 at 4,485,620 B/s
  read_back 25,051,089 B/s
```

### Power cycle: is the Clear outcome durable?

**What was tested.** After the wipe completed and verified, the stick was
unplugged, left out for 30 seconds, replugged, and re-verified end to end:

```
verify --device /dev/sda --expect-fill 0xA5
```

**Result: `passed: true`, `failed_offsets: 0`, `full_read` of all 7,759,462,400
bytes against `0xA5`.**

**Why it matters.** Deterministic-read-after-deallocate is a per-device
property. A SATA drive advertises it (DRAT/RZAT); a USB bridge advertises
nothing, and this host cannot query it. If a controller satisfies reads from a
mapping rather than from the cells, that mapping is a data structure, and a data
structure can fail to survive a power cycle or a firmware quirk — in which case
an LBA that read as sanitized before the cycle could return its old page after
it. That would mean the **Clear outcome itself failed**, which is a far larger
finding than anything else in this document.

It did not. The medium still holds the pattern the erase wrote, byte for byte,
across a power cycle. The Clear outcome is durable on this device.

**What it does not establish.** That the prior contents are gone from the NAND.
Nothing read through the device's own interface can establish that; see below.

An earlier attempt at this test was inconclusive and is recorded here so the
result above is not over-read: the first 2 GB came back non-zero, which was a
leftover `0xFF` region from the throughput measurement rather than surviving
data, and a read at the 5 GB offset returned zeros as expected. The clean Phase A
rerun replaced it and gives the power-cycle check for free.

### Write throughput depends on the byte being written

Every write below used the same aligned `O_DIRECT` path on the same device.

| Write | Where | Bytes | Seconds | MiB/s |
|---|---|---:|---:|---:|
| `0xA5` | run 3, A.2 pattern | 7,759,462,400 | 1910.76 | **3.87** |
| `0xA5` | run 2, A.2 pattern | 7,759,462,400 | 1886.75 | **3.92** |
| `0xA5` | run 3, A.4 erase | 7,759,462,400 | 1886.3 | **3.92** |
| `0xA5` | run 3, calibration | 67,108,864 | 14.96 | 4.28 |
| `0xFF` | 1 GiB via pipe, `oflag=direct` | 1,073,741,824 | 242.2 | 4.23 |
| `0xFF` | 1 GiB via `cat`, `oflag=direct` | 1,073,741,824 | 266.2 | 3.85 |
| `0x00` | run 2, A.4 erase | 7,759,462,400 | 521.5 | **14.19** |
| `0x00` | run 3, calibration | 67,108,864 | 4.60 | **13.92** |

**Published write throughput: ~4.0 MiB/s.** Every byte this controller actually
programs lands between 3.85 and 4.28 MiB/s, across three fill values, two tools,
three transfer sizes and three runs.

**14.19 MiB/s is not a write throughput and must never be quoted as one.** It is
the rate this controller acknowledges zeros, reproduced at 13.92 MiB/s on a
64 MiB sample in an independent run. The ratio between the two is 3.25-3.62
depending on sample size.

#### Reads are faster on zeros too

A correction to what this document said after run 2. Read-back rates:

| Read | Medium holds | Seconds | MiB/s |
|---|---|---:|---:|
| run 2, A.5 | `0x00` | 189.12 | **39.13** |
| run 3, engine VERIFY | `0xA5` | 309.8 | 23.89 |
| run 3, A.5 | `0xA5` | 309.11 | 23.93 |

Run 2's 39.13 MiB/s was published here as "the only figure that describes the
medium without qualification". Run 3 falsifies that: the same device reads
`0xA5` at **23.9 MiB/s**, and the two independent reads in run 3 agree to within
0.2%. **Published read throughput: ~23.9 MiB/s.**

Reading zeros is 1.64x faster than reading real data on this controller. The
straightforward reading is that the FTL answers a deallocated or zero-mapped
block without touching NAND on the read path as well as the write path. That is
consistent with the write measurement and with nothing else observed here, but
it is an inference from timing, not a proven mechanism, and it is recorded as
one.

### What that means for the erase claim

The **level does not change**. Clear is defined by its threat model: resistance
to recovery through the device's standard interface. Every LBA returns the
pattern the erase wrote, verified across the whole address space and again after
a power cycle. Purge was never claimed and is not reachable here.

What the elision changed is the **method statement**. Run 2's report named
`SINGLE_PASS_OVERWRITE` for what the device performed as a deallocate, and NIST
SP 800-88 Rev.1 does not recognise a deallocate as a sanitization method. Run 3
writes `0xA5`, which this controller has to program, so the method named is the
method performed.

**No host-side read can establish physical removal on flash.** Every read is
answered by the flash translation layer. A full read-back proves the device now
reports the expected pattern for every addressable block; it cannot prove the
cells holding the prior contents were erased, and no verification strategy —
full, sampled, seeded, repeated, or repeated across a power cycle — changes
that. This is a property of the interface, not a weakness in the verifier, and
the report states it rather than leaving a reader to infer it from a pass.

Elision is also worse than the general flash caveat, for a different reason. A
performed full-device write consumes the free pool and forces garbage collection
to erase previously-used blocks — the only mechanism by which a host-side
overwrite improves anything on flash. An elided pass programs almost nothing, so
it creates none of that pressure. Run 3's wipe programmed 7.4 GiB; run 2's
programmed almost none of it.

### `DOD_5220_22_M_3PASS` on this device

The method writes `(0x00, 0xFF, 0x00)`, or `(0xA5, 0xFF, 0xA5)` after
substitution.

| Estimate | Arithmetic | Total |
|---|---|---|
| Naive, all passes at the zero rate | 3 × 521.5 | 1564.5s = **26.1 min** |
| Zero passes elided | 521.5 + 1886.75 + 521.5 | 2929.8s = **48.8 min** |
| Every pass programmed, which is what the tool now does | 3 × 1886.3 | 5658.9s = **94.3 min** |

With the substitution in place the bottom row is the operative one: all three
passes are programmed, so the method costs **~94 minutes** on this device
against the ~26 a uniform-rate reading gives. `est_basis` records that the
number came from a measurement rather than a manufacturer's claim.

### A.7 Report, tamper, restore

| Report state | `signature` | `fingerprint` | `chain_integrity` | `chain_store` | `blobs` | overall |
|---|---|---|---|---|---|---|
| As written | PASS | PASS (`OK`) | PASS (`VERIFIED_PARTIAL`) | PASS (`VALID`) | PASS | **PASS** |
| One byte flipped at offset 19636 | **FAIL** | PASS | PASS | PASS | PASS | **FAIL** |
| Restored | PASS | PASS (`OK`) | PASS (`VERIFIED_PARTIAL`) | PASS (`VALID`) | PASS | **PASS** |

true / false / true, and **only the signature moved**. The flip was one byte,
`111 → 110`.

- `fingerprint_matches_genesis` reads `OK`: the harness now loads the signing
  key before the first ledger append, so genesis records a real fingerprint.
  Runs 1 and 2 reported `no genesis entry was available` about reports whose
  excerpt carried genesis at seq 0.
- `chain_integrity` reads `VERIFIED_PARTIAL`: 37 excerpt entries hash correctly,
  all 35 adjacent pairs link, span 0..42, and the 6 entries at seq 1-6 belonging
  to the dry-run job are named as not carried. The report declares that gap
  under its own signature: `excerpt_gaps: [{"from_seq": 1, "to_seq": 6,
  "count": 6}]`.
- `chain_store` re-verifies the whole chain independently of the excerpt and of
  the `chain_status` the report prints about itself: **all 43 entries verify,
  0..42.**

### A.8 Wall-clock per phase

| Phase | Seconds |
|---|---:|
| A.1 enumerate | < 1 |
| A.2 pattern write | 1910.76 |
| A.2 plant + hash | ~5 |
| A.3 photorec before | 309.46 |
| A.4 dry run | < 1 |
| A.4 write calibration | 19.6 |
| A.4 real wipe (write 1886.3 + read-back 309.8) | 2215.82 |
| A.5 verify | 309.11 |
| A.6 photorec after | 563.32 |
| A.7 report | ~2 |
| **Total** | **5932.07** |

98.9 minutes against run 2's 64.1. The difference is almost entirely the erase
write going from 521.5s to 1886.3s, because it is now actually writing. **That
is the fix working, not a regression.**

---

## The three runs, and the nine defects between them

The first run reported a completed wipe having written 512 bytes. The third
wiped every byte and reported every caveat. Nothing between them was a change of
intent — the same code path, the same device, the same command.

| | Run 1 `163645Z` | Run 2 `212839Z` | Run 3 `033655Z` |
|---|---|---|---|
| Bytes written | **512** (0.0000066%) | 7,759,462,400 (100%) | 7,759,462,400 (100%) |
| Fill written | `0x00` | `0x00` | **`0xA5`** by calibration |
| Write programmed by the controller? | no | **no** | **yes** |
| HPA/DCO | "1 native sector", believed | not probed, said so | not probed, said so |
| Verification | `passed: false`, 7388 bad offsets | `passed: true` against `0x00` | `passed: true` against `0xA5` |
| Verification printed? | **no**, only `strategy=full_read` | in full | in full |
| PhotoRec, planted-hash matches | 14 before / **14 after** | 14 before / 0 after | 14 before / 0 after |
| PhotoRec, raw counts | 199 / 199 | 198 / 94,720 | 198 / **0** |
| Residual level | `high` (verification failed) | `medium` | `high` (elision finding) |
| Flash caveats present? | no | **no** | yes |
| Elision finding | — | — | `CONTROLLER_WRITE_ELISION` HIGH |
| A.7 tamper table | **crashed, printed nothing** | printed, `chain_integrity` FAIL ×3 | true / false / true, all checks pass |
| Console verdict | `COMPLETE` | `COMPLETE WITH FAILURES` | `COMPLETE` |
| Exit status | **0** | 1 | 0 |
| Wall clock | 3666.89s | 3845.37s | 5932.07s |

Run 1's `COMPLETE` and exit 0 over a 512-byte wipe is the single worst outcome
this project can produce, and it is the reason the harness changes matter more
than any individual erase fix: **run 2's much smaller failure was reported, and
run 1's much larger one was not.**

### The nine defects

| # | Symptom | Mechanism | Fix |
|---|---|---|---|
| 1 | Erase covered 512 bytes of a 7.76 GB device | `hdparm -N` on a USB bridge prints `max sectors = 0/1, HPA setting seems invalid` **and exits 0**. The probe trusted the exit code and the regex match, and reported a native max of one sector | `core/device/hidden_areas.py` sanity-checks parsed values against the kernel size, detects the invalid-HPA string explicitly, and skips the probe entirely on `usb`/`mmc`. A failed probe returns `probe_failed` with a reason, never "no hidden area" |
| 2 | Geometry silently shrank to 512 bytes | The unlock branch rebuilt `Geometry` from `native_max_sectors × block_size` unconditionally | `core/erase/drive.py` widens only. `BLKGETSIZE64` is the floor, asserted before ERASE; `GeometryRefused` is raised rather than erasing part of a device |
| 3 | Console printed `COMPLETE`, exit 0, over a failed wipe | No step checked an exit status or an output file, and no phase could record a failure | `scripts/harness-steps.sh`: every step judged on exit status *and* a non-empty output file, stderr printed on failure, `zz-failures.json` always written, non-zero exit when any phase failed |
| 4 | A.5 printed `strategy=full_read` over `passed: false` and 7388 failing offsets | One `sed` extracting one field | `harness_report_verification` prints the whole `VerificationResult` and fails the phase when it did not pass. `harness_report_erase` does the same for `bytes_written` and `residual_risk` |
| 5 | A.7 crashed and the console stayed blank | `load_or_create_key` was handed a directory by both real callers and stat-ed it for 0600, then would have read it as PEM | `key_file_for` resolves a directory to `<dir>/sanctum-signing.key.pem`; `KeyPathUnusable` replaces `IsADirectoryError` |
| 6 | `chain_integrity` FAIL in all three tamper stages, for a store that verified all 42 entries | The excerpt carries one job's entries plus genesis. `_check_chain` walked it as though contiguous and called the first entry after the gap a broken link | Links checked only between adjacent `seq`; gaps named, not failed; three outcomes `VERIFIED_COMPLETE` / `VERIFIED_PARTIAL` / `BROKEN`; `excerpt_gaps` declared under the signature and cross-checked; new `chain_store` check re-verifies the whole chain from the store |
| 7 | "no genesis entry was available" about a report carrying genesis at seq 0 | Five distinct causes all returned `None` and printed the first one's message | Each cause reports itself (`GENESIS_ABSENT`, `NO_LEDGER_ROOT`, `BLOB_MISSING`, `BLOB_UNPARSABLE`, `FINGERPRINT_EMPTY`). The harness loads the signing key before the first ledger append, so genesis records a real fingerprint; a chain started without one records `NO_SIGNING_KEY`, not `""` |
| 8 | No flash caveat in the report of a USB flash stick | `flash = not device.rotational`, in three places, and a USB bridge does not clear `queue/rotational`. It also suppressed the DoD "extra passes buy nothing on flash" warning and blocked `TRIM_REMAP` for files on removable flash | `core/device/media.py:is_flash` makes a positive determination from calibration, transport, flag or model string, and returns the deciding signal so the report says how it knew |
| 9 | Zero fill acknowledged 3.25-3.62x faster than the medium can be programmed | The controller maps an all-zero write to a token or compresses it away. The cells keep their prior contents, the verification compares against the one value the FTL synthesizes for free, and the report names an overwrite the device performed as a deallocate | `core/erase/calibrate.py` times a non-zero fill against a zero fill in PREFLIGHT; at or above 2.0 it records `CONTROLLER_WRITE_ELISION` at HIGH with the measurement. `patterns.select_fills` substitutes `0xA5`, verification checks that pattern, and the ETA costs each pass at its own fill's measured rate |

**None of these was caught by the synthetic suite**, which was green throughout
at 757 tests. Loop devices have no controller, no bridge and no FTL: defects 1,
8 and 9 are unreachable on one. Defects 3, 4 and 5 needed a step to fail, and on
a loop device none does. Defects 6 and 7 needed two jobs on one ledger and a key
created after the chain, and every synthetic run had one job and a fixture key.

Every one of them now has a regression test that fails without the fix.

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

Eight things behaved differently from every loop-device run, and five of them
were defects. Two more were confirmed only by the third run.

1. **`hdparm -N` through a USB bridge answers, wrongly, and exits 0.** It printed
   `max sectors = 0/1, HPA setting seems invalid`. No loop device can produce
   this, and trusting it cost the first run its entire wipe. The probe is now
   skipped on bridged transports and sanity-checked everywhere else.
2. **PhotoRec on a zeroed device reports one candidate per 80 KiB.** 94,720 of
   them in run 2, none matching any planted file; 0 in run 3, because the medium
   ends holding `0xA5`. On a loop device nobody had ever pointed a carver at a
   freshly wiped medium, so the artefact had never appeared. It is a property of
   signature carving on uniform data, not of the wipe.
3. **Write throughput depends on the byte written.** ~4.0 MiB/s for any byte the
   controller programs against 13.92-14.19 MiB/s for `0x00`, same buffer, same
   code, same device. Loop devices run at RAM speed and hide this completely. It
   roughly doubles what a 3-pass estimate should say.
4. **The report's chain check cannot handle a filtered excerpt.** Every
   loop-device run had a single job on a fresh ledger, so the excerpt was always
   contiguous and the whole-chain rule always held. The first run with two jobs
   on one ledger broke it in all three tamper stages.
5. **Genesis records the signing key's fingerprint, and the key did not exist
   yet.** Again invisible with one job and a fixture-built ledger.
6. **`rotational` is `True` for a flash stick, and three code paths believed
   it.** The bridge does not set the kernel's non-rotational flag, and `lsblk`
   agrees, so it never showed up as a disagreement. `flash = not
   device.rotational` was the flash test in the residual assessment, in the DoD
   pass warning, and (as `rotational == "0"`) in the per-file TRIM detection.
   All three silently skipped their flash caveats on the one class of device
   most likely to need them. Replaced by `core/device/media.py:is_flash`, which
   makes a positive determination and returns the signal that decided it.
7. **Zeros are not written at all.** The controller acknowledges a zero fill
   3.25-3.62x faster than it programs any non-zero byte, reproduced across two
   runs and three sample sizes. A loop device cannot show this: it has no
   controller.
8. **Zeros are not *read* from the medium either.** The same device reads `0x00`
   at 39.13 MiB/s and `0xA5` at 23.9 MiB/s, with two independent reads of the
   `0xA5` medium agreeing to 0.2%. Consistent with the FTL answering a
   zero-mapped block without touching NAND on the read path; recorded as an
   inference from timing, not a proven mechanism.

Candidates still to watch for, from what the code assumes. Struck-through
entries were resolved by the run:

- ~~**`hdparm -I` through a USB bridge.**~~ Confirmed: `CLEAR` only, with the
  bridge named in the limitations. The probe did not report PURGE. What was not
  anticipated is that `hdparm -N` would answer with nonsense and exit 0; see
  above.
- **`BLKROSET` on a USB device.** The acquisition path sets the block device
  read-only and reads the flag back. Whether that takes on a USB bridge is
  untested.
- ~~**Write throughput.**~~ Measured: **~4.0 MiB/s** write and **~23.9 MiB/s**
  read for any byte this controller actually handles, against RAM speed on loop
  devices. The 14.19 MiB/s write and 39.13 MiB/s read figures for `0x00` are
  elision, not throughput. 29 checkpoints were recorded across the 1886.3s write
  in run 3, so the checkpoint interval behaved, and the ETA now costs each pass
  at its own fill byte's measured rate — 8.3% optimistic on a 64 MiB sample.
- ~~**`O_DIRECT` alignment.**~~ Confirmed working: this stick reports a
  512-byte logical block, `O_DIRECT` was accepted for both the pattern write and
  the erase, and no fallback to `O_DSYNC` was recorded. A device reporting a
  4096-byte physical block over a 512-byte logical one is still untested.
- ~~**Verification sampling.**~~ Confirmed: `full_read`, `sample_count: 0`,
  every addressable block compared. The sampled path stays untested until
  something larger than the 64 GiB threshold is used.
- ~~**Data surviving a power cycle.**~~ Not on the original list, and it should
  have been. A controller that satisfies reads from a mapping rather than from
  cells could lose that mapping across a power cycle. Tested after run 3:
  unplug, 30s, replug, full re-verify against `0xA5` — `passed: true`,
  `failed_offsets: 0`. The Clear outcome is durable on this device.
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

Phase A now exits non-zero when any phase failed and writes `zz-failures.json`
either way, so `echo $?` is a usable verdict.

### The power-cycle check

After Phase A, unplug the device, wait 30 seconds, replug it, and re-verify
against the byte the erase actually wrote — which is in `plan.fill_bytes` of
`a4-erase.json`, and is **not** `0x00` on a controller that elides zeros:

```bash
FILL=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["result"]["plan"]["fill_bytes"][-1])' \
        docs/validation/results-<timestamp>/a4-erase.json)

sudo .venv/bin/python scripts/hardware_validation.py verify \
    --device /dev/sdX --expect-fill "$FILL" | tee a5-postcycle.json \
  | python3 -c 'import json,sys;r=json.load(sys.stdin)["result"];print("passed:",r["passed"],"failed_offsets:",len(r["failed_offsets"]))'
```

`verify` exits 0 even when verification fails — a disappointing measurement is a
finding, not an error — so read `.result.passed` rather than the exit status.

Results land in `docs/validation/results-<timestamp>/` as one JSON file per
step, written as each step finishes, so a crash halfway through still leaves
everything measured up to that point. They are gitignored: one run is 7.3 GB of
PhotoRec output, and its ledger and signing key are root-owned 0600 by design.

Fill this document from those files. Do not fill it from anywhere else.
