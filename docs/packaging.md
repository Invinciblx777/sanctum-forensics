# Packaging and running the desktop app

One product, three native packages. Each is the same Python application
(API + UI bundle + platform adapters) frozen by PyInstaller into a
self-contained runtime, so **the target machine needs no Python, no Node and
no developer tools**. The app serves its UI on a private loopback port and
opens it in a window; nothing is fetched from the network, ever.

| Platform | Artifact | Built by | Built and driven |
|---|---|---|---|
| Linux | `Sanctum-<ver>-x86_64.AppImage`, `sanctum_<ver>_amd64.deb` | `scripts/build-linux-portable.sh` (glibc 2.31 container) or `scripts/build-linux.sh` (host) | **Yes** — locally and on `ubuntu-22.04` in CI; installed and run on Debian 12 and Ubuntu 22.04; 24 of 24 packaged checks |
| Windows 10 1809+ / 11, x64 | `SanctumSetup.exe` | `scripts/build-windows.ps1` (PyInstaller + Inno Setup 6) | **Yes, in CI** — built on `windows-latest`, installed silently, the installed app driven, then uninstalled. Unsigned. |
| macOS 12+ (arm64) | `Sanctum.dmg` containing `Sanctum.app` | `scripts/build-macos.sh` (PyInstaller + `hdiutil`) | **Yes, in CI** — built on `macos-14`, DMG mounted, `Sanctum.app` driven through a folder erase and a signed certificate; 24 of 24 checks. Unsigned, **not notarized**. |

None of these has been installed by a human on a physical Windows machine or
Mac, and none has touched removable media; see
[`validation/hardware-platform-matrix.md`](validation/hardware-platform-matrix.md).

## Build commands

### Linux (distributable)

```bash
cd ui && npm ci && npm run build && cd ..
bash scripts/build-linux-portable.sh        # needs podman (or CONTAINER=docker)
ls dist/   # Sanctum-0.0.0-x86_64.AppImage  sanctum_0.0.0_amd64.deb  SHA256SUMS-linux.txt
```

`scripts/build-linux.sh` builds the same packages on the host directly. That
build only runs on systems whose glibc is at least the build host's (a
Fedora 44 build needs glibc 2.38 and fails on Debian 12); use it for local
testing, and the portable build for anything handed to someone else.

### Windows

From a Developer PowerShell with Python 3.11 (`py -3.11`), Node 20+ and
Inno Setup 6 installed:

```powershell
pwsh scripts\build-windows.ps1
# dist\SanctumSetup.exe
```

### macOS

With Python 3.11, Node 20+ and the Xcode command line tools:

```bash
bash scripts/build-macos.sh
# dist/Sanctum.dmg (and dist/Sanctum-<ver>.dmg)
```

### CI

`.github/workflows/release.yml` builds all three on `ubuntu-22.04`,
`windows-latest` and `macos-14`, after running that platform's validation
suite and bundling the result (see *Validation record* below).

## Running

| Platform | Installed | From source (developer) |
|---|---|---|
| Linux | `./Sanctum-<ver>-x86_64.AppImage` (or `--appimage-extract-and-run` without FUSE); `.deb`: `sudo apt install ./sanctum_<ver>_amd64.deb`, then *Sanctum* in the app menu or `sanctum` | `make run`, then open the `/session/<token>` URL it prints; or `python -m api.desktop` |
| Windows | Run `SanctumSetup.exe`, then *Sanctum* in the Start menu | `py -3.11 -m venv .venv; .venv\Scripts\python -m pip install -c constraints.txt -e .[dev]; .venv\Scripts\python -m api.desktop` |
| macOS | Open `Sanctum.dmg`, drag *Sanctum* to Applications, open it (right-click > Open the first time: the build is unsigned) | `python3.11 -m venv .venv && .venv/bin/pip install -c constraints.txt -e .[dev] && .venv/bin/python -m api.desktop` |

### Linux whole-drive work

Whole-drive sanitization needs the privileged helper. The UI never runs as
root:

```bash
sudo mkdir -p /var/lib/sanctum
sudo ./Sanctum-<ver>-x86_64.AppImage --appimage-extract-and-run helper \
     --operator-uid "$(id -u)" --state-dir /var/lib/sanctum
# then, as yourself:
SANCTUM_HELPER_SOCKET=/run/sanctum/helper.sock SANCTUM_STATE_DIR=/var/lib/sanctum \
     ./Sanctum-<ver>-x86_64.AppImage
```

