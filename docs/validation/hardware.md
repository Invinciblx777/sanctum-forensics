# Hardware validation — first contact with real removable media

**Status: PHASE A RUN AND MEASURED (2026-09-04). PHASE B NOT PERFORMED.**

Everything Sanctum has been measured against so far is a loop device or an
image file. Both are perfect media: no vendor firmware, no USB bridge, no wear
levelling, no controller deciding on its own where a write lands. This document
is where the first run against real hardware gets written up.

Phase A has now been run twice against a real USB stick and its numbers are
below. Phase B still has no target on this host and its tables stay empty.
**Nothing in this document is estimated, extrapolated or inferred from the
synthetic runs.** Every unmeasured table stays empty until a real run fills it,
and every inference that is not a measurement is labelled as one.

| Run | Date | Outcome |
|---|---|---|
| `results-20260904T163645Z` | 2026-09-04 | **Invalid.** The erase covered 512 bytes of the 7.76 GB device. See "The first run was wrong" below. |
| `results-20260904T212839Z` | 2026-09-04 | Valid. The numbers in this document are from this run. |

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

Source: `docs/validation/results-20260904T212839Z/`. Target: Toshiba
TransMemory, serial `B103B9C19DE1CCC1BD535ACB`, 7,759,462,400 bytes, USB,
removable. Total wall clock 3845.37s.

### A.1 Enumeration cross-check

| Field | Sanctum | `lsblk -O` | `udevadm` | Agrees? |
|---|---|---|---|---|
| model | TransMemory | TransMemory | TransMemory | yes |
| serial | B103B9C19DE1CCC1BD535ACB | B103B9C19DE1CCC1BD535ACB | B103B9C19DE1CCC1BD535ACB | yes |
| size_bytes | 7759462400 | 7759462400 | — | yes |
| transport | usb | usb | usb | yes |
| rotational | True | True (`ROTA=1`) | — | yes |

`rotational: True` for a flash stick is the kernel's own answer: this bridge
does not set the non-rotational queue flag, and Sanctum reports what the kernel
says rather than inferring from the device class. It is recorded as an agreement
because it is one; whether the kernel is *right* is a separate question the tool
does not answer.

| Probe | Result |
|---|---|
| Capability probe | `ata_security_erase: false`, `ata_sanitize_ops: []`, `is_sed_opal: false`, `security_frozen: false` |
| Achievable levels | `CLEAR` only |
| HPA present | **Not probed** |
| DCO present | **Not probed** |
| Hidden bytes | Unknown; none detected because none was looked for |

