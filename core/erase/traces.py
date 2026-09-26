"""Traces the desktop keeps of an erased file: found, reported, and removed.

Overwriting a file's extents does not reach what the desktop made of the file.
A file manager keeps a thumbnail named by the MD5 of the file's URI. The
recent-files list keeps its path. An earlier delete may have left a whole copy
in the Trash or the Recycle Bin, with a record of where it came from. Each of
those is a trace of exactly the file the operator asked to destroy, and
overwriting the file touches none of them.

This module runs after the erase. It finds those traces and, on a real run,
removes the ones it can tie to an erased path **on evidence**:

* a thumbnail whose name is the MD5 of the erased file's URI, or whose
  ``Thumb::URI`` names a file inside an erased folder (the freedesktop
  thumbnail specification);
* an entry in GTK's ``recently-used.xbel`` whose ``href`` decodes to the path,
  and a KDE ``RecentDocuments`` link whose ``URL`` does;
* a Trash item whose ``.trashinfo`` names the path, or names a folder that held
  it (the freedesktop Trash specification), in the home Trash and in the
  ``.Trash-$uid`` directory of the volume the file was on;
* a Recycle Bin ``$R`` item whose ``$I`` record names the path;
* a Windows Recent shortcut whose LinkInfo names the path ([MS-SHLLINK] 2.3).

Anything weaker is reported with ``exact=False`` and never removed. The macOS
Trash is the case in point: it records where an item came from only inside its
``.DS_Store``, which this module does not parse, so a same-name item there is a
*possible* copy, left for the operator to judge.

**Removal reuses the erase.** A trace that is a file - a thumbnail, a Trash
copy, a shortcut - goes through :func:`core.erase.files.erase_one`, the same
steps as a target, so it is overwritten, renamed and unlinked, and the same
residual findings apply to it. An entry inside a shared list is cut out and the
list is overwritten in place, padded to its old length, so the bytes that named
the file are replaced rather than left behind in a freed block.

A dry run searches and reports everything and removes nothing.
"""

from __future__ import annotations

import hashlib
import html
import ntpath
import os
import posixpath
import re
import stat as stat_mod
import struct
import xml.etree.ElementTree as ET
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote, unquote

import structlog

from core.erase.sink import LedgerSink
from core.errors import ConfirmationMismatch, SystemDiskRefused
from core.models import (
    FileEraseOptions,
    FileEraseRecord,
    Progress,
    TraceRecord,
    TraceSweepResult,
)
from core.platform.host import family
from core.platform.model import PlatformFamily

__all__ = [
    "PHASE",
    "TraceKind",
    "TraceLocations",
    "default_locations",
    "file_uris",
    "find_traces",
    "locations_for",
    "path_from_uri",
    "sweep",
]

logger = structlog.get_logger(__name__)

#: The progress phase the sweep reports under.
PHASE = "TRACES"


class TraceKind(StrEnum):
    """What a trace is. The report prints these."""

    THUMBNAIL = "THUMBNAIL"
    #: A failed-thumbnail marker: no image, but it names the file.
    THUMBNAIL_FAILURE = "THUMBNAIL_FAILURE"
    RECENT_ENTRY = "RECENT_ENTRY"
    RECENT_DOCUMENT = "RECENT_DOCUMENT"
    TRASH_COPY = "TRASH_COPY"
    TRASH_RECORD = "TRASH_RECORD"
    RECYCLE_BIN_COPY = "RECYCLE_BIN_COPY"
    RECYCLE_BIN_RECORD = "RECYCLE_BIN_RECORD"
    RECENT_SHORTCUT = "RECENT_SHORTCUT"
    POSSIBLE_COPY = "POSSIBLE_COPY"


#: Places each platform keeps traces that this sweep does not search. Printed
#: in the report, so its reader knows where the sweep stopped.
NOT_SEARCHED: dict[str, list[str]] = {
    "linux": [
        "Caches and history an application keeps for itself: office suites, "
        "image viewers, editors and browsers.",
        "Desktop search and activity indexes: GNOME's Tracker or LocalSearch "
        "and the KDE activity database.",
        "Backups, filesystem snapshots and sync clients.",
    ],
    "windows": [
        "Jump lists (AutomaticDestinations and CustomDestinations).",
        "The thumbnail databases (thumbcache_*.db) and the Windows Search index.",
        "Recent lists an application keeps for itself, such as Office's, and "
        "the RecentDocs registry key.",
        "Volume Shadow Copies, File History, OneDrive and other sync clients.",
    ],
    "macos": [
        "The QuickLook thumbnail cache and the Spotlight index.",
        "Recent items (the shared file lists) and recent lists an application "
        "keeps for itself.",
        "Time Machine and APFS snapshots, iCloud Drive and other sync clients.",
    ],
}

#: freedesktop thumbnail size directories.
_THUMBNAIL_SIZES = ("normal", "large", "x-large", "xx-large")

