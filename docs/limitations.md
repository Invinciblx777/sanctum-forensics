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

## The ledger is not yet hash-chained

`core/erase/drive.py` currently defaults to `InMemoryLedger`, which keeps entries
in memory and mirrors them to structlog. It is neither durable nor hash-chained.
Until M4 lands `core/ledger`, a run made with the default sink is **not
independently auditable**, and that limitation is attached to every result it
produces.

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