(With the `.deb`: `sudo /opt/sanctum/Sanctum helper ...` and `sanctum`.)

## What the launcher does

`api/desktop.py`, the entry point of every package:

1. Picks a free port on 127.0.0.1.
2. Generates a 32-byte session token and passes it to the API in the
   environment. Every request without the matching `HttpOnly`,
   `SameSite=Strict` cookie gets 401 (`api/security.py`).
3. Starts the API and waits for `/health`.
4. Opens `http://127.0.0.1:<port>/session/<token>` - in a native window
   (WebView2 on Windows, WKWebView on macOS) when `pywebview` is bundled,
   otherwise in the default browser.
5. Stops when the window closes, or when *Quit Sanctum* is pressed in the
   sidebar.

State lives in the per-user data directory: `~/.local/share/sanctum`,
`%LOCALAPPDATA%\Sanctum`, `~/Library/Application Support/Sanctum`, or
`SANCTUM_STATE_DIR`.

## Upgrades and uninstall

- **Windows:** the installer has a fixed `AppId`, so a newer
  `SanctumSetup.exe` upgrades in place and replaces the runtime wholesale.
  Uninstall (Settings > Apps) removes the program; the ledger and reports in
  `%LOCALAPPDATA%\Sanctum` are **kept**, because deleting an audit trail is
  not an uninstaller's decision. The installer is per-user by default and
  never requests elevation; an administrator can choose an all-users install.
- **Linux:** `apt install` a newer `.deb` upgrades; `apt remove sanctum`
  removes `/opt/sanctum`, `/usr/bin/sanctum` and the desktop entry, and keeps
  `~/.local/share/sanctum`. The AppImage is a single file: replace it.
- **macOS:** replace `Sanctum.app` in Applications; delete it to uninstall.
  `~/Library/Application Support/Sanctum` is kept.

## Signing - not performed

| | Status | Remaining step |
|---|---|---|
| Windows Authenticode | **Unsigned.** SmartScreen will warn. | Obtain a code-signing certificate; run `scripts/build-windows.ps1 -SignCommand "signtool sign /fd sha256 /tr <tsa> /td sha256 /f cert.pfx /p ..."`. |
| macOS Developer ID | **Ad-hoc signed only** (PyInstaller). Gatekeeper blocks a double-click on other Macs. | `CODESIGN_IDENTITY="Developer ID Application: ..." bash scripts/build-macos.sh` |
| macOS notarization | **Not performed.** | `xcrun notarytool submit dist/Sanctum.dmg --keychain-profile <profile> --wait && xcrun stapler staple dist/Sanctum.dmg` |
| Linux | AppImage and `.deb` are unsigned; `SHA256SUMS-linux.txt` is produced. | Sign the checksum file with the release key. |

## Validation record

`scripts/record_platform_validation.py` runs the platform suites with pytest
and writes `core/platform/validation_record.json`; the spec bundles it. The
app's capability screen reads it: a suite not recorded as `PASS` on the
platform the app is running on leaves the capabilities it backs at
**Unverified**. A build nobody tested therefore says so on its own screen.

The record carries two shapes. `suites` is what the capability model gates
on. `features` is one row per platform feature - platform, OS, architecture,
commit, the tests behind it, the result, the date, the evidence file and the
limitations that still apply - so the record never says "Windows verified",
only which feature was validated, by what, and when. The CI jobs produce one
record per runner and `--merge` folds them together, preferring a row that
says something over one that says NOT RUN.

## Known packaging limits

- **E01 writing** needs the patched libewf build (`scripts/build-libewf-python.sh`,
  used by the Docker image). The desktop packages use the stock
  `libewf-python`, which reads E01 but cannot write it; acquisition to raw
  works. On macOS `libewf-python` compiles from source, and CI installs
  without it if that fails (E01 then unavailable in that environment).
- **AppImage and FUSE:** hosts without FUSE 2 need
  `--appimage-extract-and-run`.
- **Linux desktop window:** the Linux packages open the default browser; a
  native window there would need GTK/WebKit bindings the AppImage does not
  carry.
- **Architectures:** x86_64 Linux and Windows; macOS builds for the build
  machine's architecture (arm64 on `macos-14`). No universal2 build.
