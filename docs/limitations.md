# Limitations

Every guarantee this tool cannot make, stated plainly. If something here reads
as uncomfortable, that is the point: an operator who over-trusts a wipe is worse
off than one who knows exactly what it did and did not cover.

## The ATA security-erase password is fixed and published

`core/erase/drive.py` uses a fixed password for the ATA SECURITY ERASE sequence:

```
ATA_RECOVERY_PASSWORD = "SanctumForensics"
```

The sequence is `SECURITY SET PASSWORD` → `SECURITY ERASE PREPARE` →
`SECURITY ERASE UNIT`. Between the first and last step the drive is
password-locked. If the process dies in that window — crash, `SIGKILL`, power
cut — **the drive stays locked and is unusable until the password is cleared.**

Three mitigations, all in code:

1. The password is written to the ledger *before* `SET PASSWORD` is issued, so
   the recovery value survives the crash that makes it necessary.
2. A `SIGINT`/`SIGTERM` handler and an `atexit` hook both attempt
   `SECURITY DISABLE PASSWORD`.
3. The command refuses to start on a frozen drive.

If a drive is left locked, clear it by hand:

```bash
hdparm --user-master u --security-disable SanctumForensics /dev/sdX
```

A fixed, published value is a deliberate trade. The password protects nothing —
it is a transient precondition of the erase command, not a secret — and making
it recoverable matters far more than making it unguessable.

## DoD 5220.22-M's third pass is not random here

The historical sequence is a character, its complement, then a **random**
character. A random final pass cannot be read back and checked, and an
unverifiable erase is not something this project will report as verified. The
third pass is therefore a fixed zero character, keeping the
character/complement/character shape and leaving a result
`core/erase/verify.py` can actually verify.

On a device whose controller does not program zeros, both zero passes become
`0xA5` — see "Some controllers do not program a zero fill at all" below. That
loses the character/complement/character shape, which is worth less than passes
that actually reach the medium. The plan records the fill bytes and the reason.

The engine implements the method because operators are sometimes contractually
required to name it. It is **not offered in the UI or the API**: the erase request
carries a level only, so a screen control naming DoD could not be honoured, and an
earlier build that showed one produced a certificate contradicting the confirmation
dialog. It is reachable only by calling `core/erase/drive.py:execute` with
`EraseJob.method` set. NIST SP 800-88r2 (September 2025) states that multi-pass
overwrite is not needed for clear and calls the DoD 5220.22-M pass-count language
obsolete (Appendix D); for SSDs with over-provisioning it says such practices
should be avoided, as very little confidentiality protection is achieved
(Sec. 3.1.1). This tool measures no benefit over a single pass, and on flash
media every extra pass burns program/erase cycles without reaching a single
remapped or over-provisioned block.

## Overwrite cannot reach all of a flash device

A host overwrite only reaches host-addressable LBAs. On SSDs, eMMC and USB
flash, these are unreachable by any write pattern:

- blocks the FTL has remapped after wear or failure,
- over-provisioned capacity never exposed to the host,
- data still live in the write cache or in an unmapped erase block,
- cells the controller never programmed because it elided the write — see the
  next section.

Only a firmware sanitize or a cryptographic erase covers those. Where neither is
available, the result is a **Clear**, not a **Purge**, and the report says so.

**No host-side read can establish physical removal on flash.** Every read is
answered by the flash translation layer, which decides what a logical block
returns. A full read-back proves that the device now reports the expected
pattern for every addressable block. It cannot prove that the cells holding the
prior contents were erased, and no verification strategy — full, sampled,
seeded, repeated — changes that. The report states this rather than leaving a
reader to infer it from a passing verification.

## Some controllers do not program a zero fill at all

Measured on a Toshiba TransMemory USB stick across three hardware-validation
runs. Every write used `O_DIRECT` with the same 4 MiB buffer, on the same
device:

| Write | Bytes | Seconds | MiB/s |
|---|---:|---:|---:|
| `0x00`, whole device | 7,759,462,400 | 521.5 | **14.19** |
| `0x00`, 64 MiB calibration | 67,108,864 | 4.60 | **13.92** |
| `0xA5`, whole device (×3) | 7,759,462,400 | 1886.3-1910.8 | 3.87-3.92 |
| `0xA5`, 64 MiB calibration | 67,108,864 | 14.96 | 4.28 |
| `0xFF`, 1 GiB, via pipe | 1,073,741,824 | 242.2 | 4.23 |
| `0xFF`, 1 GiB, via `cat` | 1,073,741,824 | 266.2 | 3.85 |

Every byte the controller actually programs lands between 3.85 and 4.28 MiB/s,
across three fill values, two tools, three transfer sizes and three runs. Zeros
run 3.25-3.62x faster than any of them. A write that completes faster than the
medium can be programmed was not performed: the controller mapped the addresses
to a zero token, or compressed the all-zero buffer away. Which of the two is not
distinguishable from the host and does not matter — either way the cells still
hold what they held before.

Reads show the same asymmetry. The same device reads `0x00` at 39.13 MiB/s and
`0xA5` at 23.89-23.93 MiB/s, two independent reads of the `0xA5` medium agreeing
to 0.2%. Consistent with the FTL answering a zero-mapped block without touching
NAND on the read path too; recorded as an inference from timing rather than a
proven mechanism. **Published throughput for this class of device: ~4.0 MiB/s
write, ~23.9 MiB/s read.**

This is worse than the general flash caveat above, and for a different reason.
There, the write happened and could not reach everything. Here the write did not
happen, so it created none of the free-block pressure that forces garbage
collection to erase the old blocks — which is the only mechanism by which a
host-side overwrite improves anything on flash.

`core/erase/calibrate.py` measures this before every software overwrite: one
64 MiB non-zero fill and one 64 MiB zero fill over the same region, timed. A
ratio at or above 2.0 records `CONTROLLER_WRITE_ELISION` at HIGH severity with
the measurement attached, and `core/erase/patterns.py` substitutes `0xA5` for
every `0x00` pass so the write is actually performed and the verification checks
a pattern the controller had to store. **A device wiped this way holds `0xA5`
afterwards, not zeros.** The plan states the fill bytes before the run starts.

