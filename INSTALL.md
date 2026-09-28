# Installing Sanctum Forensics

This guide covers Linux, Windows and macOS. There are two ways to get a working
copy:

- **A desktop package** (AppImage / `.deb`, `SanctumSetup.exe`, `Sanctum.dmg`).
  The target machine needs no Python and no Node. Packages are built from this
  repository; no pre-built release is published on GitHub yet, so either build
  one yourself (see [Building a desktop package](#building-a-desktop-package))
  or download the artifacts of a `release` workflow run from the repository's
  **Actions** tab.
- **From source** — for development, running the test suite, or when you want
  the exact code in this checkout.

Before you start, know what each platform can do. Whole-drive sanitization
(M1) runs **only on Linux**. File and folder erasure (M2), recovery from an
image (M3), and signed reports work on all three. The full matrix is in
[`docs/platform-support.md`](docs/platform-support.md).

| | Linux | Windows | macOS |
|---|---|---|---|
| Supported OS | x86_64; packages run on Debian 12 and Ubuntu 22.04, source verified on Fedora 44 | Windows 10 1809+ / 11, x64 | macOS 12+ (arm64 package; source install on Intel too) |
| Whole-drive erase (M1) | yes, through the root helper | refused, with the reason | refused, with the reason |
| File / folder erase (M2) | yes | yes | yes (verification is not possible on APFS; reported, not claimed) |
| Recovery from image (M3) | yes | yes | yes |

---

## Requirements for a source install

| Tool | Version | Why |
|---|---|---|
| Python | **3.11 exactly** | `pyproject.toml` pins `==3.11.*`. 3.12+ and 3.10 are refused by pip. |
| Node.js + npm | 20 or newer (CI uses 22) | Builds the UI bundle in `ui/dist/`. Without it the API runs headless. |
| Git | any | To clone. |
| C compiler | Linux and macOS only | `libewf-python` has no cp311 wheel for Linux or for Apple Silicon and compiles from source. Windows gets a wheel. |

The host's default `python3` is often **not** 3.11 (Fedora 44 ships 3.14,
Ubuntu 24.04 ships 3.12). Install 3.11 alongside it; do not replace the
system interpreter.

---

## Linux

### Option A: desktop package

AppImage (any x86_64 distribution with glibc 2.31 or newer, when built with
the portable script):

```bash
chmod +x Sanctum-<ver>-x86_64.AppImage
./Sanctum-<ver>-x86_64.AppImage
# Without FUSE 2 on the host:
./Sanctum-<ver>-x86_64.AppImage --appimage-extract-and-run
```

`.deb` (Debian, Ubuntu):

```bash
sudo apt install ./sanctum_<ver>_amd64.deb
sanctum          # or "Sanctum" in the application menu
```

The app opens in your default browser on a private loopback URL.

### Option B: from source

**1. System packages**

Debian 12 / Ubuntu 22.04:

```bash
sudo apt update
sudo apt install -y git build-essential pkg-config python3.11 python3.11-venv python3.11-dev \
    nodejs npm ntfs-3g exfatprogs dosfstools hdparm nvme-cli sleuthkit
```

Ubuntu 24.04 does not ship Python 3.11; add the deadsnakes PPA first:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
# then run the apt install line above
```

If your distribution's `nodejs` is older than 20, install Node 20+ from
[nodejs.org](https://nodejs.org/) or NodeSource instead.

Fedora:

```bash
sudo dnf install -y git gcc gcc-c++ make python3.11 python3.11-devel nodejs npm \
    ntfs-3g exfatprogs dosfstools hdparm nvme-cli sleuthkit
```

`hdparm` and `nvme-cli` are what the capability probe reads to choose an
erase method; `sedutil-cli` (not packaged by most distributions) adds Opal
crypto erase. Without them the probe cannot see firmware Purge support, and
the app will not offer it. `sleuthkit` is a debugging aid only.

**2. Clone and install**

```bash
git clone https://github.com/Invinciblx777/sanctum-forensics.git
cd sanctum-forensics
make install                     # creates .venv with python3.11, installs .[dev]
source .venv/bin/activate
python -c "import pytsk3, pyewf; print('native bindings OK')"
```

On Debian/Ubuntu, `./scripts/devsetup.sh` does steps 1 and 2 in one go.

**3. Build the UI**

```bash
(cd ui && npm ci && npm run build)
```

**4. Check and run**

```bash
make check       # ruff, mypy --strict, pytest; optional but recommended
make run         # prints http://127.0.0.1:8787/session/<token>
```

Open the `/session/<token>` URL it prints. Any other URL, including the bare
`http://127.0.0.1:8787`, is refused with 401.

To run the desktop launcher (picks a free port and opens the browser for you)
instead: `python -m api.desktop`.

### Linux: whole-drive work

Whole-drive sanitization needs the privileged helper. The UI and API never run
as root; a human starts the helper with `sudo`, in its own terminal:

```bash
sudo mkdir -p /var/lib/sanctum
sudo .venv/bin/python -m helper --operator-uid "$(id -u)" --state-dir /var/lib/sanctum
```

Then, as yourself, in a second terminal:

```bash
SANCTUM_HELPER_SOCKET=/run/sanctum/helper.sock \
SANCTUM_STATE_DIR=/var/lib/sanctum \
.venv/bin/python -m api.main
```

With a package, replace `.venv/bin/python -m helper` with
`./Sanctum-<ver>-x86_64.AppImage --appimage-extract-and-run helper` or
`/opt/sanctum/Sanctum helper`.

Check `/health` before any real job: its `limitations` list must **not**
contain `HELPER_IN_PROCESS`. The full procedure, including creating the signing
key before the first ledger entry, is in
[user manual §3](docs/user-manual.md#3-starting-it).

---

## Windows

### Option A: installer

1. Run `SanctumSetup.exe`. The installer is **unsigned**, so SmartScreen shows
   *Windows protected your PC*; choose **More info → Run anyway**.
2. It installs per-user by default and does not ask for Administrator.
3. Start **Sanctum** from the Start menu. It opens in its own window (WebView2).

Uninstall from **Settings → Apps**. The ledger and reports in
`%LOCALAPPDATA%\Sanctum` are kept on purpose; delete that folder yourself if
you want them gone.

### Option B: from source

**1. Install the tools** (PowerShell):

```powershell
winget install -e --id Python.Python.3.11
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Git.Git
```

Close and reopen PowerShell so the new `PATH` is picked up. No compiler is
needed: `pytsk3` and `libewf-python` both ship Windows wheels for Python 3.11.

**2. Clone and install**

```powershell
git clone https://github.com/Invinciblx777/sanctum-forensics.git
cd sanctum-forensics
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install --constraint constraints.txt -e ".[dev,desktop]"
```

If PowerShell refuses to run `.venv\Scripts\Activate.ps1`, you do not need
it; every command here calls `.venv\Scripts\python` directly.

**3. Build the UI**

```powershell
cd ui; npm ci; npm run build; cd ..
```

**4. Run**

```powershell
.venv\Scripts\python -m api.desktop     # opens a native window
# or the plain server, then open the /session/<token> URL it prints:
.venv\Scripts\python -m api.main
```

Tests: `.venv\Scripts\python -m pytest`. The `Makefile` also works from Git
Bash or any shell with GNU make; it detects the Windows venv layout.

### Windows: what to expect

- Whole-drive sanitization is **refused** on Windows, with the reason. To
  sanitize a whole drive on a Windows machine, boot a Linux live USB and run
  the Linux AppImage on the same hardware.
- File-erase verification reads the disk back; without elevation it is
  reported as *not verified*, never as a pass. The app never asks to run as
  Administrator.

---

## macOS

### Option A: disk image

1. Open `Sanctum.dmg` and drag **Sanctum** to **Applications**.
2. The build is ad-hoc signed and **not notarized**, so Gatekeeper blocks a
   double-click the first time. Right-click **Sanctum** → **Open** → **Open**.
   On macOS 15 and later, if there is no *Open* button, go to
   **System Settings → Privacy & Security** and choose **Open Anyway**.
3. Sanctum opens in its own window.

The published package is arm64 (Apple Silicon). On an Intel Mac, install from
source.

### Option B: from source

**1. Install the tools**

```bash
xcode-select --install                 # C compiler for libewf-python
brew install python@3.11 node git     # Homebrew: https://brew.sh
```

**2. Clone and install**

```bash
git clone https://github.com/Invinciblx777/sanctum-forensics.git
cd sanctum-forensics
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python scripts/ci_install.py dev,desktop
```

`libewf-python` has no Apple Silicon wheel and compiles from source. If that
compile fails, `scripts/ci_install.py` retries without it and prints a note:
everything works except reading and writing E01 images.

**3. Build the UI**

```bash
(cd ui && npm ci && npm run build)
```

**4. Run**

```bash
python -m api.desktop       # opens a native window
# or: make run, then open the /session/<token> URL it prints
```

### macOS: what to expect

- Whole-drive sanitization is **refused** on macOS, with the reason. macOS
  purges its own internal storage through *Erase All Content and Settings*,
  which the app names but does not perform.
- File erasure runs on APFS, but APFS is copy-on-write, so the erase cannot be
  verified. The report says *not verifiable* and gives the reason.

---

## Docker (API, UI and recovery only)

```bash
docker build -t sanctum-forensics .          # or: podman build -t sanctum-forensics .
docker run --rm --network host -e SANCTUM_STATE_DIR=/var/lib/sanctum sanctum-forensics
```

`--network host` is required because the API binds `127.0.0.1` only. The image
has no privileged helper, so device operations inside it fail rather than
escalate. It is the one build that can **write** E01 images; see
[`docs/technical.md`](docs/technical.md).

---

## Building a desktop package

| Platform | Prerequisites | Command | Output |
|---|---|---|---|
| Linux (portable) | podman or Docker, Node 20+ | `(cd ui && npm ci && npm run build) && bash scripts/build-linux-portable.sh` | `dist/Sanctum-<ver>-x86_64.AppImage`, `dist/sanctum_<ver>_amd64.deb` |
| Linux (host only) | Python 3.11, Node 20+ | `make package-linux` | same, but only runs on glibc at least the build host's |
| Windows | Python 3.11, Node 20+, [Inno Setup 6](https://jrsoftware.org/isinfo.php) | `pwsh scripts\build-windows.ps1` | `dist\SanctumSetup.exe` |
| macOS | Python 3.11, Node 20+, Xcode command line tools | `bash scripts/build-macos.sh` | `dist/Sanctum.dmg` |

Signing, notarization, upgrades and the packaging limits are in
[`docs/packaging.md`](docs/packaging.md).

---

## Where your data goes

Ledger, reports, signing keys and recovered files live in the per-user state
directory. Uninstalling the app never deletes it.

| Platform | Default location |
|---|---|
| Linux | `~/.local/share/sanctum` |
| Windows | `%LOCALAPPDATA%\Sanctum` |
| macOS | `~/Library/Application Support/Sanctum` |

Set `SANCTUM_STATE_DIR` to use another directory.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ERROR: Package 'sanctum-forensics' requires a different Python` | The venv is not Python 3.11. Delete `.venv` and recreate it with `python3.11 -m venv .venv` (or `py -3.11` on Windows). |
| `error: command 'gcc' failed` while installing `libewf-python` | No C compiler or Python headers. Linux: install `build-essential python3.11-dev` (Debian/Ubuntu) or `gcc python3.11-devel` (Fedora). macOS: `xcode-select --install`. |
| `libewf_handle_open: write access currently not supported - compiled without zlib` | The stock `libewf-python` reads E01 but cannot write it. Acquire to raw, or use the Docker image or `scripts/build-libewf-python.sh`. |
| The page is blank, or `/health` shows `"ui_bundled": false` | The UI was not built. Run `npm ci && npm run build` in `ui/`. |
| Every request returns 401 | Open the `/session/<token>` URL the server printed, not the bare address. A new token is minted each start. |
| `/health` lists `HELPER_IN_PROCESS` (Linux) | The API did not find the helper socket. Check the helper terminal is still running and `SANCTUM_HELPER_SOCKET` matches the path it printed. |
| `hdparm could not read /dev/sdX: permission denied.` | Same cause as above: a privilege failure, not a statement about the drive. |
| AppImage: `dlopen(): error loading libfuse.so.2` | Run it with `--appimage-extract-and-run`, or install FUSE 2 (`libfuse2` / `fuse-libs`). |
| Windows: SmartScreen blocks the installer | The installer is unsigned. **More info → Run anyway**. |
| macOS: *"Sanctum" cannot be opened* | Not notarized. Right-click → Open, or System Settings → Privacy & Security → Open Anyway. |

Next: the [user manual](docs/user-manual.md) walks through sanitizing a drive,
erasing files, recovering evidence and verifying a report.
