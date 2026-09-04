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

The method is offered only because operators are sometimes contractually
required to name it. It is superseded by NIST SP 800-88 Rev.1, provides no
measurable benefit over a single pass on any drive made after 2001, and on
flash media it is actively harmful: every extra pass burns program/erase cycles
without reaching a single remapped or over-provisioned block.

## Overwrite cannot reach all of a flash device

A host overwrite only reaches host-addressable LBAs. On SSDs, eMMC and USB
flash, these are unreachable by any write pattern:

- blocks the FTL has remapped after wear or failure,
- over-provisioned capacity never exposed to the host,
- data still live in the write cache or in an unmapped erase block.

Only a firmware sanitize or a cryptographic erase covers those. Where neither is
available, the result is a **Clear**, not a **Purge**, and the report says so.

## USB and MMC bridges block ATA pass-through

Most USB-SATA bridges do not forward ATA pass-through commands, so `hdparm -I`
fails and neither SANITIZE nor SECURITY ERASE can be issued or even confirmed to
exist. Those devices are limited to overwrite-based CLEAR. Attach the drive to a
native SATA port to do better.

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

## Platform

Whole-device sanitization is Linux only. `core/erase/drive.py` refuses to import
elsewhere rather than offer a shim that would have to fake `O_DIRECT` alignment,
`BLKGETSIZE64` and ATA/NVMe pass-through. On Windows, use WSL2 with
`usbipd-win`, or a Linux VM with the controller passed through.
`core/erase/files.py` stays cross-platform.

## Destroy

`SanitizationLevel.DESTROY` is never achievable in software and is never
returned by method selection. It means physical destruction: shred, disintegrate,
incinerate, melt.