The substitution is a truthfulness fix before it is a security one: the report
names `SINGLE_PASS_OVERWRITE`, and a controller that elides wrote nothing. NIST
SP 800-88r2 Sec. 3.1.1 describes overwrite as replacing target data with
non-sensitive data, and an elided write replaced nothing. The Clear *outcome*
still holds — every block reads as zero through the device's own interface,
which is what clear is defined to protect against — but the method statement
would have been false.

### What this does to a three-pass estimate

`DOD_5220_22_M_3PASS` writes `(0x00, 0xFF, 0x00)`, or `(0xA5, 0xFF, 0xA5)` after
substitution. Costing every pass at the zero rate is what makes a naive estimate
wrong by a factor of two:

| Estimate | Arithmetic | Total |
|---|---|---|
| Naive, all passes at the zero rate | 3 × 521.5 | 1564.5s = **26.1 min** |
| Measured, zero passes elided | 521.5 + 1886.75 + 521.5 | 2929.8s = **48.8 min** |
| Upper bound, every pass programmed | 3 × 1886.75 | 5660.3s = **94.3 min** |

`core/erase/calibrate.py:estimate_seconds` costs each pass at the rate measured
for the byte that pass writes, and `ErasePlan.est_basis` records where the
number came from. An operator must not discover a 3.6x mid-run.

**14.19 MiB/s is not a write throughput** and must not be quoted as one. It is
the rate this controller acknowledges zeros. The device's real write rate is
~4.0 MiB/s. The same applies to the 39.13 MiB/s read: that is the rate it
answers zero-mapped blocks, and the real read rate is ~23.9 MiB/s.

The substitution was exercised end to end on 2026-09-05: the erase wrote
`0xA5` over the whole device at 3.92 MiB/s, verification passed against `0xA5`
with 0 failed offsets, and a full re-verification after an unplug/replug cycle
passed again. See `docs/validation/hardware.md`.

## `queue/rotational` is not a flash test

A USB bridge does not clear the kernel's `queue/rotational` flag. The validation
stick reports `rotational: True` and `lsblk` agrees, so it is not even a
disagreement to report. Every flash caveat in the erase path was gated on
`not device.rotational`, so none of them reached the report for a USB flash
stick — the media that needs them most.

`core/device/media.py:is_flash` makes a positive determination instead, from the
transport, the flag, the model string, or the write calibration, and returns the
signal that decided it so the report can say how it knew. Transport wins over
the flag: there are no rotating USB sticks or SD cards, and the flag being wrong
is the documented failure mode.

## ATA enhanced SECURITY ERASE is Purge on magnetic media only, and that rule is unvalidated

On a device `is_flash` determines to be flash, ATA enhanced SECURITY ERASE and
ATA SANITIZE overwrite are not counted as Purge. The authority is the withdrawn
NIST SP 800-88r1 Table A-8, which lists SECURITY ERASE UNIT under Clear for ATA
SSDs; SP 800-88r2 does not name the command and defers to IEEE 2883, which has
not been read. `docs/compliance.md` quotes the text.

What this does not establish:

- **No SATA SSD has been available**, so the flash half of the rule has never
  run on real media. No ATA firmware erase has run on hardware at all. The unit
  tests in `tests/device/test_purge_by_device_class.py` and
  `tests/erase/test_enhanced_erase_on_flash.py` are the only evidence.
- **The rule is only as good as the flash determination.** A hybrid drive, or an
  SSD whose kernel reports `rotational=1` and whose model string names no flash,
  is treated as magnetic, and its enhanced erase is counted as Purge. r1 Table
  A-5 itself warns that hybrid drives "may not be easily identifiable by the
  label".
- **On magnetic media the Purge claim rests on a withdrawn table.** The capability
  record says so on every device where enhanced erase would be the Purge method.
- r1 advises consulting the manufacturer before relying on SECURITY ERASE UNIT
  on either media type. This tool does not, and cannot.

## USB and MMC bridges block ATA pass-through

Most USB-SATA bridges do not forward ATA pass-through commands, so `hdparm -I`
fails and neither SANITIZE nor SECURITY ERASE can be issued or even confirmed to
exist. Those devices are limited to overwrite-based CLEAR. Attach the drive to a
native SATA port to do better.

## A software write block is a claim about a flag, not about a refusal

`core/carve/acquire.py:apply_write_block` sets `BLKROSET`, reads it back with
`BLKROGET`, and reports `applied` from the read-back. That establishes that the
kernel holds the device read-only. It does **not** establish that a write would
be refused, and the record says so with `WRITE_BLOCK_NOT_VERIFIED`.

Proving the refusal means attempting a write, and the acquisition path never
writes to a device. The asymmetry is the reason: a write test fails safe only
when the block works, and it is precisely when the block does not work — the
case the test exists to detect — that the test writes to the evidence it was
protecting. The restore is another write, and on flash it programs a new page.

Verification by attempted write lives in `scripts/probe-write-block.py`, gated
behind `--i-understand-this-may-write-to-the-device`, run once against **scratch
media** to qualify an interface. `AcquisitionRecord.write_block_verified_by`
records which claim is being made: `flag_read_back` from the acquisition path,
`attempted_write` only from a qualification.

Measured on a Toshiba TransMemory behind a USB bridge, 2026-09-05:
`WRITE_BLOCK_WORKS`. The refusal arrived at `write()` with `EPERM` while
`open(O_WRONLY)` **succeeded** — a check that stopped at the open would report a
working block on a cosmetic flag. That refusal point is a kernel property, not a
bridge one; `BLKROSET` sets `bd_read_only` on the kernel's block device and the
kernel is what refuses.

Two paths are not covered by the flag at all, on any bridge:

- **SG_IO and ATA pass-through**, which address the device below the block layer
  where `bd_read_only` is never consulted. `hdparm --write-sector` and `sg_dd`
  write straight through a set flag.
- **A partition node whose own flag was never set.** The flag is applied to the
  path given to the acquisition; an automount writing through `/dev/sdX1` while
  only `/dev/sdX` was set is exactly the accident a write block exists to stop.
  Untested.

Use a hardware write blocker for evidence that will be presented.

## Verification above 64 GiB is sampled, not exhaustive

At or below 64 GiB every block is read. Above it, verification reads the first
and last 1 GiB in full plus 4096 random 1 MiB windows from a seeded RNG. The seed
is recorded so a third party can redraw the same sample set.

The report carries the detection-probability formula rather than a bare
percentage:

```
P = 1 - (1 - (r + u - 1) / n)^k
```

`n` total bytes, `u` sample window, `r` size of a hypothetical residual region,
`k` random draws. **This is the chance of detecting a residual region of a given
size. It is not proof that none exists.**

## Hardware attestation is the drive's claim about itself

After a firmware sanitize, the ATA SANITIZE STATUS or NVMe sanitize log is read
and recorded. That is evidence, not proof: it is the drive reporting on its own
behaviour. Verification therefore always reads the medium as well, and a clean
attestation never excuses residual data found by sampling.

Firmware sanitize is accepted as leaving either `0x00` or `0xFF`, because vendors
differ. Any other byte value is treated as residual data.

## Unwritable ranges are skipped, not fixed

A sector that returns `EIO` is recorded in `unwritable_ranges` and skipped; the
wipe continues, because one bad sector must not cost a four-terabyte job. Those
ranges still hold whatever they held before, and they raise the residual-risk
level to high.

## HPA and DCO

Hidden areas are detected read-only. For overwrite, the native max is unlocked
before the wipe and restored afterwards, and the original accessible sector count
is written to the ledger first so an interrupted job leaves a record of what to
restore to. If the unlock fails, the hidden region is **not** erased and the
report says so. Firmware sanitize covers the full media by design, so no unlock
is attempted there.

The `HIDDEN_AREA_UNLOCK` phase is ledgered on **every** run, including when the
drive reports no hidden area at all — as `not_required`, carrying the probed
sector counts. A chain that simply omitted the phase would be indistinguishable
from one where the tool never probed, and those two support opposite conclusions
about whether the sectors beyond the accessible max were ever considered. The
same is true of `HIDDEN_AREA_RESTORE`, which has always recorded its own
negative case.

No loopback device reports an HPA or a DCO — they are ATA features of real
media — so the branch where sectors genuinely are hidden cannot be reached by
pointing the erase engine at one. It is covered against a faked hidden-area
report in `tests/erase/test_hidden_area_phases.py`, which fakes the *probe* and
leaves the unlock decision, the geometry widening, the ledger entries and the
restore running unaltered.

## NVMe scope

`sanitize` acts at **controller** scope: it destroys every namespace on the
controller, not only the one named. `format` acts **per namespace**; every
namespace is iterated and named, and a namespace created after the run is not
covered.

## The ledger is mandatory, and there is no longer a fallback

This section previously recorded that `core/erase/drive.py` defaulted to an
in-memory ledger that was neither durable nor hash-chained, so a run made
without an explicit sink was not independently auditable.

That hole is closed. `execute()` now **refuses to start** without a ledger:

```
execute() needs a ledger: every phase must be recorded in the hash-chained
audit log. Pass ChainLedgerSink(Ledger(root, ...)).
```

The in-memory implementation is gone from `core/`. An equivalent lives in
`tests/erase/test_overwrite_file.py`, where its lack of durability costs
nothing, because what those tests need is to see the phase records the
overwrite loop emits — the chain itself is covered by `tests/ledger/`.

## Undelete recovers a different amount on every filesystem

`core/carve/fsaware.py` recovers deleted files from surviving filesystem
records. How much that is worth is not the same on any two filesystems, and
averaging them into one number would describe none of them. Measured against
`testkit/generate_corpus.py`'s real filesystem images, on the date in
`docs/performance/calibration.md`:

| filesystem | deleted | recovered exactly | recall |
|---|---:|---:|---:|
| NTFS | 25 | 24 | 96.0% |
| FAT32 | 246 | 235 | 95.5% |
| ext2 | 2 | 2 | 100.0% |
| ext3 | 2 | 2 | 100.0% |
| exFAT | 2 | 1 | 50.0% |
| **ext4** | **2** | **0** | **0.0%** |

### ext4 recovers essentially nothing, by design

`ext4_ext_remove_space` zeroes the inode's extent tree when a file is
unlinked. The inode survives with its size, its mode and its timestamps, and
points at **no blocks at all**. There is nothing to follow, and no
implementation can change that.

What is recovered instead comes from the jbd2 journal: stale copies of
inode-table blocks written before the tree was zeroed. The journal is a
circular buffer covering only the recent past, so a file deleted before it
wrapped is gone from it, and the recovered inodes carry no filename — ext4
keeps names in directory blocks, which cannot be tied to an inode number
without the transaction that wrote both.

**ext4 is not demonstrated.** On ext4, use the signature carver over the
unallocated map, which `undelete_report()` returns for exactly this reason.

### FAT recovery is a reconstruction, however clean the result looks

FAT deletion zeroes the file's cluster chain. The start cluster and the size
survive; the layout does not. Recovery walks forward from the start cluster
taking clusters the FAT currently shows as free and skipping any a live file
now owns.

That reconstruction is right for an unfragmented file, and right surprisingly
often for a fragmented one — but only while its neighbours still exist. Once a
neighbouring file has *also* been deleted, its freed clusters are
indistinguishable from this file's and are pulled into the result. The
recovered file is then the right length and the wrong content, and **nothing on
the volume can detect it**. Every FAT candidate is therefore marked
`contiguity_assumed`, whatever it looks like.