#: Characters left unescaped in a file URI's path by the encoders a thumbnail
#: may have been named under: GLib's ``g_filename_to_uri``, RFC 3986's pchar
#: set (Qt's fully encoded form keeps the same ones), and a strict encoder that
#: escapes everything but ``/``. The MD5 differs with each, so each is tried.
_URI_SAFE = ("!$&'()*+,-./:=@_~", "!$&'()*+,;=:@/-._~", "/")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: A thumbnail's text chunks come before its image data; this much is read.
_PNG_HEAD = 64 * 1024

#: How many cached thumbnails are read, per cache, for thumbnails of files
#: inside an erased folder. The MD5 lookups for the erased files themselves
#: are not bounded by this.
SCAN_LIMIT = 20_000

#: Size bounds for the small files a trace is recorded in.
_RECORD_LIMIT = 64 * 1024
_LIST_LIMIT = 32 * 1024 * 1024

_XBEL_BOOKMARK = re.compile(
    rb"""[ \t]*<bookmark\b[^>]*?\bhref=(["'])(.*?)\1[^>]*?"""
    rb"""(?:/>|>.*?</bookmark\s*>)[ \t]*(?:\r?\n)?""",
    re.DOTALL,
)


# --------------------------------------------------------------------------
# Where to look
# --------------------------------------------------------------------------


def _mount_top(path: str) -> Path | None:
    """The mount point of the volume that holds ``path``.

    The path itself is usually gone by now, so the walk starts at its nearest
    surviving ancestor.
    """
    current = Path(os.path.abspath(path))
    while not os.path.lexists(current) and current.parent != current:
        current = current.parent
    while not os.path.ismount(current):
        if current.parent == current:
            return None
        current = current.parent
    return current


def _drive_top(path: str) -> Path | None:
    """The root of the Windows volume ``path`` is on: ``C:\\`` for ``C:\\x``."""
    drive, _ = ntpath.splitdrive(path)
    return Path(drive + "\\") if drive else None


@dataclass(frozen=True)
class TraceLocations:
    """Where one host keeps traces.

    :func:`locations_for` builds it for a platform; a test builds its own.
    """

    family: PlatformFamily
    home: Path | None = None
    #: freedesktop thumbnail caches.
    thumbnail_roots: tuple[Path, ...] = ()
    #: GTK recent-files lists (XBEL).
    recent_lists: tuple[Path, ...] = ()
    #: KDE's one-link-per-document recent list.
    recent_document_dirs: tuple[Path, ...] = ()
    #: The home Trash.
    home_trash: Path | None = None
    #: The user's uid, for per-volume Trash directories. None: not searched.
    uid: int | None = None
    #: Windows Recent shortcuts.
    recent_shortcut_dirs: tuple[Path, ...] = ()
    #: Search ``<volume>\\$Recycle.Bin`` on each erased path's volume.
    recycle_bin: bool = False
    #: The macOS Trash.
    mac_trash: Path | None = None
    #: Finds the top of the volume that holds a path. Injected, so a test never
    #: walks up the host's own mounts.
    volume_top: Callable[[str], Path | None] = field(default=_mount_top)


def _xdg_dir(env: Mapping[str, str], name: str, fallback: Path) -> Path:
    """An XDG base directory. A relative value is invalid and is ignored."""
    value = env.get(name, "")
    return Path(value) if value and os.path.isabs(value) else fallback


def locations_for(
    platform: PlatformFamily,
    env: Mapping[str, str],
    home: Path,
    *,
    uid: int | None = None,
) -> TraceLocations:
    """The places ``platform`` keeps traces, for the user whose home is ``home``."""
    if platform == "windows":
        appdata = env.get("APPDATA", "")
        recent = (
            (Path(appdata) / "Microsoft" / "Windows" / "Recent",) if appdata else ()
        )
        return TraceLocations(
            family=platform,
            home=home,
            recent_shortcut_dirs=recent,
            recycle_bin=True,
            volume_top=_drive_top,
        )
    if platform == "macos":
        return TraceLocations(family=platform, home=home, mac_trash=home / ".Trash")
    cache = _xdg_dir(env, "XDG_CACHE_HOME", home / ".cache")
    data = _xdg_dir(env, "XDG_DATA_HOME", home / ".local" / "share")
    return TraceLocations(
        family=platform,
        home=home,
        thumbnail_roots=(cache / "thumbnails", home / ".thumbnails"),
        recent_lists=(data / "recently-used.xbel",),
        recent_document_dirs=(data / "RecentDocuments",),
        home_trash=data / "Trash",
        uid=uid,
    )


def default_locations() -> TraceLocations:
    """This host's places, for the user the process runs as."""
    uid = os.getuid() if hasattr(os, "getuid") else None
    return locations_for(family(), os.environ, Path.home(), uid=uid)


# --------------------------------------------------------------------------
# URIs
# --------------------------------------------------------------------------


def file_uris(path: str) -> list[str]:
    """The ``file://`` URIs a desktop may have written for ``path``, deduplicated."""
    raw = os.fsencode(path)
    uris: list[str] = []
    for safe in _URI_SAFE:
        uri = "file://" + quote(raw, safe=safe)
        if uri not in uris:
            uris.append(uri)
    return uris


