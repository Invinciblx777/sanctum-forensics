# Compliance

What the standards ask for, where this tool does it, and — the part that matters —
where it does not and says so.

Nothing here claims certification. No product is "NIST certified"; SP 800-88 is a
guideline, and the honest claim is that the tool implements its decision procedure and
its documentation requirements, which is checkable. Every row below names the code or
the report section that carries it.

## NIST SP 800-88 Rev.1 — *Guidelines for Media Sanitization*

### Section 2.5 — the three types of sanitization

The standard's vocabulary is the only vocabulary this tool uses. `SanitizationLevel` in
`core/models.py` has exactly three members and no fourth.

| Type | What the standard means | Where |
|---|---|---|
| **Clear** | Logical techniques that overwrite all user-addressable locations. Resists keyboard-level recovery. | `SINGLE_PASS_OVERWRITE`, `DOD_5220_22_M_3PASS` in `core/erase/patterns.py` |
| **Purge** | Physical or logical techniques that make recovery infeasible using state-of-the-art laboratory techniques. | `ATA_SANITIZE_BLOCK_ERASE`, `ATA_SANITIZE_CRYPTO_SCRAMBLE`, `NVME_SANITIZE_BLOCK`, `ATA_SECURITY_ERASE_ENHANCED`, `SED_CRYPTO_ERASE` |
| **Destroy** | Physical destruction — disintegrate, incinerate, pulverize, shred, melt. | **Never returned by method selection.** `docs/limitations.md` states it plainly: Destroy is not achievable in software and this tool will not claim it. |

**The level is derived, never requested.** `core/erase/drive.py:select_method` is a
decision table over the probed `DeviceCapabilities`. A user asking for Purge on a device
behind a USB bridge that cannot pass ATA commands gets Clear, and the report says Clear
and says why. This is the requirement most tools fail, and it is the one an NTRO panel
will test first.

### Section 4 — sanitization decision making

The standard makes the choice a function of media type and the security categorisation
of the data. This tool implements the media-type half — the half a tool can know — and
records the evidence that produced the decision so a reviewer can check it:

- The probe output that decided the level is carried in the report's **Method** section
  under `capability_evidence` (`core/report/render.py`), not merely the conclusion.
- Where a capability could not be established rather than established-absent, the
  distinction is preserved. `hdparm` failing behind a USB bridge produces "ATA
  pass-through is unavailable through this bridge", not "SANITIZE unsupported".

The data-categorisation half is an organisational input this tool does not have and does
not guess at.

### Appendix A — minimum sanitization recommendations

Appendix A is per media type, and the substantive point is that for ATA hard drives
manufactured after 2001 **a single overwrite pass is sufficient**; multi-pass overwrite
is not required and is not more effective.

| Media | Appendix A's position | What this tool does |
|---|---|---|
| ATA HDD | Single overwrite pass for Clear; ATA SANITIZE OVERWRITE / SECURITY ERASE for Purge | Implemented; single pass is the default and the report cites the standard's position |
| ATA / SATA SSD | Overwrite is **not** sufficient for Purge; use SANITIZE BLOCK ERASE or CRYPTO SCRAMBLE | Implemented. Overwrite-only on flash produces a Clear and a residual-risk factor naming remapped and over-provisioned blocks |
| NVM Express | `nvme sanitize`, or format with a secure-erase setting | Implemented. `docs/limitations.md` records that sanitize acts at **controller** scope and destroys every namespace |
| Self-encrypting (Opal) | Cryptographic erase — destroy the media encryption key | Implemented, and Pyrite is distinguished from Opal: Pyrite implements the command set *without* media encryption, so there is no key to destroy and a crypto erase would erase nothing |
| USB / flash / memory card | No sanitize command path; overwrite with residual risk stated | Implemented as Clear with the flash caveats attached |

**Where we fall short of Appendix A:** eMMC exposes its own SECURE ERASE and SANITIZE
through the MMC command set. There is no MMC dispatcher, so a memory card that could
achieve Purge is reported as Clear-only. That is a capability the tool declines to probe,
and it is recorded here rather than left for a reviewer to find.

### Section 4.7 — verification

The standard asks for verification of the sanitization result, and permits representative
sampling where full verification is impractical.

- At or below 64 GiB every addressable block is read and compared
  (`core/erase/verify.py:choose_strategy`).
