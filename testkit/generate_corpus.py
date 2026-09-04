"""Build a synthetic disk-image corpus for evaluating the carving pipeline.

Every object planted here is produced by a real encoder and can be opened by
the format's real reader, because a corpus of synthetic byte patterns measures
nothing: the difficulty in carving is that real containers carry padding,
embedded thumbnails and nested copies of their own magic.

The manifest records four kinds of planted object, and the difference between
them is what makes precision measurable at all:

``intact``
    A whole file. The carver is expected to recover exactly these bytes, so
    the SHA-256 here is what a true positive matches against.
``duplicate``
    Byte-identical content planted at another offset. Recovering it is not a
    second find; the dedupe pass folds it into the first.
``truncated``
    A real file with its tail removed. Recoverable as an object, but never
    byte-identical to anything - a candidate matching one of these is a
    genuine finding that must not be scored HIGH.
``decoy``
    A valid header attached to bytes that are not that format at all: the
    ``FFD8FF`` inside a text file that every naive signature carver reports.
    Every candidate covering a decoy is a false positive.

Never touches a real device. The images are ordinary files in ``out_dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import sqlite3
import struct
import tempfile
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

__all__ = [
    "PlantedObject",
    "CorpusManifest",
    "MANIFEST_NAME",
    "generate_corpus",
    "load_manifest",
    "main",
]

KIB = 1024
MIB = 1024 * KIB

MANIFEST_NAME = "ground_truth.json"

#: Distance between planted objects. Wide enough that the signature scanner's
#: "bounded by the next header" rule never truncates an intact object, small
#: enough that the whole corpus fits in a few megabytes.
STRIDE_BYTES = 512 * KIB

#: Offset of the first object. Deliberately not a power of two, so an off-by-one
#: in chunk arithmetic cannot pass by luck.
FIRST_OFFSET = 1337

PlantKind = Literal["intact", "duplicate", "truncated", "decoy"]


@dataclass(frozen=True)
class PlantedObject:
    """One object at one offset in one image, with its ground truth."""

    offset: int
    length: int
    sha256: str
    ext: str
    name: str
    kind: PlantKind
    #: True when a correct carve reproduces these bytes exactly. Only ``intact``
    #: and ``duplicate`` objects are recoverable; the rest exist to be scored
    #: against, not to be found.
    recoverable: bool


@dataclass(frozen=True)
class CorpusManifest:
    """Everything planted in one image."""

    image: str
    size_bytes: int
    seed: int
    objects: list[PlantedObject]

    @property
    def recoverable_digests(self) -> set[str]:
        """SHA-256 of every object a correct carve reproduces."""
        return {item.sha256 for item in self.objects if item.recoverable}


# --------------------------------------------------------------------------
# Content generators - real encoders only
# --------------------------------------------------------------------------


def _noisy_image(size: int, rng: random.Random) -> Image.Image:
    """An image the entropy coder cannot flatten, so the file is worth carving."""
    image = Image.new("RGB", (size, size))
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(size * size)
        ]
    )
    return image


def make_jpeg(rng: random.Random, size: int = 128, *, gps: bool = False) -> bytes:
    buffer = io.BytesIO()
    image = _noisy_image(size, rng)
    if gps:
        exif = Image.Exif()
        # 28.6139 N, 77.2090 E - degrees/minutes/seconds as EXIF rationals.
        exif[0x8825] = {
            1: "N",
            2: (IFDRational(28), IFDRational(36), IFDRational(50)),
            3: "E",
            4: (IFDRational(77), IFDRational(12), IFDRational(32)),
        }
        image.save(buffer, "JPEG", quality=95, exif=exif)
    else:
        image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def make_png(rng: random.Random, size: int = 96) -> bytes:
    buffer = io.BytesIO()
    _noisy_image(size, rng).save(buffer, "PNG")
    return buffer.getvalue()


def make_gif(size: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("P", (size, size)).save(buffer, "GIF")
    return buffer.getvalue()


def make_zip(rng: random.Random, entries: int = 4) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(entries):
            archive.writestr(
                f"file{index}.txt", f"payload {index} {rng.random()}\n" * 60
            )
    return buffer.getvalue()


def make_docx(*, macros: bool = False) -> bytes:
    """A structurally real OOXML package, optionally carrying a VBA project."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
            '/package/2006/content-types"><Default Extension="xml" '
            'ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r>'
            "<w:t>sanctum</w:t></w:r></w:p></w:body></w:document>",
        )
        archive.writestr("docProps/core.xml", "<coreProperties/>")
        if macros:
            # Bytes, not a real VBA project: the flag is set from the part's
            # presence, and nothing in this codebase ever executes it.
            archive.writestr("word/vbaProject.bin", b"\xd0\xcf\x11\xe0" + b"\x00" * 512)
    return buffer.getvalue()


