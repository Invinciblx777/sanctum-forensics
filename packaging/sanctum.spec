# PyInstaller spec for every desktop build: Linux, Windows and macOS.
#
#   pyinstaller --noconfirm --clean packaging/sanctum.spec
#
# Produces dist/Sanctum/ (onedir) everywhere, plus dist/Sanctum.app on macOS.
# The build scripts in scripts/ wrap this and then make the platform package
# (AppImage/.deb, SanctumSetup.exe, Sanctum.dmg). Run it from the repository
# root after `npm run build` in ui/ and after `pip install .[desktop]`.
#
# Everything the app imports lazily (core.device, core.erase.drive, the
# platform adapters, every API router) is listed explicitly: those imports
# live inside functions, where PyInstaller's static analysis cannot see them,
# and a module missing from a frozen build fails only when that screen is
# opened - which is exactly when it is least convenient to find out.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - defined by PyInstaller
ICONS = ROOT / "build" / "icons"
UI_DIST = ROOT / "ui" / "dist"
if not (UI_DIST / "index.html").is_file():
    raise SystemExit("ui/dist is missing: run `npm ci && npm run build` in ui/ first")

hidden = (
    collect_submodules("core")
    + collect_submodules("api")
    + collect_submodules("helper")
    + collect_submodules("uvicorn")
    # reportlab.graphics.barcode imports its symbologies (code128, qr, ...)
    # through exec() of a string, which PyInstaller's analysis cannot see. The
    # certificate draws its QR code through that package; without these the
    # packaged app failed every PDF with ModuleNotFoundError. Until 2026-09-25
    # the old renderer caught the ImportError, so packaged PDFs had silently
    # shipped with no QR code at all.
    + collect_submodules("reportlab.graphics.barcode")
)
datas = [(str(UI_DIST), "ui/dist")]
# What the platform test suites recorded (scripts/record_platform_validation.py).
# Without it every capability backed by a suite ships as UNVERIFIED, which is
# the honest state for a build nobody tested - so its absence is not an error.
RECORD = ROOT / "core" / "platform" / "validation_record.json"
if RECORD.is_file():
    datas.append((str(RECORD), "core/platform"))
# What this build is: version, commit, platform, architecture, date, runtime
# versions (packaging/build_info.py). Missing from an ad-hoc build, and the
# app then shows no build identity rather than an invented one.
BUILD_INFO = ROOT / "core" / "platform" / "build_info.json"
if BUILD_INFO.is_file():
    datas.append((str(BUILD_INFO), "core/platform"))
datas += collect_data_files("reportlab")
datas += copy_metadata("sanctum-forensics")

try:
    import webview  # noqa: F401

    hidden += collect_submodules("webview")
    datas += collect_data_files("webview")
except ImportError:
    pass  # browser fallback; see api/desktop.py

icon = None
if sys.platform == "win32" and (ICONS / "sanctum.ico").is_file():
    icon = str(ICONS / "sanctum.ico")
elif sys.platform == "darwin" and (ICONS / "sanctum.icns").is_file():
    icon = str(ICONS / "sanctum.icns")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "sanctum_entry.py")],
    pathex=[str(ROOT)],
    hiddenimports=hidden,
    datas=datas,
    # Development and test tooling is never shipped.
    excludes=["pytest", "mypy", "ruff", "matplotlib", "testkit", "tests", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sanctum",
    # A console on Linux keeps the helper subcommand and the browser fallback
    # usable from a terminal; Windows and macOS are windowed apps.
    console=sys.platform.startswith("linux"),
    icon=icon,
    # Never embed a UAC manifest asking for elevation: nothing needs it.
    uac_admin=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Sanctum")  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="Sanctum.app",
        icon=icon,
        bundle_identifier="local.sanctum.forensics",
        info_plist={
            "CFBundleShortVersionString": __import__("importlib.metadata").metadata.version("sanctum-forensics"),
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
