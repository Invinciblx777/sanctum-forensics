# Cross-platform demo (SIH 26149)

The point to land: **one sanitization platform, not three applications.** The
same screens, the same capability model and the same certificate on every OS;
what differs is what each OS can honestly do, and the app says so itself.

Use only designated disposable media. Never demonstrate on a disk that holds
data, and never on the laptop's own disk except to show the refusal.

## Linux (the full engine) — about 3 minutes

1. Launch `Sanctum-<ver>-x86_64.AppImage` (helper running as in
   `docs/packaging.md` for whole-drive work).
2. **Platform** screen: Linux, privilege *Privileged helper*, whole-drive Clear
   *Supported with limits*, Purge *Supported with limits* (decided per device),
   each row with its source. Point at a filesystem row: detect ≠ support.
3. **Devices**: the laptop's own disk is locked — *holds the running root
   filesystem*. Select the designated USB stick.
4. **Sanitization**: the step tracker; *Ready to sanitize*; the three answers
   (device, what will happen, can it be verified); the flash limitation; Purge
   listed under *Unavailable, and why* (the USB bridge blocks pass-through) -
   no silent downgrade.
5. *Review plan* → dry run → *Erase this device* → re-read → type the serial.
6. Watch progress; verification result; *Get certificate*; verify it on the
   **Audit** screen.

## Windows — about 2 minutes

1. Run `SanctumSetup.exe` (no administrator prompt), open Sanctum from Start.
2. **Platform**: Windows 11, *Standard user*, file erase and discovery with
   their sources; whole-drive *Unsupported* with the reason. If the validation
   record for Windows has not been recorded, file erase shows *Unverified* —
   say so; that is the design working.
3. **Devices**: the USB stick with size and bus; the internal disk locked as
   the boot/system disk with the Windows reasons (IsBoot, system volume, page
   file).
4. Select the stick → **Sanitization not available**: the reason, *what to do
   instead* (the Linux build), *no operation was performed*.
5. **File eraser**: erase a scratch folder on the stick → per-file results,
   the NTFS residual findings → *Get certificate*.

## macOS — about 2 minutes

1. Open `Sanctum.dmg`, drag to Applications, open (right-click > Open: the
   build is unsigned and not notarized - say so).
2. **Platform**: macOS, APFS rows showing erase *Runs, not verifiable*
   (copy-on-write), whole-drive *Unsupported*.
3. **Devices**: the internal SSD locked - *the running macOS boots from an
   APFS container on this disk*; the stick listed.
4. Stick → *Sanitization not available* with Apple's own path for internal
   storage named (*Erase All Content and Settings*).
5. **File eraser** on the stick; certificate.

## The close

Put the three **Platform** screens side by side: same layout, same statuses,
different answers - every one of them traced to a probe. "A platform that
honestly says *unsupported on this device* is a stronger forensic product than
one that shows a green check and performs an unverified wipe."