def make_xlsx() -> bytes:
    """A SpreadsheetML package: the same OPC rules, a different required part."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
            '/package/2006/content-types"><Default Extension="xml" '
            'ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.'
            'openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet '
            'name="Sheet1" sheetId="1"/></sheets></workbook>',
        )
        archive.writestr("docProps/core.xml", "<coreProperties/>")
    return buffer.getvalue()


def make_pdf() -> bytes:
    """A hand-built PDF with a correct xref table and startxref offset."""
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 12 Tf 20 100 Td "
        b"(sanctum) Tj ET\nendstream\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n" % xref_at
    out += b"%%EOF\n"
    return bytes(out)


def make_sqlite(rows: int = 200) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "corpus.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany(
            "INSERT INTO contacts (body) VALUES (?)",
            [(f"row {index} " * 8,) for index in range(rows)],
        )
        connection.commit()
        connection.close()
        return path.read_bytes()


def make_mp4() -> bytes:
    """Top-level boxes only: ftyp then a sized mdat. Enough to walk."""

    def box(kind: bytes, payload: bytes) -> bytes:
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload

    ftyp = box(b"ftyp", b"isom" + (512).to_bytes(4, "big") + b"isomiso2mp41")
    mdat = box(b"mdat", bytes(range(256)) * 16)
    return ftyp + mdat


_DECOY_PROSE = (
    "The examiner opened the drive image and began the recovery run. "
    "Every candidate was recorded with the offset it was found at. "
)


def make_jpeg_decoy(length: int = 100 * KIB) -> bytes:
    """A JPEG header glued to English text: the classic signature false positive."""
    body = (_DECOY_PROSE * (length // len(_DECOY_PROSE) + 1))[:length]
    return b"\xff\xd8\xff\xe0" + body.encode("ascii")


def make_png_bomb() -> bytes:
    """A PNG header declaring 40000x40000 pixels over a few hundred bytes of data.

    Decoding it honestly would ask for 4.8 GB. It is planted so a corpus run
    exercises the decompression-bomb guard on the real pipeline rather than
    only in a unit test.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 40000, 40000, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 256))
        + chunk(b"IEND", b"")
    )


def make_encrypted_zip(rng: random.Random) -> bytes:
    """An archive whose members are marked encrypted in their headers.

    Built by setting the encryption bit on a plain archive rather than by
    actually encrypting it: what has to be exercised is that the validator
    *declines* to decrypt and moves on, and that path is reached from the flag
    bits alone. No decryption is ever attempted, so the payload behind the flag
    never matters.
    """
    plain = make_zip(rng, entries=2)
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        local_offsets = [item.header_offset for item in archive.infolist()]
    out = bytearray(plain)
    for offset in local_offsets:
        out[offset + 6] |= 0x01
    cursor = out.find(b"PK\x01\x02")
    while cursor != -1:
        out[cursor + 8] |= 0x01
        cursor = out.find(b"PK\x01\x02", cursor + 4)
    return bytes(out)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Plant:
    ext: str
    name: str
    data: bytes
    kind: PlantKind