def path_from_uri(uri: str) -> str | None:
    """The local path a ``file:`` URI names, or None for any other URI."""
    if not uri.startswith("file://"):
        return None
    rest = uri[len("file://") :]
    if rest.startswith("localhost/"):
        rest = rest[len("localhost") :]
    if not rest.startswith("/"):
        return None  # a host other than this one
    return unquote(rest, errors="surrogateescape")


# --------------------------------------------------------------------------
# Reading the places, safely
# --------------------------------------------------------------------------


def _read(path: Path, limit: int, *, whole: bool) -> bytes | None:
    """Bytes of a regular file, never following a link or blocking on a FIFO.

    ``whole`` returns None for a file larger than ``limit``: a list that is
    rewritten must have been read in full. Otherwise the first ``limit`` bytes.
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat_mod.S_ISREG(info.st_mode):
            return None
        if whole and info.st_size > limit:
            return None
        chunks: list[bytes] = []
        remaining = limit
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _listdir(directory: Path) -> list[Path] | None:
    """Entries of a directory, sorted; None when it is absent or unreadable."""
    try:
        return sorted(Path(entry.path) for entry in os.scandir(directory))
    except OSError:
        return None


def _is_real_dir(path: Path) -> bool:
    """A directory, and not a link to one."""
    try:
        return stat_mod.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def png_text(data: bytes) -> dict[str, str]:
    """The ``tEXt`` chunks that come before a PNG's image data."""
    found: dict[str, str] = {}
    if not data.startswith(_PNG_SIGNATURE):
        return found
    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        if kind in (b"IDAT", b"IEND"):
            break
        body = data[offset + 8 : offset + 8 + length]
        if kind == b"tEXt" and len(body) == length:
            key, _, value = body.partition(b"\x00")
            found[key.decode("latin-1")] = value.decode("latin-1")
        offset += 12 + length
    return found


def _trashinfo_path(data: bytes) -> str | None:
    """The ``Path=`` of a ``.trashinfo`` file's ``[Trash Info]`` group, decoded."""
    in_group = False
    for line in data.decode("utf-8", "surrogateescape").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_group = stripped == "[Trash Info]"
        elif in_group and stripped.startswith("Path="):
            value = stripped[len("Path=") :]
            return unquote(value, errors="surrogateescape") or None
    return None


def recycle_record(data: bytes) -> tuple[str, int] | None:
    """``(original path, size)`` from a Recycle Bin ``$I`` record.

    Version 1 (Vista to 8.1) holds the path in a fixed 520-byte field; version
    2 (Windows 10 and later) gives its length in characters first.
    """
    if len(data) < 24:
        return None
    version, size, _deleted = struct.unpack_from("<qqq", data, 0)
    if version == 1:
        raw = data[24 : 24 + 520]
    elif version == 2 and len(data) >= 28:
        (chars,) = struct.unpack_from("<i", data, 24)
        if chars <= 0:
            return None
        raw = data[28 : 28 + 2 * chars]
    else:
        return None
    text = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
    return (text, size) if text else None


def _cstring(buffer: bytes, offset: int, *, wide: bool) -> str:
    """A NUL-terminated string at ``offset``; empty when out of range."""
    if offset <= 0 or offset >= len(buffer):
        return ""
    if wide:
        end = offset
        while end + 1 < len(buffer) and buffer[end : end + 2] != b"\x00\x00":
            end += 2
        return buffer[offset:end].decode("utf-16-le", errors="replace")
    end = buffer.find(b"\x00", offset)
    raw = buffer[offset : end if end >= 0 else len(buffer)]
    return raw.decode("mbcs" if os.name == "nt" else "cp1252", errors="replace")


def shortcut_target(data: bytes) -> str | None:
    """The local path a Shell Link (``.lnk``) points at, from its LinkInfo.

    [MS-SHLLINK] 2.3: the path is ``LocalBasePath`` followed by
    ``CommonPathSuffix``, in their Unicode forms when the header carries them.
    """
    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return None
    (flags,) = struct.unpack_from("<I", data, 0x14)
    offset = 0x4C
    if flags & 0x1:  # HasLinkTargetIDList
        if offset + 2 > len(data):
            return None
        (id_list_size,) = struct.unpack_from("<H", data, offset)
        offset += 2 + id_list_size
    if not flags & 0x2:  # HasLinkInfo
        return None
    if offset + 0x1C > len(data):
        return None
    (info_size, header_size, info_flags, _volume, base, _network, suffix) = (
        struct.unpack_from("<7I", data, offset)
    )
    info = data[offset : offset + info_size]
    if len(info) < 0x1C:
        return None
    if not info_flags & 0x1:  # VolumeIDAndLocalBasePath
        return None
    if header_size >= 0x24 and len(info) >= 0x24:
        wide_base, wide_suffix = struct.unpack_from("<2I", info, 0x1C)
        if wide_base:
            head = _cstring(info, wide_base, wide=True)
            return (head + _cstring(info, wide_suffix, wide=True)) or None
    head = _cstring(info, base, wide=False)
    return (head + _cstring(info, suffix, wide=False)) or None


# --------------------------------------------------------------------------
# What was erased
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Erased:
    """One erased path and its comparison key."""

    path: str
    key: str
    is_directory: bool
    size_bytes: int