Where a cluster between a file's first and last is allocated to a live file,
fragmentation is *proven* and the candidate says so separately. The absence of
that proof is not evidence of contiguity.

### exFAT is the one case where the filesystem records the answer

The stream extension entry carries a `NoFatChain` flag. When it is set, exFAT
stored the file as one run and kept no chain for it *while it was live*, so a
contiguous read is a recorded fact rather than an assumption, and
`contiguity_assumed` is false. When it is clear, the file used a chain that
deletion destroyed and exFAT is no better off than FAT32. Which case applied is
recorded on every exFAT candidate.

### ext3's measured recall is an upper bound

The corpus deletes ext2 and ext3 files by unlinking, freeing the blocks and
setting `i_dtime`, leaving the block pointers in the inode. That is exactly
what ext2 does. A Linux 2.6 or later kernel also runs `ext3_truncate` on
delete, which zeroes `i_block` as well — so **real ext3 recall is at or below
the 100% measured here**, and on a modern kernel it will be closer to ext4's.
ext2's figure stands as measured.

### NTFS resident files are reassembled around the update sequence

A file small enough to fit inside its MFT record has no run list: its content
is part of the record, and NTFS has overwritten the last two bytes of each
sector of that record with a check value. Those bytes are spliced back from the
record's update sequence array, so the recovered content is exact. If a future
NTFS variant changed the fixup layout, this would produce two wrong bytes per
sector in small files, which is why `candidate.sha256` is computed over the
same extents `read_recovered()` returns rather than over the bytes TSK hands
back — the claim is checkable.

## What each carved format's length comes from, and which ones are a guess

A carved object has no filename and no recorded size, so its end is either derived from
the format's own structure or it is guessed. The difference decides whether the recovered
file opens, and it is not uniform across the table in `testkit/signatures.yaml`:

| Format | Where the end comes from | Exact? |
|---|---|---|
| JPEG | segment walk to the EOI marker | yes |
| PNG | length-prefixed chunk walk to IEND, every CRC verified | yes |
| PDF | the last `%%EOF` a `startxref` corroborates | yes |
| ZIP, DOCX, XLSX | the end-of-central-directory record | yes |
| SQLite | `page_size` × `page_count` from the header | yes |
| MP4 | the top-level box sizes | yes |
| TIFF | the IFD chain and the strip offsets it points at | yes |
| BMP | the 32-bit file size in the file header | yes |
| WebP, WAV | the RIFF size field | yes |
| GZIP | inflating the member, bounded at 256 MiB of output | yes |
| RTF | counting braces to the one closing the document group | yes |
| TAR | member headers to the end-of-archive marker | yes, with the caveat below |
| **HTML** | **the closing `</html>` tag, or the next object** | **no** |
| GIF | footer search | no |
| OLE, ELF, PE, RAR, 7z, EVTX | no parser; footer or next-object bound | no |

**HTML is a footer bound and says so.** The format carries no length anywhere, and
`</html>` is a convention rather than a requirement. A document that ends without one is
bounded by wherever the next object's header begins, which is an upper bound and not a
length; the candidate reports `possibly_fragmented` and does not claim an exact length.
Only the lower-case `<!DOCTYPE html` and `<html>` spellings are matched, so an
upper-case or mixed-case document is not found at all.

**A tar is returned without its trailing padding, and is therefore usually not
byte-identical to the file as written.** GNU tar pads an archive to its blocking factor -
10,240 bytes by default - after the two zero blocks that end it. Those padding bytes are
zeros, and so are the bytes of the last cluster's slack on every filesystem, so nothing
in the content distinguishes them. Rounding the length up to a blocking factor would
return the file exactly and would be an assumption about the writer rather than a
measurement of the medium, which is the same defect as reading cluster slack as an MP4
box. The archive therefore ends where the format says it ends. Where the filesystem
record survives, the undelete pass recovers the tar at its recorded size and this does
not apply.

**A gzip member that inflates beyond 256 MiB gets no derived length.** Deriving the end
of a gzip stream means decompressing it, and a carved candidate is exactly the kind of
untrusted input that is a decompression bomb. The output is counted and discarded, never
held; past the bound the parser declines and the candidate keeps the scan's bound.

**Two formats share a header.** WebP and WAV are both `RIFF`, and what separates them is
the form type four bytes later. Both are in the table and both are checked.

## Bifragment reassembly is narrow, and depends on the volume's cluster size

`core/carve/fragmentation.py` rebuilds a baseline JPEG or a PNG split into two runs.
PNG's reach and refusals are measured separately in
[`validation/png-reassembly.md`](validation/png-reassembly.md): 120 of 120 layouts to
a 7 MiB gap, 0 of 800 adversarial joins accepted, on synthetic images. The rest of
this section is about JPEG. What it can be trusted with is exactly this and no more:

* **One baseline JPEG, exactly two runs, both still on the medium.** Progressive,
  arithmetic-coded, lossless and multi-scan JPEGs are never reassembled, and no format
  other than JPEG and PNG is. A file in three or more pieces is not recovered.
* **Reach: the gap between the runs at most 2 MiB (2,097,152 bytes).** Measured over
  ten random layouts at each of 64 KiB, 128 KiB, 256 KiB, 512 KiB, 1 MiB and 2 MiB, on
  512-byte and 4096-byte clusters with the size known: all recovered byte for byte.
  Beyond that it is not reliable on small clusters — 7 of 10 at 4 MiB and 3 of 10 at
  8 MiB on 512-byte clusters, 10 of 10 to 8 MiB on 4096-byte ones — and the object must
  end within 8 MiB of its header in every case. Failures are refusals; none of the
  layouts was reassembled wrongly.
* **The volume's cluster size has to be known for that reach.** It is read per volume
  by the undelete pass (boot sector, BPB or superblock) and limits every join to that
  grid. On a raw image, a damaged boot sector or a carve run without undelete, the
  search walks 512-byte sectors instead: still safe, since every real layout lies on
  that grid, but slower and with less reach — on a 4096-byte volume, 10 of 10 recovered
  to 1 MiB and 8 of 10 at 8 MiB, and two or more clusters of ambiguous gap bytes next
  to a run edge were refused.
