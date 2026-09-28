# Demo video script — Sanctum Forensics (SIH 26149)

Built from [`docs/validation/demo-evidence-index.md`](docs/validation/demo-evidence-index.md)
— the repo's own "source of truth for the presentation." **One rule that beats
everything else here: never say a claim this doc doesn't have a row for.**
That discipline is the actual differentiator — say it plainly in the video,
not just follow it.

Population labels — say them out loud whenever you show a result, don't skip
this:

- **SYNTHETIC** — generated test data, run live, real code.
- **REAL DEVICE / TEST STICK ONLY** — a real erase, live, on one disposable
  stick you own. Not a device-class validation. Say so.
- **PHYSICAL** — the one recorded hardware run in `docs/validation/hardware.md`
  (2026-09-05) and the Windows run of 2026-09-27. Shown from the document, not
  reproduced live.

Never claim: government-signed certificates, PKI backing, firmware Purge
having run on real hardware, Windows/macOS whole-drive clear validated on a
device, general fragmented-file recovery "solved," the evidence score as a
probability, or DoD/NIST *compliance* (you use the vocabulary, you don't
claim compliance). Full list: `demo-evidence-index.md` → "What must not be
said."

---

## Cold open (0:00–0:30) — the one sentence

**Show:** Overview screen.

**Say:**

> Sanctum recovers evidence from a disk image without ever writing to it,
> erases a drive only with a method the drive itself reports it supports, and
> signs a tamper-evident record of both. Three modules, one hash-chained
> ledger, one signed report at the end.

Point at the executive summary — note it leads with what is **not**
physically validated yet, not just the wins. That's deliberate; say so:

> Every claim on this screen maps to a test or a recorded run. If we can't
> point at the evidence, we don't say it — and we'll show you that discipline
> live in a minute.

---

## Beat 1 — Devices (0:30–1:00)

**Show:** Devices screen. Hover the capability badge on your test stick.

**Say:**

> This stick reads `CLEAR ONLY`. Not because we chose Clear — the probe found
> no firmware sanitize path on this hardware, so Purge isn't offered. The
> method is selected from what we measure, never from what an operator would
> prefer.

Click a locked row (system disk, or a mounted volume if you have one):

> And this one's blocked — filesystem is mounted, human unmount required. No
> sudo, no auto-unmount. If you didn't know it was mounted, you don't yet
> know what's on it.

*(Optional terminal cut, same beat): run a plan against a device path that
doesn't exist — it refuses cleanly with a reason, exit 2, nothing
substituted. Good 10-second beat if you want a second "it fails honestly"
example.*

---

## Beat 2 — Real device erase, started (1:00–1:45) — TEST STICK ONLY

**Show:** Sanitize screen on your disposable test stick (imaged first, holds
nothing you need).

**Say, before you click anything:**

> This is a real erase of our test stick. It is not a device-class
> validation — that takes a recorded methodology run, which we haven't
> claimed. What you're about to watch is real.

Walk: **REAL DEVICE** card → *Erase this device* → verify backup → plan
appears → tick acknowledgement → type serial → *Approve* → type serial again
→ *Erase*.

**When the plan panel and the write-calibration finding land, this is your
best 30 seconds — don't rush it:**

> It just measured this controller acknowledging a zero-fill about 3x faster
> than it can actually program the medium. So instead of writing zeros and
> calling it an overwrite, the tool substitutes `0xA5` — because NIST SP
> 800-88 Rev 2 defines overwrite as replacing the data with something else,
> and a write that completes faster than the medium can be programmed wasn't
> performed. We know of no other tool at this level that measures this before
> writing.

Let it start (`PREFLIGHT` → `ERASE`, progress moving), then cut away — full
wipes run minutes to tens of minutes depending on device size, and you say
that instead of faking a fast one:

> This will keep running in the background while we cover recovery and the
> report. I'm not going to fake a fast wipe for the camera.

---

## Beat 3 — Recovery (1:45–2:30)