class _Index:
    """The erased paths, and which of them another path is or lies inside."""

    def __init__(self, records: Sequence[FileEraseRecord], *, windows: bool) -> None:
        self.windows = windows
        self.sep = "\\" if windows else "/"
        self.erased = [
            _Erased(
                path=record.path,
                key=self.key(record.path),
                is_directory=record.is_directory,
                size_bytes=record.inspection.size_bytes,
            )
            for record in records
            if record.ok and (record.dry_run or record.unlinked)
        ]
        self._exact = {item.key: item for item in self.erased}
        #: Longest first, so the innermost erased folder claims a path.
        self._folders = sorted(
            (item for item in self.erased if item.is_directory),
            key=lambda item: len(item.key),
            reverse=True,
        )
        #: Erased paths that are not inside another erased folder.
        self.roots = [
            item
            for item in self.erased
            if not any(
                item.key.startswith(folder.key + self.sep)
                for folder in self._folders
                if folder is not item
            )
        ]

    def key(self, path: str) -> str:
        if self.windows:
            return ntpath.normcase(ntpath.normpath(path))
        return posixpath.normpath(posixpath.abspath(path))

    def owner(self, path: str) -> _Erased | None:
        """The erased path ``path`` is, or the erased folder it lies inside."""
        key = self.key(path)
        exact = self._exact.get(key)
        if exact is not None:
            return exact
        for folder in self._folders:
            if key.startswith(folder.key + self.sep):
                return folder
        return None

    def inside(self, path: str) -> list[tuple[_Erased, list[str]]]:
        """Erased roots inside the folder ``path``, each with its relative parts."""
        key = self.key(path)
        found: list[tuple[_Erased, list[str]]] = []
        for item in self.roots:
            if item.key.startswith(key + self.sep):
                relative = item.key[len(key) + 1 :]
                found.append(
                    (item, [part for part in relative.split(self.sep) if part])
                )
        return found

    @property
    def names(self) -> dict[str, _Erased]:
        """Erased paths by final component, for a same-name comparison."""
        split = ntpath.basename if self.windows else posixpath.basename
        by_name: dict[str, _Erased] = {}
        for item in self.erased:
            by_name.setdefault(split(item.path.rstrip("/\\")), item)
        return by_name


# --------------------------------------------------------------------------
# Finding
# --------------------------------------------------------------------------


@dataclass
class _Found:
    """A trace, before anything is done to it."""

    kind: TraceKind
    target: str
    location: Path
    evidence: str
    content_copy: bool
    exact: bool = True
    #: How a real run removes it: "erase" puts the file through erase_one,
    #: "list" cuts ``entry`` out of the list at ``location``.
    how: str = "erase"
    entry: str = ""
    #: For a Trash or Recycle Bin record, the copy it describes. The record is
    #: kept when that copy could not be removed, so the copy stays visible.
    pair: Path | None = None


@dataclass
class _Plan:
    found: list[_Found] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def add(self, trace: _Found) -> None:
        key = (str(trace.location), trace.entry)
        if key not in self._seen:
            self._seen.add(key)
            self.found.append(trace)

    def place(self, label: str, where: Path) -> None:
        present = "" if os.path.lexists(where) else " (not present)"
        self.searched.append(f"{label}: {where}{present}")


def _size_words(copy: Path, erased: _Erased) -> str:
    """How a copy's size compares with the erased file's, for the evidence."""
    if erased.is_directory:
        return ""
    try:
        info = os.lstat(copy)
    except OSError:
        return ""
    if not stat_mod.S_ISREG(info.st_mode):
        return ""
    if info.st_size == erased.size_bytes:
        return f" It is the same size as the erased file ({info.st_size:,} bytes)."
    return (
        f" It is {info.st_size:,} bytes and the erased file was "
        f"{erased.size_bytes:,}: an earlier version."
    )


def _thumbnail_dirs(root: Path) -> list[tuple[Path, bool]]:
    """``(directory, holds failure markers)`` for each cache directory present."""
    found = [(root / size, False) for size in _THUMBNAIL_SIZES]
    failures = _listdir(root / "fail") or []
    found.extend((entry, True) for entry in failures)
    return [
        (directory, failed) for directory, failed in found if _is_real_dir(directory)
    ]


def _thumbnail_kind(failed: bool) -> TraceKind:
    return TraceKind.THUMBNAIL_FAILURE if failed else TraceKind.THUMBNAIL