* **Ambiguous gap bytes cost reach.** Bytes that cannot occur inside a JPEG scan mark
  where the gap starts and ends. Zeros, directory entries, text and another JPEG's scan
  data do not, so the search has to try every join through them: up to 8 clusters of
  them next to a run edge were recovered, 16 were refused.
* **A refused search costs time.** Up to about 1.7 seconds per JPEG header, against
  tens of milliseconds before Batch 7. Measured on an adversarial image with an EOI
  every 4 KiB: 396 ms per header, against 23 ms before.

### Why a reassembled object is never HIGH

Acceptance rests on an exact count of the scan's entropy-coded data against its frame
header, which Pillow does not do: libjpeg reports a short, overlong or misaligned scan
as a warning and returns an image regardless, and at `hwval-run4` that let the tool
emit a real head joined to its own tail read 3,584 bytes late, scored HIGH (PREFLIGHT2
FINDING 1). The count is much stronger — 0 of 600 insertions, 0 of 400 chimeras of two
JPEGs and 0 of 7,200 same-length substitutions accepted — but it is not a proof. **A
join that drops 512 to 1,536 bytes of the object's own scan passes about one time in
twenty on noise-like JPEGs** (18, 21 and 14 of 400 at 512 and 1,024 bytes; none at
3,584 bytes or more; none on a smooth photographic image). The search tries the true
join before any such one whenever the true join is on the medium, so this bites when it
is not: a tail whose first sectors were overwritten. That is why every reassembled
candidate carries a `reassembly` score component holding it at 7999, one basis point
below HIGH, and why the report and the UI both say it was rebuilt from runs.

### The JPEG verdict has been checked against one camera's photos

The same exact scan count also marks a contiguous baseline JPEG `corrupt` when it
decodes but its entropy-coded data does not account for its frame header — including
the first image of a JPEG whose MPF index lists further images, which Pillow opens as
MPO — and that verdict has been checked against the JPEGs of one camera firmware
only, an Apple iPhone 15 Pro Max on iOS 18.5 (7 photos, 6 EXIF thumbnails and 7 HDR
gain maps, every one with restart intervals, all counted exactly), and against no
Android phone and no dedicated camera, so on JPEGs from any other device that verdict
is not yet shown to mean damage.

**Only the first image of an MPO is counted.** A further image the candidate holds —
the gain map of a phone photo recovered whole by undelete, or a second stereo view —
is judged by the decoder alone, which reports foreign bytes inside a scan only as a
warning. Damage confined to that second image can therefore still read `valid`.

**A carved phone photo is its first image only.** The object ends at the EOI its scan
reaches, and the gain map an iPhone stores after that EOI is carved as a separate
candidate. The photo's `validation_detail` says how many images its MPF index
declares, how many the object holds, and where the absent ones were declared to be;
the photo's bytes and digest are exact.

**Every recall and precision figure in `docs/validation/` and `docs/performance/` was
measured on populations whose JPEGs were all Pillow encodes**: the calibration corpora
built by `testkit/generate_corpus.py` and `testkit/fsimage.py`, and the files
`scripts/hardware-validation.sh` plants on real media. No camera-written JPEG was in
any measured population. That is a limit of those figures, not a finding about the
tool: they say nothing, in either direction, about how camera photos are recovered or
scored.

## E01 acquisition is uncompressed, and slightly larger than the source

`pyewf` binds exactly one write-configuration setter, `set_header_codepage`.
`libewf_handle_set_compression_values` is not reachable from Python, so
`AcquireOptions.compression` is accepted and **has no effect**. libewf's default
is not "fast"; it is *no compression*.

An E01 written by this tool is therefore marginally **larger** than the source.
Confirmed with `ewfinfo` against a container this codebase wrote: 8 MiB of a
single repeated byte produced 8,394,899 bytes. E01 here is a container format
and an integrity record, never a space saving. Use `ewfacquire -c fast` where a
compressed container is required.

An E01 acquisition also **cannot be resumed** — libewf has no append mode for
an existing segment set — and checkpoints during one are recorded in the ledger
but not forced to disk, because `pyewf` exposes no flush. Both are refused or
recorded rather than worked around. Acquiring to raw resumes normally.

## Per-file overwrite is best effort, and the report names every gap

`core/erase/files.py` writes through a file handle. That reaches the file's
current data extents and nothing else. It does not reach:

- the ext3/ext4/xfs journal or the NTFS `$LogFile`,
- the NTFS `$UsnJrnl` change journal,
- `$MFT` record slack or `$I30` index slack,
- file slack between end-of-file and end-of-cluster,
- any block a copy-on-write filesystem has already redirected away from,
- a page a flash translation layer remapped after a TRIM.

`core/erase/residual.py` enumerates each of these as a named finding with a
derived severity rather than leaving them out of the report. **The enumeration
is the deliverable.** A separate free-space wipe now overwrites the blocks a
volume calls free (next section); every class in the list above is still only
detected and reported. A tool that reports "shredded, unrecoverable" is lying; a
tool that reports "overwrote 3 extents, the ext4 journal may retain content, and
2 snapshots still reference the old extents" is evidence.

That the journal really is a recovery route is not a claim taken on trust here:
`core/carve/fsaware.py` recovers deleted ext4 content from exactly that
structure, and `tests/erase/files/test_residual_against_real_filesystems.py`
pins the two modules to the same mechanism.

### A free-space wipe reaches free blocks and nothing else

`core/erase/freespace.py` fills a mounted volume's free space with `0xA5` through
files in a directory of its own until `ENOSPC`, then deletes them. What it can be
trusted with:

**Measured, on udisks loop volumes mounted by the kernel's own drivers.** Six JPEGs
were planted and deleted, then the recovery pipeline (`api.carve_job`) was run over
the image before and after the wipe
(`tests/erase/files/test_free_space_wipe_carve.py`):

| Volume | Recovered before | Recovered after | Raw slices after | Bytes written | Free before (`f_bavail`) | Blocks still free at `ENOSPC` |
|---|---|---|---|---|---|---|
| FAT32, 512-byte clusters | 6 of 6 | 0 | 0 | 66,053,120 | 66,056,704 | 0 |
| FAT32, 4096-byte clusters | 6 of 6 | 0 | 0 | 326,975,488 | 326,979,584 | 0 |
| exFAT, 4096-byte clusters | 6 of 6 | 0 | 0 | 64,974,848 | 64,978,944 | 0 |
| exFAT, 32768-byte clusters | 6 of 6 | 0 | 0 | 64,749,568 | 64,782,336 | 0 |
| ext4, 4096-byte blocks | 6 of 6 | 0 | 0 | 53,805,056 | 53,809,152 | 4,694,016 |

"Raw slices after" counts planted files a 512-byte slice of which was still
anywhere in the image, whatever the carver made of it. On each volume, the gap
between free space and bytes written is one cluster: the one the filler
directory itself took.

What that does not establish:

- **None of it ran on real media.** A loop device has no flash translation layer.
  On flash the fill reaches the logical blocks the filesystem calls free, and the
  controller chooses which physical pages receive it (see "Overwrite cannot reach
  all of a flash device"). No USB stick or SD card run has been recorded.
- **The fill must reach `ENOSPC`, because every allocator measured here is
  next-fit.** On each of the five volumes, a file written right after a deletion
  did not land on the clusters just freed. A partial fill therefore misses the most
  recently freed space first. A cancelled wipe claims no coverage.
- **ext4's reserved blocks are not written.** The fill runs unprivileged and stops
  at `ENOSPC` with the root reserve still free: 4,694,016 bytes on the 64 MiB test
  volume, reported as `free_blocks_bytes_at_full`. None of the planted content was
  there in the measured run. The allocator's placement decided that, not the wipe's
  coverage, and a different history could leave content in those blocks.
- **Not reached, on any filesystem:** file slack (writing past the end of a file
  the operator did not name is refused on principle: that file is evidence);
  deleted directory entries, whose names, sizes and timestamps the undelete pass
  still reads; journals and filesystem metadata; the filler directory's own
  clusters. One side effect was measured, and it is not coverage: on FAT32 and
  exFAT the filler directory's own entry in the volume root was placed in the
  slots of a deleted root entry, and that one name was gone after the wipe. On
  ext4 the deleted name was still in its directory block afterwards.
- **Nothing is read back.** `verified` is always `null`. The carve before and
  after is test evidence, not something the product does on every run.
- **Only kernel `vfat`, `exfat` and `ext4` are accepted.** NTFS has not been
  measured, and neither has FUSE (including `fuse2fs`). Copy-on-write filesystems
  would write the fill beside old data rather than over it. All are refused. Linux
  only.
- **The flash caveat is attached to loop volumes too.** The `trim_likely` probe
  treats a device that advertises discard as flash, and a loop device does. For
  these test volumes the caveat is a false positive, and it is reported rather
  than suppressed.
- **The integration tests need a desktop session.** udisks attaches and mounts loop
  devices for the active local user through polkit. Elsewhere (CI, SSH, a
  container) the tests skip, and they name the reason.

### A file erase is usually unverifiable, and is reported as unverifiable

`verify_file_erase` returns a **tri-state** `passed`. `None` means "the original
physical location could not be read, so nothing is claimed", and it is the
common answer. It refuses in four distinct situations:

| situation | why nothing can be claimed |
|---|---|
| no extent map was captured | there is no address to read back |
| copy-on-write filesystem | the overwrite went to freshly allocated blocks |
| data was resident in metadata | there is no data extent at all |
| raw device read refused | reading the original blocks needs root |

Only after all four are cleared does it open the block device read-only, seek to
the pre-erase physical offsets and compare. **Exactly one function in
`core/erase/verify.py` can construct `passed=True`, and it is reachable only
after that read.** A test parses the module's AST and fails the build if a
second construction site appears.

On an ordinary unprivileged run the honest outcome is: the file was overwritten,
renamed, unlinked — and verification reports `not_possible`.

### No file erase on FAT or exFAT can be verified by reading back extents

The extent map a file erase is verified against is captured with the `FS_IOC_FIEMAP`
ioctl on Linux. **Neither `vfat` nor `exfat` answers it**: both return
`[Errno 95] Operation not supported`, measured on loopback mounts of each during the
second hardware pre-flight (PREFLIGHT2 FINDING 2). So `core/erase/inspect.py` captures
no extents for any file on either filesystem, and `verify_file_erase` reports
`not_possible` for every one of them.

That is not an edge case. FAT32 and exFAT are what a USB stick or an SD card is
formatted with out of the box, so **a per-file erase on a removable device's own
filesystem can never be verified by reading the medium back.** The overwrite, the
renames and the unlink still happen and are recorded; the verification claim is
absent, and the report says why.

### Hard-linked files are not overwritten by default

A file with `st_nlink > 1` shares its inode with names the operator did not
give. Overwriting it would destroy their content too, so by default only the
named link is unlinked and `HARDLINK_SURVIVES` is reported at HIGH with the link
count. **The data survives, and the report says so.** `break_hardlinks=True`
overwrites anyway, and the finding still reports that it happened.

### Metadata cleansing runs before the overwrite, and never claims a false clean

Cleansed bytes are what get destroyed. The other order would leave the original
EXIF block in whatever the overwrite did not reach.

A file that could not be parsed is reported `parsed=False` with a reason, never
as a clean file with zero fields — those two read identically in a report and
only one of them is true. OLE compound documents (`.doc`, `.xls`, `.ppt`) and
HTML are **identified but not rewritten**: doing so safely needs a full writer
for each format, and a partial rewrite risks a document that no longer opens.
Their metadata is destroyed by the overwrite that follows, not by the cleanser.

A PDF updated incrementally keeps its earlier revisions in the same file.
Clearing the current metadata does not clear a copy held in a previous revision.

### Directory fsync is not available on Windows

The rename chain is flushed with `fsync` on a directory handle, which POSIX
supports and Windows does not expose unprivileged. On Windows the renames may
remain recoverable from the directory index until the filesystem flushes on its
own schedule, and the limitation is recorded on the record.

### The Windows backend: what has now run, and what has not

`core/erase/_platform/win.py` now runs on a real Windows 11 runner in CI
(`platform-ci`), against that machine's NTFS volume: alternate data streams,
resident MFT data, the read-only attribute, and a real directory junction that
must not redirect a recursive erase. Those results are recorded in
`core/platform/validation_record.json`, and the app's capability screen lifts
file erase on Windows out of *Unverified* only because that record exists.

What has still never happened: a human installing the package on a physical
Windows machine, and any erase on physical media. A CI runner has a virtual
disk and no removable device. Every method still degrades to an honest unknown
plus a recorded limitation when a call fails.

One defect that testing found and fixed: the extent map recorded one cluster
per run, so a post-erase read-back of a contiguous 256 KiB file verified
4 KiB of it. Runs now carry their length, and a map truncated by fragmentation
says so.

## Platform differences

The full matrix is [`platform-support.md`](platform-support.md). The limits
that change what an operator can do:

- **Whole-drive sanitization is Linux-only.** Windows and macOS discover and
  assess devices and refuse whole-drive work with the reason, performing
  nothing. No raw-disk writer has been written and validated for either, and
  the tool does not offer one it cannot verify. Use the Linux AppImage on the
  same hardware (from a live USB for an internal disk).
- **Firmware Purge has never run on hardware**, on any platform. It is
  selected from probed capability and dispatched on Linux; that path is
  UNVERIFIED.
- **APFS, Btrfs, ReFS and F2FS are copy-on-write.** A file erase on them
  removes the file and reports residuals; it cannot destroy the old blocks and
  is never reported as verified.
- **macOS internal storage** is purged by macOS's own *Erase All Content and
  Settings* (Apple silicon, T2), which destroys the storage keys. The app
  names that path and does not perform or verify it.
- **Windows and macOS discovery are UNVERIFIED.** The parsers are tested from
  captured `Get-Disk` and `diskutil` output on Linux; they have not been run
  against a real Windows or macOS disk set.
- **Windows file verification needs elevation** (raw volume read); the app
  never elevates, so unelevated erases are reported *not verified*.
- **diskutil reports no serial numbers.** macOS devices are identified by BSD
  name, size and media UUID.
- **The development server is loopback-only and session-protected**, like the
  packaged app: `python -m api.main` prints a `/session/<token>` URL and
  refuses anything without that cookie. `SANCTUM_DEV_INSECURE=1` disables the
  check for a single-user development machine and prints a warning; there is
  no configuration that binds anything other than `127.0.0.1`.
- **Containers.** Inside a container the host's root, mounts and swap are
  invisible while `/sys` still lists the host's disks, so the system disk
  cannot be identified. Whole-drive work is refused there unless
  `SANCTUM_ALLOW_CONTAINER_DEVICES=1` is set for a container that was given
  exactly the target device.
- **Hardware validation of this change: none.** No designated disposable media
  was used; no device was written. See
  [`validation/platform-matrix.md`](validation/platform-matrix.md).

## PII triage counts shapes, stores no values, and reads only some types

`core/carve/pii.py` counts Aadhaar (Verhoeff), PAN, IFSC, Indian mobile, payment
card (Luhn) and email shapes in each recovered object. The user manual (§6, "PII
triage") lists the exact patterns.

**Values are never stored.** Each recovered object gets kinds and counts only. No
matched value, prefix, suffix, mask, hash or offset is written to the report, the
ledger, the job result or any log. A hash is excluded because an Aadhaar number
has 10^11 checksum-valid values, which is brute-forced against a SHA-256 in
minutes. An offset is excluded because, with the recovered object, it gives the
value back. `tests/api/test_pii_no_leak.py` plants synthetic values in deleted
files on a FAT32 volume and runs the carve, the SSE stream, report generation and
verification. It then searches the ledger, the reports (the JSON, and the PDF raw
and decoded), the job result, the SSE replay, every structlog event at DEBUG,
stdlib logging, stdout, stderr and every other file under the state and key
directories. It searches for each value as planted, as bare digits, in UTF-16LE,
base64, SHA-256/SHA-1/MD5 hex, and as a mask ending in the real last four digits.
It fails on any match. It was also run with a deliberate leak (a debug log of the
match) and failed, naming the sink.

**A count is a signal to look, not a finding.** A shape plus a checksum does not
establish an identifier. One random 12-digit string in ten passes Verhoeff, and
one random 16-digit string in ten passes Luhn. A 16-digit Aadhaar Virtual ID that
happens to pass Luhn is counted as a card. International phone numbers,
non-Indian identity numbers and postal addresses are not detected at all.

**Measured false-positive rates.** Raw bytes, no type gate, data holding no planted
identifier; hits per MiB:

| Corpus | Size | Aadhaar | PAN | IFSC | Mobile | Card | Email |
|---|---:|---:|---:|---:|---:|---:|---:|
| Synthetic carving corpus, seed 0 | 12.5 MiB | 0 | 0 | 0 | 0 | 0 | 0 |
| Filesystem corpus images, seed 0 (13) | 414.0 MiB | 0 | 0 | 0.188 | 0 | 0 | 0 |
| iPhone camera JPEGs (7) | 13.9 MiB | 0 | 0 | 0 | 0 | 0.072 | 0.144 |
| iPhone HEIC (6) | 7.8 MiB | 0 | 0 | 0 | 0 | 0 | 0 |
| Thumbnails and gain maps carved from those photos (19) | 0.87 MiB | 0 | 0 | 0 | 0 | 0 | 0 |

All three JPEG hits are in entropy-coded image data. One card hit is inside a
secondary (gain-map) image. All 78 IFSC hits are FAT 8.3 directory entries
(`FILL0001PAD`), a false-positive class that camera names such as `DSCN0001JPG`
also fall into. Zero means none were seen in that many bytes, not a zero rate.
These corpora are small, and the photos come from one phone.

**Therefore only documents, databases and unclassified objects are scanned.**
Images, media, archives and executables are not. Scanned as raw bytes, 2 of the 7
camera JPEGs measured would have shown an identifier that is not there. A ZIP's
members are therefore not scanned, and neither is text inside an image, such as a
photographed ID card.

What the scan reads, and misses:

* **OOXML:** the text of the XML parts, tags removed, capped at 64 MiB of inflated
  text. Each part is inflated 64 KiB at a time and never held whole; a 32 MiB part
  measured 2.37 MiB of peak allocation. Paragraph, cell and row boundaries are
  kept, so two cells never fuse into one number.
* **PDF:** content streams, skipping image and font streams. Text drawn
  with per-glyph kerning (`[(98765)-20(43210)]TJ`), hex strings or a custom
  encoding is not reassembled and is missed. So is an encrypted PDF. A recovered
  PDF is untrusted input, so no stream is decoded whole: each stream's raw bytes
  are read, unfiltered and `FlateDecode` streams are inflated 64 KiB at a time
  into the counter, and reading stops at the 64 MiB budget. A 1 GiB Flate stream
  stored in 1 MiB costs +6.7 MiB of peak RSS (it cost +2,050 MiB before this
  bound). **Streams behind any other filter** (`ASCII85Decode`, `LZWDecode`,
  `RunLengthDecode`), with a predictor, or in an encrypted file **are not
  decoded and are missed**; the `basis` counts them. qpdf inflates object and
  cross-reference streams while it opens a file, so those are measured in the raw
  bytes first, and a PDF with one that inflates beyond 16 MiB, or that uses a
  filter whose size cannot be measured, is not opened or scanned at all.
* **Raw bytes** (`.txt`, `.csv`, SQLite, legacy `.doc` and unclassified objects):
  ASCII/UTF-8 only. UTF-16 text is missed; that covers most legacy `.doc` bodies
  and EVTX. So is a number stored as a binary integer. The false-positive rate on
  OLE and unclassified binary objects has not been measured.
* **Objects above 64 MiB:** raw-byte types are scanned through the read-only handle
  in 1 MiB windows. OOXML and PDF objects that large are not scanned, and their
  `basis` says so.

## The operator identity is a local account, not a person

The ledger's `actor` is resolved server-side by the privileged helper from the
uid it was started with (`api/identity.py`, `helper/daemon.py:_op_whoami`). A
client can no longer write an arbitrary name into it. What that establishes is
**which operating-system account** ran the operation. It does not establish who
was at the keyboard, and on a shared account it does not distinguish two people.
With no helper socket configured the identity is the API process's own uid and
its basis sentence says so. A label an examiner types is recorded beside the
identity and marked `[label: …]`; it is not verified.

## Reports rebuilt after a restart carry no progress trace

A finished job's result is written into the chain as a `job.outcome` entry, so
`POST /reports/{job_id}` works after the API restarts (`api/durable.py`). The
result object is byte-identical — it is content-addressed — but the per-record
progress stream never entered the chain and is absent, and the report's
limitations say it was rebuilt. A job that never reached a terminal state (the
process was killed mid-run) has no outcome entry and still cannot be reported.

## Spilled carve bytes are deleted, not sanitized

During a carve, fragmented and multi-extent objects are spilled to
`<state>/work/` and removed on success, failure and cancel
(`api/carve_job.py:SpillStore`). Removal is `unlink`. On flash media the bytes
may persist physically until the FTL reclaims them. Keep the state directory on
an encrypted volume if recovered content is sensitive.

## The case document is an index

`<state>/cases/*.json` is ordinary mutable JSON. It groups operations and
reports for the UI and proves nothing; the hash chain is the record, and the
case screen's integrity verdict is always the chain's. Editing a case document
changes the grouping, not the evidence.

## The API has no authentication

It binds `127.0.0.1` only. Any local account that can reach that port can drive
it. Run it on a single-user examination workstation. See
[`threat-model.md`](threat-model.md#local-multi-user-threat).

## Platform

Whole-device sanitization is Linux only. `core/erase/drive.py` refuses to import
elsewhere rather than offer a shim that would have to fake `O_DIRECT` alignment,
`BLKGETSIZE64` and ATA/NVMe pass-through. On Windows, use WSL2 with
`usbipd-win`, or a Linux VM with the controller passed through.
`core/erase/files.py` stays cross-platform.

**The Windows file-erasure backend (`core/erase/_platform/win.py`) is
type-checked under `--platform win32` and now also runs on a real Windows 11
runner in CI** — see "The Windows backend: what has now run, and what has not"
above, which is the authoritative statement, and the `file_folder_erase` /
Windows 11 row of `core/platform/validation_record.json`. Alternate data
streams, resident MFT data and a real directory junction are covered there. The
USN journal and Windows file locking are still implemented from documentation
and exercised by no test, and **no erase has run on physical Windows media**: a
CI runner has a virtual disk and no removable device.

This paragraph previously said the backend had never executed on a Windows
host. That stopped being true when `platform-ci` began running the suites on a
Windows runner, and the sentence outlived the fact.

**E01 limits.** Acquisition to E01 is uncompressed (see above). E01 reading
depends on the `libewf-python` build; the tests that need an E01-writing build
are among the suite's skips and name their reason.

## Destroy

`SanitizationLevel.DESTROY` is never achievable in software and is never
returned by method selection. It means physical destruction: shred, disintegrate,
incinerate, melt.