- Above it, the first and last 1 GiB are read in full plus 4096 seeded random 1 MiB
  windows. **The seed is recorded in the report** so a third party can redraw the same
  sample set.
- The report carries the detection-probability formula, not a bare percentage:
  `P = 1 - (1 - (r + u - 1) / n)^k`. It states the chance of detecting a residual region
  of a given size, and the report says explicitly that this is not proof none exists.
- After a firmware sanitize the drive's own status log is read and recorded as
  hardware attestation — and verification still reads the medium, because an attestation
  is the drive reporting on itself.

Verified against real media: `docs/validation/hardware.md` records a full read of all
7,759,462,400 bytes of the validation device passing with zero failed offsets, twice.

### Section 4.8 and Appendix G — documentation and the certificate of sanitization

Appendix G lists the fields a sanitization record should carry. The report's nine
sections cover them:

| Appendix G field | Report section |
|---|---|
| Manufacturer, model, serial number | **2. Device Identity** — with `by_id_path` and both block sizes |
| Media type | **2. Device Identity** — `transport`, plus the flash determination and the signal that decided it |
| Sanitization method and tool used | **3. Method** — method, level requested, level achieved, and the probe evidence behind the choice |
| Verification method | **5. Verification** — strategy, bytes checked, sample count, seed, and the probability statement |
| Person performing and validating | **1. Case Identity** — operator; the ledger records the actor per entry |
| Date | **1. Case Identity** — `generated_at`, and per-entry UTC timestamps in the audit trail |

Two fields Appendix G does not ask for and this tool adds:

- **6. Residual Risk** — what the run could not cover, as measurements rather than prose.
- **7. Limitations** — carried verbatim from the layer that raised them, never reworded
  and never truncated (`tests/report/test_render.py`).

The certificate is issued in two artifacts: a canonical JSON that is authoritative and
carries the Ed25519 detached signature, and a PDF that states on its own face that it is
a rendering and not the authoritative artifact.

## IEEE 2883-2022 — *Standard for Sanitizing Storage*

IEEE 2883 refines the same three-outcome model that SP 800-88 established (its third
outcome is named *Destruct*) and is more explicit about per-technology methods and about
sanitization of logical rather than physical media.

Where this tool aligns:

- **Outcome-based rather than pass-count-based.** The result is named by what it achieves
  on the device in front of it. No pass count is offered as a security claim.
- **Media-specific method selection**, with flash treated as its own case rather than as
  a hard drive that happens to be fast.
- **Cryptographic erase as a first-class Purge method**, including the Opal/Pyrite
  distinction that decides whether a key exists to destroy.
- **Verification is a required step, not an option**, and its strength is reported.

Where it does not: this tool sanitizes physical block devices and files on mounted
filesystems. It does not address logical storage abstractions — virtual disks, cloud
volumes, storage-array LUNs — and makes no claim about them.

Clause numbers are deliberately not cited here. The standard is paywalled, the mapping
above is by substance, and inventing clause references we have not read against would be
exactly the kind of unchecked claim the rest of this document exists to avoid.

## DoD 5220.22-M — legacy status

Offered, labelled, and argued against in the interface itself.

`DOD_5220_22_M_3PASS` exists because operators are sometimes contractually required to
name it. It is presented as `LEGACY` in the UI with the reason attached, and the method's
own evidence string says:

> Superseded by NIST SP 800-88 Rev.1, which states that a single overwrite pass is
> sufficient for any drive manufactured after 2001. On flash media it is actively
> harmful: every extra pass burns program/erase cycles without reaching a single
> remapped block.

`docs/limitations.md` additionally records that the third pass is a fixed character here
and not random, and why that is not a security-relevant difference. The DoD 5220.22-M
overwrite standard was itself withdrawn from the NISPOM in 2007.

## What this tool does not claim, in one place

- No "military-grade". No Gutmann. No unrecoverability guarantee on flash.
- **Destroy is never achieved in software** and is never returned.
- A signature proves the bytes did not change; it does not prove identity. The report
  carries that caveat and the verification screen prints it.
- Verification above 64 GiB is a detection probability, not a proof of absence.
- A per-file erasure is usually unverifiable, and is reported as unverifiable rather
  than as a pass.
- Hidden-area coverage depends on an unlock that can fail; when it fails the region is
  not erased and the report says so.

Every one of these is a place a competing tool would print a green tick. That is the
disagreement, and it is deliberate.