def _find_thumbnails(root: Path, index: _Index, plan: _Plan) -> None:
    """Thumbnails of erased files, by name, then of files inside erased folders."""
    plan.place("thumbnail cache", root)
    directories = _thumbnail_dirs(root)
    if not directories:
        return
    for item in index.erased:
        if item.is_directory:
            continue
        for uri in file_uris(item.path):
            name = hashlib.md5(uri.encode("ascii"), usedforsecurity=False).hexdigest()
            for directory, failed in directories:
                candidate = directory / f"{name}.png"
                if not os.path.lexists(candidate):
                    continue
                recorded = png_text(_read(candidate, _PNG_HEAD, whole=False) or b"")
                named = recorded.get("Thumb::URI")
                named_path = path_from_uri(named) if named else None
                if named is None:
                    evidence = (
                        f"Named by the MD5 of {uri}. It carries no Thumb::URI "
                        "to confirm the match."
                    )
                    exact = True
                elif named_path is not None and index.key(named_path) == item.key:
                    evidence = (
                        f"Named by the MD5 of {uri}, and its Thumb::URI names "
                        "the same file."
                    )
                    exact = True
                else:
                    evidence = (
                        f"Named by the MD5 of {uri}, but its Thumb::URI names "
                        f"{named}. Left in place."
                    )
                    exact = False
                plan.add(
                    _Found(
                        kind=_thumbnail_kind(failed),
                        target=item.path,
                        location=candidate,
                        evidence=evidence,
                        content_copy=not failed,
                        exact=exact,
                    )
                )

    if not any(item.is_directory for item in index.erased):
        return
    read = 0
    for directory, failed in directories:
        for candidate in _listdir(directory) or []:
            if read >= SCAN_LIMIT:
                plan.notes.append(
                    f"The thumbnail cache at {root} holds more than "
                    f"{SCAN_LIMIT:,} files. Only the first {SCAN_LIMIT:,} were "
                    "read for thumbnails of files inside erased folders."
                )
                return
            if candidate.suffix != ".png":
                continue
            read += 1
            named = png_text(_read(candidate, _PNG_HEAD, whole=False) or b"").get(
                "Thumb::URI"
            )
            named_path = path_from_uri(named) if named else None
            owner = index.owner(named_path) if named_path else None
            if owner is None or not owner.is_directory:
                continue
            plan.add(
                _Found(
                    kind=_thumbnail_kind(failed),
                    target=owner.path,
                    location=candidate,
                    evidence=(
                        f"Its Thumb::URI names {named}, inside the erased "
                        f"folder {owner.path}."
                    ),
                    content_copy=not failed,
                )
            )


def _find_recent_list(path: Path, index: _Index, plan: _Plan) -> None:
    """Entries of a GTK recent-files list that name an erased path."""
    plan.place("recent-files list", path)
    if not os.path.lexists(path):
        return
    data = _read(path, _LIST_LIMIT, whole=True)
    if data is None:
        plan.notes.append(f"{path} could not be read, so it was not searched.")
        return
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        plan.notes.append(f"{path} declares a DTD or entities and was not parsed.")
        return
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        plan.notes.append(
            f"{path} is not well-formed XML ({exc}) and was not searched."
        )
        return
    for bookmark in root.iter("bookmark"):
        href = bookmark.get("href") or ""
        local = path_from_uri(href)
        owner = index.owner(local) if local else None
        if owner is None:
            continue
        plan.add(
            _Found(
                kind=TraceKind.RECENT_ENTRY,
                target=owner.path,
                location=path,
                evidence=f"The list has an entry for {href}.",
                content_copy=False,
                how="list",
                entry=href,
            )
        )


def _desktop_url(line: str, home: Path | None) -> str | None:
    """The local path in a ``URL=`` line of a KDE recent-document link."""
    key, sep, value = line.partition("=")
    if not sep or key.strip().split("[", 1)[0] != "URL":
        return None
    value = value.strip()
    if home is not None:
        value = value.replace("$HOME", str(home))
    if value.startswith("file://"):
        return path_from_uri(value)
    if value.startswith("file:"):
        return unquote(value[len("file:") :], errors="surrogateescape")
    return value if value.startswith("/") else None


def _find_recent_documents(
    directory: Path, index: _Index, plan: _Plan, home: Path | None
) -> None:
    """KDE ``RecentDocuments`` links whose URL names an erased path."""
    plan.place("recent documents", directory)
    for link in _listdir(directory) or []:
        if link.suffix != ".desktop":
            continue
        data = _read(link, _RECORD_LIMIT, whole=True)
        if data is None:
            continue
        for line in data.decode("utf-8", "surrogateescape").splitlines():
            local = _desktop_url(line, home)
            owner = index.owner(local) if local else None
            if owner is None:
                continue
            plan.add(
                _Found(
                    kind=TraceKind.RECENT_DOCUMENT,
                    target=owner.path,
                    location=link,
                    evidence=f"Its URL names {local}.",
                    content_copy=False,
                )
            )
            break


def _real_dirs_down(root: Path, parts: list[str]) -> bool:
    """``root`` and each folder below it along ``parts`` is a directory, not a link."""
    current = root
    if not _is_real_dir(current):
        return False
    for part in parts:
        current = current / part
        if not _is_real_dir(current):
            return False
    return True