**Disagreements found:** 0.

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
| Elapsed | 1886.75s |
| Throughput | **3.92 MiB/s** |
| Buffer / `O_DIRECT` | 4,194,304 bytes / yes |
| Files planted | 14 (11 distinct digests, 3 files duplicating another's content) |
| Filesystem | FAT32 |

`0xA5` rather than `0x00` on purpose: a wipe that leaves zeros over a device
that was *already* zeroed proves nothing, and `0xA5` is neither the wipe
pattern nor the erased-flash pattern.

The 14/11 split is why the manifest is keyed by path. Three of the planted PDFs
are byte-identical to each other and two of the docx files are byte-identical to
each other; a digest-keyed manifest recorded 11 entries for 14 files, and any
recall denominator taken from that count is 27% too small.

### A.3 / A.6 PhotoRec, before and after

The raw counts, as PhotoRec reported them:

| Run | Files recovered | Elapsed |
|---|---|---|
| **Before wipe** | 198 | 287.17s |
| **After wipe** | 94,720 | 188.73s |

**Neither raw count means what it appears to mean.** Both are dominated by a
signature-carving artefact. Every recovered file was hashed and compared against
the A.2 manifest:

| Run | Files | Byte-identical to a planted file | All-zero 81,920-byte `.dovecot` | Other |
|---|---:|---:|---:|---:|
| Before wipe | 198 | **14** | 184 | 0 |
| After wipe | 94,720 | **0** | 94,720 | 0 |

**The result is 14 of 14 planted files recovered before sanitization, and 0
after.** Those are the numbers that describe the wipe.

#### The artefact

PhotoRec's `dovecot` signature accepts an all-zero 80 KiB block and emits it as a
fixed-size 81,920-byte file, non-overlapping. The count is therefore a property
of the medium's uniformity, not of what was recoverable from it:
`7,759,462,400 / 81,920 = 94,720` exactly. Reproduced on the validation host
with the same PhotoRec build and command:

| Input | Files recovered |
|---|---:|
| 100 MiB of `0x00` | **1280** (= 104,857,600 / 81,920, exactly) |
| 100 MiB of `0xA5` | **0** |
| 81,919 bytes of `0x00` | **0** — a short block does not qualify |
| 122,880 bytes of `0x00` | **1**, of 81,920 bytes — no partial tail |

The before-run's 184 artefacts account for 15,073,280 bytes of zeros, which is
consistent with the two FAT32 file allocation tables `mkfs.vfat` wrote over the
`0xA5` pattern.

Both runs use the identical command, recorded in the results JSON:

```
photorec /log /d <out>/recup /cmd <device> partition_none,fileopt,everything,enable,search
```

A non-zero after-count is still the single most important finding this
validation can produce, and it must be *explained*, not explained away. The
explanation here is a hash comparison against known content, and it is
reproducible in four commands.

#### Independent confirmation that the medium is uniform

The 94,720 recovered files total 7,759,462,400 bytes — exactly the device size —
and every byte of them is `0x00`. PhotoRec's output is therefore a complete
independent read of the medium, and it agrees with A.5's own full read. Two
readers, 100% coverage each, no residual data.

### A.4 Dry run writes nothing

| | |
|---|---|
| Device SHA-256 before dry run | `e779999994791f6e393017f8d2c07a5241204b07e018e7b109e815812f2d7591` |
| Device SHA-256 after dry run | `e779999994791f6e393017f8d2c07a5241204b07e018e7b109e815812f2d7591` |
| Unchanged | **yes** |

Hashed over the whole device, not a sample, so this is a claim about every byte.

### A.4 / A.5 Real wipe and verification

| | |
|---|---|
| Method selected | `SINGLE_PASS_OVERWRITE` |
| Level | `CLEAR` |
| Passes / fill byte | 1 / `0x00` |
| Bytes written | 7,759,462,400 of 7,759,462,400 (**100.00%**) |
| Write elapsed | 521.5s (ledger `erase.preflight.plan` → `erase.erase.complete`) |
| Write throughput | 14.19 MiB/s for `0x00` — **not a write throughput**, see below |
| Step elapsed | 710.8s, of which 189.3s is the engine's own read-back |
| Phases recorded | PREFLIGHT, HIDDEN_AREA_UNLOCK, ERASE, HIDDEN_AREA_RESTORE, VERIFY, REPORT |
| Verify strategy | `full_read` |
| Bytes checked | 7,759,462,400 |
| Sample count / seed | 0 / none — every addressable block was compared |
| Failed offsets | **0** |
| Verification passed | **yes** |
| Verify elapsed | 189.12s (**39.13 MiB/s** read) |
| Residual risk | `medium`, `purge_achieved: false` |

Residual risk stays `medium` with `purge_achieved: false` because the device is
behind a USB bridge with no firmware sanitize, and because HPA/DCO could not be
probed. That is the honest answer for this device, not a defect.

### Write throughput depends on the byte being written

Five writes, same device, all `O_DIRECT` with the same 4 MiB buffer. The `0xFF`
rows were measured afterwards, specifically to test the hypothesis the first
three raised:

| Write | Bytes | Seconds | MB/s | MiB/s |
|---|---:|---:|---:|---:|
| A.4 erase, `0x00`, whole device | 7,759,462,400 | 521.5 | 14.88 | **14.19** |
| A.2 pattern, `0xA5`, whole device | 7,759,462,400 | 1886.75 | 4.11 | 3.92 |
| `0xFF`, 1 GiB, via pipe, `oflag=direct` | 1,073,741,824 | 242.2 | 4.43 | 4.23 |
| `0xFF`, 1 GiB, via `cat`, `oflag=direct` | 1,073,741,824 | 266.2 | 4.03 | 3.85 |
| A.5 read-back, whole device | 7,759,462,400 | 189.12 | 41.03 | **39.13** |

Non-zero fills span **3.85–4.23 MiB/s** — a 10% spread across two fill bytes,
two tools and two transfer sizes. Zeros run **3.6x faster than the fastest of
them**.

**Real write throughput on this device is ~4.0 MiB/s.** That is the number to
publish. **14.19 MiB/s is not a write throughput** and must not appear as one:
it is the rate this controller acknowledges zeros.

**Zero elision, confirmed.** A write that completes faster than the medium can
be programmed was not programmed. The controller either mapped the affected
addresses to a zero token or compressed the all-zero buffer away. Which of the
two is *not* distinguishable from the host and does not change the consequence:
the cells still hold what they held before.

Ruled out, in order of how plausible they were:

- *Content-equality no-op* — the medium held `0xA5` and a FAT32 filesystem when
  the zero pass ran, not zeros.
- *Code-path difference* — both whole-device writes used the same aligned
  `O_DIRECT` buffer, and `dd` reproduces the non-zero rate independently.
- *Run-to-run noise* — a 10% spread against a 260% difference.

### What that means for the erase claim

The **level does not change**. Clear is defined by its threat model: resistance
to recovery through the device's standard interface. Every LBA returns zeros,
verified across the whole address space, so the outcome Clear is defined to
produce is present. Purge was never claimed and is not reachable here.

The **method statement does**. The report named `SINGLE_PASS_OVERWRITE` for what
the device performed as a deallocate, and NIST SP 800-88 Rev.1 does not
recognise a deallocate as a sanitization method.

**No host-side read can establish physical removal on flash.** Every read is
answered by the flash translation layer. A full read-back proves the device now
reports the expected pattern for every addressable block; it cannot prove the
cells were erased, and no verification strategy changes that. This is a property
of the interface, not a weakness in the verifier, and it is now stated in the
report rather than left to be inferred from a passing verification.

Elision is also worse than the general flash caveat, for a different reason. A
performed full-device write consumes the free pool and forces garbage collection
to erase previously-used blocks — the only mechanism by which a host-side
overwrite improves anything on flash. An elided pass programs almost nothing, so
it creates none of that pressure and the old blocks are far more likely to
survive intact.

### `DOD_5220_22_M_3PASS` on this device

The method writes `(0x00, 0xFF, 0x00)`, or `(0xA5, 0xFF, 0xA5)` after the
substitution described below.

| Estimate | Arithmetic | Total |
|---|---|---|
| Naive, all passes at the zero rate | 3 × 521.5 | 1564.5s = **26.1 min** |
| Measured, zero passes elided | 521.5 + 1886.75 + 521.5 | 2929.8s = **48.8 min** |
| Upper bound, every pass programmed | 3 × 1886.75 | 5660.3s = **94.3 min** |

The publishable figure is the **49–94 minute range**, not a single number: the
middle row assumes pass 3's zeros are elided the same way pass 1's were, which
is likely — elision keys off the incoming buffer, not the prior contents — but
is an assumption. Either way it is roughly double what a uniform-rate reading
gives.

### What changed in the tool because of this

- `core/erase/calibrate.py` times a 64 MiB non-zero fill against a 64 MiB zero
  fill over the same region, in PREFLIGHT, after the confirmation gates and
  never in a dry run. A ratio at or above 2.0 records
  `CONTROLLER_WRITE_ELISION` at HIGH severity with the measurement attached.
- `core/erase/patterns.py:select_fills` substitutes `0xA5` for every `0x00` pass
  when elision is measured, so the write is performed and the verification
  checks a pattern the controller had to store — not the one value the FTL
  synthesizes for free. A device wiped this way holds `0xA5` afterwards, and the
  plan says so before the run starts.
- `ErasePlan.est_seconds` is costed per pass at the rate measured for that
  pass's fill byte, with `est_basis` recording where the number came from.

### The residual assessment the run should have produced

`core/erase/verify.py` tested for flash with `flash = not device.rotational`.
This stick reports `rotational: True` — the USB bridge never clears the kernel's
flag, and `lsblk` agrees, so it did not even show up as a disagreement in A.1.
Every flash-specific caveat was therefore skipped for a USB flash stick.

**What the report said:**

```
level: medium   purge_achieved: False
notes: Erase completed and verified within the stated sampling limits.
factor 1: /dev/sda is behind a usb bridge, where ATA pass-through is not
          dependable; HPA/DCO was not probed and hidden sectors, if any, were
          neither detected nor erased.
factor 2: Device is behind a usb bridge; no firmware sanitize could be issued,
          so erasure is limited to what host writes reach.
```

**What the corrected predicate alone produces** — no calibration, just
`core/device/media.py:is_flash`:

```
level: medium
notes: Overwrite on flash cannot reach remapped or over-provisioned blocks, and
       no host-side read can establish physical removal. Use a firmware sanitize
       or crypto-erase where available.
factor 3: Flash media erased by overwrite only. Remapped bad blocks and
          over-provisioned capacity are not host-addressable and cannot be
          reached by any host write pattern. No host-side read can establish
          physical removal on flash: every read is answered by the flash
          translation layer. Determined to be flash because the device is on the
          usb bus (the kernel reports rotational=True, which a usb bridge
          routinely fails to clear; the transport is the stronger signal).
```

**What it should have said, with the elision measurement in hand:**

```
level: high   purge_achieved: False
notes: The controller acknowledged the zero fill far faster than it can program
       this medium, so the cells were not written. Every block reads as zero
       through the device's own interface, which is all a host-side read can
       establish on flash. Use a firmware sanitize or crypto-erase where
       available; otherwise destroy the media.
finding: CONTROLLER_WRITE_ELISION  severity=HIGH  addressable=False
  ratio_bp 36179, threshold_bp 20000, sample_bytes 7759462400,
  zero 0x00 at 14.19 MiB/s, non-zero 0xA5 at 3.92 MiB/s
```

**`medium` to `high`, and a finding that was not there at all.** The
`purge_achieved: False` was already correct; everything else understated what
happened. Both corrected blocks above were produced by feeding this run's stored
`a1-enumerate.json`, `a4-erase.json` and `a5-verify.json` back through
`core.erase.verify.assess_residual_risk`, so they are re-derivable rather than
written by hand.

The two `rotational` sites with the same bug were fixed with it:
`core/erase/drive.py` suppressed the "extra DoD passes buy nothing on flash"
warning for every USB stick, and `core/erase/_platform/posix.py` required
`rotational == "0"` before `ResidualKind.TRIM_REMAP` could fire, so per-file
erasure on removable flash never reported the finding that matters most there.

### A.7 Report, tamper, restore

| Report state | `signature` | `fingerprint` | `chain_integrity` | `chain_store` | `blobs` | overall |
|---|---|---|---|---|---|---|
| As written | PASS | n/a | **FAIL** | — | PASS | **FAIL** |
| One byte flipped at offset 19061 | **FAIL** | n/a | **FAIL** | — | PASS | **FAIL** |
| Restored | PASS | n/a | **FAIL** | — | PASS | **FAIL** |

Tamper detection behaved exactly as required: only the signature moved, PASS →
FAIL → PASS, and the flip was a single byte (`111 → 110`).

The constant `chain_integrity` failure was a defect in the *checker*, not in the
chain. The store verified all 42 entries (`chain_status: VALID`, `0..41`); the
report's embedded excerpt carries genesis plus the real job's 35 entries and
omits the dry run's seqs 1–6, and the checker walked that filtered view as
though every entry were adjacent to the next. `fingerprint_matches_genesis`
reported "no genesis entry was available" about a report whose excerpt carried
genesis at seq 0 — a separate defect, whose real cause was that the ledger was
created before the signing key existed and genesis recorded an empty
fingerprint.

Both are fixed: the excerpt now declares its own gaps, the checker verifies
adjacent pairs only and reports `VERIFIED_COMPLETE` / `VERIFIED_PARTIAL` /
`BROKEN`, the whole chain is re-verified from the store as its own check, the
five genesis situations are reported distinctly, and the harness loads the
signing key before the first ledger append. **This table is from the run as it
happened and will be replaced when A.7 is re-run against the same results
directory.**

### A.8 Wall-clock per phase

| Phase | Seconds |
|---|---:|
| A.1 enumerate | < 1 |
| A.2 pattern write | 1886.98 |
| A.2 plant + hash | ~5 |
| A.3 photorec before | 287.17 |
| A.4 dry run | < 1 |
| A.4 real wipe (write 521.5 + read-back 189.3) | 710.82 |
| A.5 verify | 189.12 |
| A.6 photorec after | 188.73 |
| A.7 report | ~2 |
| **Total** | **3845.37** |

Two thirds of the run is the A.2 pattern write, and per the section above that
is a property of this controller and this fill byte rather than of the tool.

---

## The first run was wrong

`results-20260904T163645Z` reported a completed wipe. It had written 512 bytes.

`hdparm -N` on this USB bridge prints `max sectors = 0/1, HPA setting seems
invalid` **and exits 0**. The hidden-area probe trusted the exit code, parsed
`0/1`, and reported a native max of one sector; the erase path then rebuilt its
geometry from that number and overwrote 512 bytes of a 7,759,462,400-byte
device. Verification correctly reported `passed: false` with 7,388 failing
offsets, and PhotoRec recovered all 14 planted files afterwards — but the
console printed only `strategy=full_read`, then `COMPLETE`, and exited 0.

Four defects, all fixed before the second run:

| Defect | Fix |
|---|---|
| A successful exit treated as a successful probe | Sanity checks on the parsed values, the `HPA setting seems invalid` string detected explicitly, and no HPA probe at all on `usb`/`mmc`. A failed probe returns `probe_failed` with a limitation, never "no hidden area" |
| A hidden-area report allowed to *shrink* the erase geometry | Widen only. `BLKGETSIZE64` is the floor, asserted before ERASE, and `GeometryRefused` is raised rather than erasing part of a device |
| The harness could not surface a failure | Every step's exit status and output file checked, the whole `VerificationResult` and `residual_risk` printed, and a non-zero exit when any phase fails |
| Recall denominators from a digest-keyed manifest | Manifest keyed by path; `files`, `unique_digests` and `duplicate_content_files` all reported |

The harness change is the one that matters most for anything downstream: the
second run's A.7 failure was *reported*, and the script exited 1. The first
run's much larger failure was not.

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

Seven things behaved differently from every loop-device run, and five of
them were defects.

1. **`hdparm -N` through a USB bridge answers, wrongly, and exits 0.** It printed
   `max sectors = 0/1, HPA setting seems invalid`. No loop device can produce
   this, and trusting it cost the first run its entire wipe. The probe is now
   skipped on bridged transports and sanity-checked everywhere else.
2. **PhotoRec on a zeroed device reports one candidate per 80 KiB.** 94,720 of
   them here, none matching any planted file. On a loop device nobody had ever
   pointed a carver at a freshly wiped medium, so the artefact had never
   appeared. It is a property of signature carving on uniform data, not of the
   wipe.
3. **Write throughput depends on the byte written.** 3.92 MiB/s for `0xA5`
   against 14.19 MiB/s for `0x00`, same buffer, same code, same device. Loop
   devices run at RAM speed and hide this completely; the untested hypothesis is
   controller-side zero elision. It changes what a 3-pass estimate should say.
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
   3.6x faster than it programs any non-zero byte. A loop device cannot show
   this: it has no controller. See the throughput section above.

Candidates still to watch for, from what the code assumes. Struck-through
entries were resolved by the run:

- ~~**`hdparm -I` through a USB bridge.**~~ Confirmed: `CLEAR` only, with the
  bridge named in the limitations. The probe did not report PURGE. What was not
  anticipated is that `hdparm -N` would answer with nonsense and exit 0; see
  above.
- **`BLKROSET` on a USB device.** The acquisition path sets the block device
  read-only and reads the flag back. Whether that takes on a USB bridge is
  untested.
- ~~**Write throughput.**~~ Measured: **~4.0 MiB/s** for any byte this
  controller actually programs, against RAM speed on loop devices. The
  14.19 MiB/s figure for `0x00` is elision, not throughput. 29 checkpoints were
  recorded across the 521.5s zero pass, so the checkpoint interval behaved, and
  the ETA now costs each pass at its own fill byte's measured rate.
- ~~**`O_DIRECT` alignment.**~~ Confirmed working: this stick reports a
  512-byte logical block, `O_DIRECT` was accepted for both the pattern write and
  the erase, and no fallback to `O_DSYNC` was recorded. A device reporting a
  4096-byte physical block over a 512-byte logical one is still untested.
- ~~**Verification sampling.**~~ Confirmed: `full_read`, `sample_count: 0`,
  every addressable block compared. The sampled path stays untested until
  something larger than the 64 GiB threshold is used.
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