**Show:** `python scripts/demo_fragmented.py` (SYNTHETIC, sub-second) then the
Recovery screen, filtered to HIGH, one candidate opened to **Score
breakdown**.

**Say:**

> Two files, deliberately split into two pieces each, rebuilt — but only
> because their own bytes prove the join: an exact Huffman scan count for
> JPEG, chunk CRCs plus a zlib stream that inflates to exactly the declared
> size for PNG. A reassembled file is capped below our HIGH threshold, always
> — rebuilding something is never as certain as finding it whole.
>
> This number isn't a percentage or a probability. It's an evidence score —
> six components, each shown, each independently checked. Ten thousand out of
> ten thousand means every check fired, not that we're claiming certainty.

---

## Beat 4 — Forensic integrity, and the tamper (2:30–3:15)

**Show:** Audit screen. *Generate signed report* on the recovery job just run
→ *Verify* → **Tamper a scratch copy**.

**Say, before the tamper:**

> Five independent checks. Signature, key fingerprint against the ledger's
> own genesis entry, chain integrity, an independent re-verification of the
> whole chain from the store — not from the copy inside the report — and
> that every referenced blob exists.

Click Tamper — one byte flips in a scratch copy, re-verify live.

> Signature fails. The other four still pass — deliberately, because they're
> independent of the report's own bytes. A forged report can't make the
> ledger store agree with it.

Point at the verdict line:

> And notice it never said `VERIFIED`, even before the tamper — it said
> `VERIFIED_WITH_LIMITATIONS`, because the report itself declares what
> overwrite couldn't reach on flash. We don't round a limited result up to a
> clean pass.

---

## Beat 5 — The honesty layer: capability matrix (3:15–3:45)

**Show:** Platform screen / capability matrix.

**Say:**

> Every capability, every platform, one of seven states — supported,
> implemented-but-unvalidated, device-dependent, platform-limited, requires
> privilege, blocked for safety, not implemented — each with why. This is
> generated from the code, not typed by hand, and a test fails if the
> document drifts from what the resolver actually says.
>
> Most demos you'll see for this problem statement show one platform, fully
> simulated, and call it done. We built the thing that tells you exactly what
> it hasn't proven yet — because a sanitization tool that oversells its own
> guarantees is worse than one that's honest about its gaps.

*(This is the beat to make your differentiation pitch — you don't need to
name other teams; the matrix does the talking.)*

---

## Closer (3:45–4:30) — back to the running wipe

**Show:** cut back to the terminal/progress bar from Beat 2, still running.

**Say:**

> That's the wipe I started three minutes ago. Still running, ETA holding —
> because it was derived from a measurement on this exact device, not a
> datasheet number. It'll finish, verify by reading the medium back, and sign
> a report same as everything else you just watched.
>
> Three modules. One ledger. Nothing claimed that isn't backed by a test or a
> recorded run.

---

## If you have time for one more beat (optional, +45s)

**File erase (M2), desktop traces.** Erase a file that has a thumbnail /
recent-files entry → show the findings list naming exactly what was swept and
what it searched but couldn't remove.

> It doesn't just overwrite the file. It hunts down what the desktop made of
> it — thumbnail, recent-files entry, trash copy — and tells you exactly what
> it found, what it removed, and what it looked for and couldn't.

---

## Before you hit record

- [ ] Test stick is disposable, imaged, holds nothing you need.
- [ ] Helper + API running per `tutorial.md` §1; `GET /health` has no
      `HELPER_IN_PROCESS` / `NO_SIGNING_KEY` limitation.
- [ ] Serial written somewhere you can read without turning to the camera.
- [ ] Know your video's time budget and cut beats top-down from the bottom of
      this list if you're over — Beat 5 and the optional M2 beat go first,
      Beats 1–4 are the core.
- [ ] Skim [`docs/demo/qa.md`](docs/demo/qa.md) once — 29 judge questions,
      sourced answers, in case Q&A is part of the submission format.
- [ ] Say the population label (SYNTHETIC / TEST STICK ONLY / PHYSICAL) every
      single time you show a number. It's not a hedge, it's the pitch.