def _find_bin_items(
    items: list[tuple[str, Path, Path]],
    index: _Index,
    plan: _Plan,
    *,
    copy_kind: TraceKind,
    record_kind: TraceKind,
) -> None:
    """Trash or Recycle Bin items whose recorded origin was erased, or held it.

    Each item is ``(original path, content copy, record)``. An item deleted
    from an erased path is a copy of it, and its record names it; both go. A
    folder deleted from above an erased path may hold a copy of it inside; only
    that inner copy goes, because the folder and its record also describe
    files nobody asked to erase.
    """
    for original, copy, record in items:
        owner = index.owner(original)
        if owner is not None:
            where = (
                f"{original}"
                if owner.key == index.key(original)
                else f"{original}, inside the erased folder {owner.path}"
            )
            present = os.path.lexists(copy)
            if present:
                plan.add(
                    _Found(
                        kind=copy_kind,
                        target=owner.path,
                        location=copy,
                        evidence=f"{record.name} records that this was deleted "
                        f"from {where}." + _size_words(copy, owner),
                        content_copy=True,
                    )
                )
            plan.add(
                _Found(
                    kind=record_kind,
                    target=owner.path,
                    location=record,
                    evidence=f"It records the deletion of {where}.",
                    content_copy=False,
                    pair=copy if present else None,
                )
            )
            continue
        for inner, parts in index.inside(original):
            inner_copy = copy.joinpath(*parts)
            # Every folder on the way down must be a real one. A link inside
            # a trashed folder would lead the erase out of the Trash, onto a
            # file nobody named.
            if not _real_dirs_down(copy, parts[:-1]) or not os.path.lexists(inner_copy):
                continue
            plan.add(
                _Found(
                    kind=copy_kind,
                    target=inner.path,
                    location=inner_copy,
                    evidence=(
                        f"{record.name} records a folder deleted from {original}; "
                        f"this is the copy of {inner.path} inside it."
                    )
                    + _size_words(inner_copy, inner),
                    content_copy=True,
                )
            )


def _trash_items(
    trash: Path, top: Path | None, plan: _Plan
) -> list[tuple[str, Path, Path]]:
    """Items of one freedesktop Trash directory, with their recorded origin.

    Its ``info`` and ``files`` folders must be real directories: a link there
    would point the erase of a "Trash copy" at some other folder entirely.
    """
    items: list[tuple[str, Path, Path]] = []
    if not os.path.lexists(trash / "info"):
        return items
    if not (_is_real_dir(trash / "info") and _is_real_dir(trash / "files")):
        plan.notes.append(
            f"{trash} has an info or files folder that is not a real directory, "
            "so it was not searched."
        )
        return items
    for info in _listdir(trash / "info") or []:
        if not info.name.endswith(".trashinfo"):
            continue
        data = _read(info, _RECORD_LIMIT, whole=True)
        original = _trashinfo_path(data) if data else None
        if not original:
            continue
        if not original.startswith("/"):
            # A per-volume Trash records paths relative to the volume's top.
            if top is None:
                continue
            original = str(top / original)
        name = info.name[: -len(".trashinfo")]
        items.append((original, trash / "files" / name, info))
    return items


def _volume_trashes(where: TraceLocations, index: _Index) -> list[tuple[Path, Path]]:
    """``(trash directory, volume top)`` for each erased path's volume."""
    if where.uid is None:
        return []
    home_trash = os.path.realpath(where.home_trash) if where.home_trash else None
    found: list[tuple[Path, Path]] = []
    tops: list[Path] = []
    for item in index.roots:
        top = where.volume_top(item.path)
        if top is not None and top not in tops:
            tops.append(top)
    for top in tops:
        candidates = [top / f".Trash-{where.uid}"]
        shared = top / ".Trash"
        try:
            info = os.lstat(shared)
            # The specification: use $topdir/.Trash only when it is a real
            # directory with the sticky bit set.
            if stat_mod.S_ISDIR(info.st_mode) and info.st_mode & stat_mod.S_ISVTX:
                candidates.append(shared / str(where.uid))
        except OSError:
            pass
        for candidate in candidates:
            if _is_real_dir(candidate) and os.path.realpath(candidate) != home_trash:
                found.append((candidate, top))
    return found


def _recycle_items(bin_root: Path) -> list[tuple[str, Path, Path]]:
    """Recycle Bin items this user can read, with their recorded origin.

    Another user's folder under ``$Recycle.Bin`` is closed by its ACL, and is
    skipped rather than reported: it is not this user's to erase.
    """
    items: list[tuple[str, Path, Path]] = []
    for sid in _listdir(bin_root) or []:
        if not _is_real_dir(sid):
            continue
        for record in _listdir(sid) or []:
            if not record.name.startswith("$I"):
                continue
            data = _read(record, _RECORD_LIMIT, whole=True)
            parsed = recycle_record(data) if data else None
            if parsed is None:
                continue
            original, _size = parsed
            items.append((original, record.with_name("$R" + record.name[2:]), record))
    return items


def _find_shortcuts(directory: Path, index: _Index, plan: _Plan) -> None:
    """Windows Recent shortcuts whose link target was erased."""
    plan.place("Recent shortcuts", directory)
    for link in _listdir(directory) or []:
        if link.suffix.lower() != ".lnk":
            continue
        data = _read(link, _RECORD_LIMIT, whole=True)
        target = shortcut_target(data) if data else None
        owner = index.owner(target) if target else None
        if owner is None:
            continue
        plan.add(
            _Found(
                kind=TraceKind.RECENT_SHORTCUT,
                target=owner.path,
                location=link,
                evidence=f"Its link target is {target}.",
                content_copy=False,
            )
        )