def _plants(rng: random.Random) -> list[_Plant]:
    """The corpus contents, in the order they are planted."""
    photo = make_jpeg(rng, 128, gps=True)
    duplicated = make_png(rng, 96)
    intact: list[_Plant] = [
        _Plant("jpg", "photo-with-gps.jpg", photo, "intact"),
        _Plant("png", "screenshot.png", duplicated, "intact"),
        _Plant("pdf", "statement.pdf", make_pdf(), "intact"),
        _Plant("zip", "archive.zip", make_zip(rng), "intact"),
        _Plant("zip", "letter.docx", make_docx(), "intact"),
        _Plant("zip", "budget-macros.docm", make_docx(macros=True), "intact"),
        _Plant("sqlite", "contacts.sqlite", make_sqlite(), "intact"),
        _Plant("gif", "sticker.gif", make_gif(), "intact"),
        _Plant("mp4", "clip.mp4", make_mp4(), "intact"),
        _Plant("jpg", "second-photo.jpg", make_jpeg(rng, 160), "intact"),
        _Plant("jpg", "third-photo.jpg", make_jpeg(rng, 96, gps=True), "intact"),
        _Plant("png", "chart.png", make_png(rng, 128), "intact"),
        _Plant("pdf", "invoice.pdf", make_pdf(), "intact"),
        _Plant("zip", "sheet.xlsx", make_xlsx(), "intact"),
        _Plant("sqlite", "messages.sqlite", make_sqlite(rows=400), "intact"),
    ]
    truncated_jpeg = make_jpeg(rng, 128)[: 3 * KIB]
    extras: list[_Plant] = [
        # The same PNG at two more offsets: dedupe has to fold three into one.
        _Plant("png", "screenshot.png", duplicated, "duplicate"),
        _Plant("png", "screenshot.png", duplicated, "duplicate"),
        _Plant("jpg", "half-written.jpg", truncated_jpeg, "truncated"),
        _Plant("pdf", "half-written.pdf", make_pdf()[:180], "truncated"),
        _Plant("png", "half-written.png", make_png(rng, 96)[: 2 * KIB], "truncated"),
        _Plant("jpg", "prose-with-jpeg-header.txt", make_jpeg_decoy(), "decoy"),
        _Plant(
            "jpg",
            "source-code-with-jpeg-header.txt",
            make_jpeg_decoy(40 * KIB),
            "decoy",
        ),
        _Plant("png", "declared-40000x40000.png", make_png_bomb(), "decoy"),
        _Plant("zip", "locked.zip", make_encrypted_zip(rng), "intact"),
    ]
    return intact + extras


def _filler(size: int, rng: random.Random) -> bytearray:
    """Pseudo-random filler. Not zeros: zeros make every carve look easy."""
    return bytearray(rng.randbytes(size))


def generate_corpus(out_dir: Path, *, seed: int = 0) -> CorpusManifest:
    """Write a synthetic image plus ``ground_truth.json`` into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    plants = _plants(rng)
    size = FIRST_OFFSET + STRIDE_BYTES * (len(plants) + 1)
    canvas = _filler(size, rng)

    objects: list[PlantedObject] = []
    for index, plant in enumerate(plants):
        offset = FIRST_OFFSET + index * STRIDE_BYTES
        canvas[offset : offset + len(plant.data)] = plant.data
        objects.append(
            PlantedObject(
                offset=offset,
                length=len(plant.data),
                sha256=hashlib.sha256(plant.data).hexdigest(),
                ext=plant.ext,
                name=plant.name,
                kind=plant.kind,
                recoverable=plant.kind in {"intact", "duplicate"},
            )
        )

    image_path = out_dir / "corpus.dd"
    image_path.write_bytes(bytes(canvas))
    manifest = CorpusManifest(
        image=image_path.name, size_bytes=size, seed=seed, objects=objects
    )
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "image": manifest.image,
                "size_bytes": manifest.size_bytes,
                "seed": manifest.seed,
                "objects": [asdict(item) for item in manifest.objects],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def load_manifest(corpus_dir: Path) -> CorpusManifest:
    """Read a manifest written by :func:`generate_corpus`."""
    raw = json.loads((Path(corpus_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    return CorpusManifest(
        image=raw["image"],
        size_bytes=raw["size_bytes"],
        seed=raw["seed"],
        objects=[PlantedObject(**item) for item in raw["objects"]],
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate a synthetic carving corpus.")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    manifest = generate_corpus(args.out_dir, seed=args.seed)
    print(  # noqa: T201 - this is a CLI, not a core layer
        f"wrote {manifest.image} ({manifest.size_bytes} bytes) with "
        f"{len(manifest.objects)} planted objects"
    )


if __name__ == "__main__":
    main()