def _find_mac_trash(trash: Path, index: _Index, plan: _Plan) -> None:
    """Same-name items in the macOS Trash: possible copies, never removed."""
    plan.place("Trash", trash)
    if not os.path.lexists(trash):
        return
    items = _listdir(trash)
    if items is None:
        plan.notes.append(
            f"{trash} could not be listed. macOS lets an application read the "
            "Trash only with Full Disk Access."
        )
        return
    names = index.names
    for item in items:
        erased = names.get(item.name)
        if erased is None:
            continue
        plan.add(
            _Found(
                kind=TraceKind.POSSIBLE_COPY,
                target=erased.path,
                location=item,
                evidence=(
                    "An item of the same name is in the Trash. The Trash records "
                    "where an item came from only in its .DS_Store, which is not "
                    "read here, so this is a possible copy and was left in place."
                ),
                content_copy=True,
                exact=False,
            )
        )


def _find(records: Sequence[FileEraseRecord], where: TraceLocations) -> _Plan:
    index = _Index(records, windows=where.family == "windows")
    plan = _Plan()
    for root in where.thumbnail_roots:
        _find_thumbnails(root, index, plan)
    for recent in where.recent_lists:
        _find_recent_list(recent, index, plan)
    for directory in where.recent_document_dirs:
        _find_recent_documents(directory, index, plan, where.home)
    if where.home_trash is not None:
        plan.place("home Trash", where.home_trash)
        _find_bin_items(
            _trash_items(where.home_trash, None, plan),
            index,
            plan,
            copy_kind=TraceKind.TRASH_COPY,
            record_kind=TraceKind.TRASH_RECORD,
        )
    for trash, top in _volume_trashes(where, index):
        plan.place("volume Trash", trash)
        _find_bin_items(
            _trash_items(trash, top, plan),
            index,
            plan,
            copy_kind=TraceKind.TRASH_COPY,
            record_kind=TraceKind.TRASH_RECORD,
        )
    if where.recycle_bin:
        bins: list[Path] = []
        for item in index.roots:
            volume = where.volume_top(item.path)
            if volume is not None and volume / "$Recycle.Bin" not in bins:
                bins.append(volume / "$Recycle.Bin")
        for bin_root in bins:
            plan.place("Recycle Bin", bin_root)
            _find_bin_items(
                _recycle_items(bin_root),
                index,
                plan,
                copy_kind=TraceKind.RECYCLE_BIN_COPY,
                record_kind=TraceKind.RECYCLE_BIN_RECORD,
            )
    for directory in where.recent_shortcut_dirs:
        _find_shortcuts(directory, index, plan)
    if where.mac_trash is not None:
        _find_mac_trash(where.mac_trash, index, plan)
    return plan


def _as_record(found: _Found) -> TraceRecord:
    return TraceRecord(
        kind=found.kind.value,
        target=found.target,
        location=str(found.location),
        evidence=found.evidence,
        content_copy=found.content_copy,
        exact=found.exact,
    )


def find_traces(
    records: Sequence[FileEraseRecord], locations: TraceLocations | None = None
) -> TraceSweepResult:
    """Every trace of the erased records, found and left untouched.

    Only records that were erased - or, in a dry run, would be - are looked
    for: a path the erase refused keeps its traces, because it keeps its file.
    """
    where = locations if locations is not None else default_locations()
    plan = _find(records, where)
    return TraceSweepResult(
        searched=plan.searched,
        not_searched=list(NOT_SEARCHED.get(where.family, NOT_SEARCHED["linux"])),
        traces=[_as_record(found) for found in plan.found],
        notes=plan.notes,
    )


# --------------------------------------------------------------------------
# Removing
# --------------------------------------------------------------------------


def _erase_trace(location: Path, settings: FileEraseOptions) -> tuple[bool, int, str]:
    """Put one trace file or folder through the same steps as a target."""
    # Imported here because core.erase.files imports this module.
    from core.erase.files import erase_one, expand_targets

    options = FileEraseOptions(
        dry_run=False,
        confirm=True,
        cleanse_metadata=False,
        break_hardlinks=settings.break_hardlinks,
        rename_rounds=settings.rename_rounds,
        workers=1,
    )
    written = 0
    error = ""
    for path in expand_targets([location]):
        try:
            record = erase_one(path, options)
        except (ConfirmationMismatch, SystemDiskRefused) as exc:
            error = error or str(exc)
            continue
        written += record.bytes_overwritten
        if not record.ok and not error:
            error = ": ".join(
                part for part in (record.error_kind, record.error) if part
            )
    removed = not os.path.lexists(location)
    if not removed and not error:
        error = f"{location} was still present after the erase."
    return removed, written, error


def _overwrite_in_place(path: Path, content: bytes, *, expected: os.stat_result) -> str:
    """Replace a list's bytes where they lie, padded to its old length.

    Writing a new file and renaming it over the old one would free the old
    blocks with the removed entries still in them. Writing in place, and
    padding with whitespace rather than truncating, puts new bytes over every
    byte that held them. XML allows whitespace after the root element, so the
    list still parses; the desktop drops the padding the next time it saves.
    Returns an error, or an empty string.
    """
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return f"{path} could not be opened for writing: {exc.strerror}."
    try:
        info = os.fstat(fd)
        if (info.st_ino, info.st_size, info.st_mtime_ns) != (
            expected.st_ino,
            expected.st_size,
            expected.st_mtime_ns,
        ):
            return (
                f"{path} changed while the sweep was reading it, so it was left "
                "as it is. Close the applications that use it and erase again."
            )
        padding = info.st_size - len(content)
        body = content + (b" " * (padding - 1) + b"\n" if padding > 0 else b"")
        os.lseek(fd, 0, os.SEEK_SET)
        view = memoryview(body)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    except OSError as exc:
        return f"{path} could not be rewritten: {exc.strerror}."
    finally:
        os.close(fd)
    return ""


def _remove_entries(path: Path, hrefs: set[str]) -> str:
    """Cut the entries for ``hrefs`` out of a recent-files list. Returns an error."""
    try:
        before_stat = os.lstat(path)
    except OSError as exc:
        return f"{path} could not be read: {exc.strerror}."
    data = _read(path, _LIST_LIMIT, whole=True)
    if data is None:
        return f"{path} could not be read in full."
    removed = 0

    def cut(match: re.Match[bytes]) -> bytes:
        nonlocal removed
        href = html.unescape(match.group(2).decode("utf-8", "surrogateescape"))
        if href in hrefs:
            removed += 1
            return b""
        return match.group(0)

    rewritten = _XBEL_BOOKMARK.sub(cut, data)
    refused = (
        f"The entry could not be cut out of {path} without disturbing the "
        "rest of the list, so the list was left as it is."
    )
    try:
        before = [item.get("href") for item in ET.fromstring(data).iter("bookmark")]
        after = [item.get("href") for item in ET.fromstring(rewritten).iter("bookmark")]
    except ET.ParseError:
        return refused
    wanted = sum(1 for href in before if href in hrefs)
    if (
        not wanted
        or removed != wanted
        or len(after) != len(before) - wanted
        or any(href in hrefs for href in after)
    ):
        return refused
    return _overwrite_in_place(path, rewritten, expected=before_stat)


def _progress(job_id: str, message: str) -> Progress:
    return Progress(
        job_id=job_id,
        phase=PHASE,
        pct_bp=10_000,
        bytes_done=0,
        bytes_total=0,
        throughput_bytes_per_sec=0,
        eta_seconds=0,
        message=message,
    )


def sweep(
    records: Sequence[FileEraseRecord],
    settings: FileEraseOptions,
    *,
    job_id: str,
    ledger: LedgerSink,
    locations: TraceLocations | None = None,
) -> Generator[Progress, None, TraceSweepResult]:
    """Find the traces of the erased records and, on a real run, remove the exact ones.

    Every trace gets an ``erase.file.trace`` entry as it is dealt with, so a
    sweep closed part-way has left a record of each one it reached, and the
    sweep ends with one ``erase.file.traces`` entry naming every place it
    searched - so a sweep that found nothing is distinguishable from one that
    never ran.
    """
    where = locations if locations is not None else default_locations()
    plan = _find(records, where)
    yield _progress(
        job_id,
        f"{len(plan.found)} trace(s) found in {len(plan.searched)} place(s)",
    )

    lists: dict[Path, str] = {}
    kept: set[Path] = set()
    traces: list[TraceRecord] = []
    for found in plan.found:
        trace = _as_record(found)
        if not settings.dry_run and found.exact:
            if found.pair is not None and found.pair in kept:
                trace.error = (
                    "Kept, because the copy it describes could not be removed; "
                    "without it the copy would be hidden in the Trash."
                )
            elif found.how == "list":
                if found.location not in lists:
                    hrefs = {
                        other.entry
                        for other in plan.found
                        if other.exact
                        and other.how == "list"
                        and other.location == found.location
                    }
                    lists[found.location] = _remove_entries(found.location, hrefs)
                trace.error = lists[found.location]
                trace.removed = not trace.error
                trace.action = "entry removed" if trace.removed else ""
            else:
                removed, written, error = _erase_trace(found.location, settings)
                trace.removed = removed
                trace.bytes_overwritten = written
                trace.error = error
                trace.action = "erased" if removed else ""
                if not removed:
                    kept.add(found.location)
        ledger.record_file(
            "trace",
            {"job_id": job_id, "dry_run": settings.dry_run}
            | trace.model_dump(mode="json"),
        )
        traces.append(trace)
        yield _progress(job_id, f"{trace.kind} {trace.location}")

    result = TraceSweepResult(
        searched=plan.searched,
        not_searched=list(NOT_SEARCHED.get(where.family, NOT_SEARCHED["linux"])),
        traces=traces,
        notes=plan.notes,
    )
    ledger.record_file(
        "traces",
        {
            "job_id": job_id,
            "dry_run": settings.dry_run,
            "searched": result.searched,
            "found": len(traces),
            "exact": sum(1 for trace in traces if trace.exact),
            "removed": sum(1 for trace in traces if trace.removed),
            "notes": result.notes,
        },
    )
    logger.info(
        "trace_sweep_complete",
        job_id=job_id,
        dry_run=settings.dry_run,
        found=len(traces),
        removed=sum(1 for trace in traces if trace.removed),
    )
    return result
