# Recovery benchmark: Sanctum, PhotoRec and Foremost

**Run date:** 2026-09-15 · **Sanctum measured at:** `3c6b303` (`pii-triage-1-3-g3c6b303`, clean tree; `core/` and `api/` are identical to `b5a439a`, the PT4 fix)
· **Harness:** `testkit/benchmark.py`, `testkit/damage.py` · **Table:** [`benchmark.csv`](benchmark.csv)
· **Host:** one Fedora 44 laptop, Linux 6.19.10, 16 logical CPUs, 14 GiB RAM

This is the performance evaluation of the recovery module against the two
open-source carvers a forensic examiner is most likely to reach for. It exists to
answer one question with numbers: **given the same image and the same ground truth,
which tool returns the planted files byte for byte, what else does it return, and how
long does it take.**

Every figure here is from synthetic images built on this host. No real storage medium
was measured. Read [Limits](#limits) before quoting any number.

## Tools, versions and settings

| Row | Tool and version | How it was run | Settings |
|---|---|---|---|
| Sanctum, carve only | Sanctum at `3c6b303` | `api.carve_job.carve_generator(image, undelete=False, out_dir=…)` in a fresh process | Defaults: structure carving, bifragment reassembly, validation, scoring, PII triage on |
| PhotoRec | PhotoRec 7.2 (February 2024), Fedora package `testdisk-7.2-6.fc44.x86_64` | `photorec /log /d <out>/recup /cmd <image> partition_none,wholespace,search` | Default file-type selection. The only option given is scope: the whole image, as one unpartitioned space |
| Foremost | Foremost 1.5.7, Fedora package `foremost-1.5.7-37.fc44`, **not installed on the host**: the RPM was downloaded with `dnf download` and unpacked into a scratch directory, because installing needs root | `foremost -i <image> -o <out>` | No configuration file, which selects Foremost's built-in type set |
| Sanctum, undelete + carve | Sanctum at `3c6b303` | `carve_generator(image, undelete=True, out_dir=…)` | Defaults. **Not a peer of the carvers**; see fairness rule 1 |

Scalpel was not measured.

Time is wall-clock seconds for the whole process, measured from outside with
`time.perf_counter`, one run each, tools run one at a time. Sanctum's figure includes
starting Python and importing its modules (about a second); the carvers are native
binaries.

## Fairness rules

1. **Like with like.** PhotoRec and Foremost carve. They never read filesystem
   metadata. Sanctum's comparable row is **carve only**. Sanctum's full pipeline runs
   undelete first, which reads the surviving filesystem records; it is reported in its
   own row, below a separator, and no sentence in this document compares it with a
   carver as if it were one.
2. **Default settings for all three.** Nobody was tuned. PhotoRec's one non-default
   choice is scope, `partition_none,wholespace`: without it PhotoRec's `/cmd` mode scans
   only the first recognised partition (measured: on the two-partition image it analysed
   the NTFS partition and never reached the FAT32 one), while Sanctum and Foremost scan
   the whole image. Its file-type selection is left at its default.
3. **One scorer.** Each tool writes files into a directory. The same function
   (`score_run`) hashes and attributes every file for every tool. No tool's log, report,
   file name or claimed offset is read; renaming every output does not change a score
   (`tests/testkit/test_benchmark_scoring.py`).
4. **Report the losses.** The planted set deliberately includes seven formats whose
   header is not in Sanctum's signature table and which PhotoRec knows.

## Corpora

| Corpus | Images | What it is |
|---|---:|---|
| Flat corpus as shipped | 1 | `generate_corpus` seed 0: 24 objects in 12.5 MiB of pseudo-random filler, the first at byte 1337 and every 512 KiB after. Includes 3 truncated plants, 3 decoys and a 40000×40000 PNG bomb header. |
| Flat corpus, sector-aligned | 1 | The same objects with the first at byte 4096. Added for this benchmark: no filesystem places a file off a sector boundary, and a carver that looks for headers only at block starts (PhotoRec) finds nothing in the shipped layout for that reason alone. |
| Filesystem corpus | 13 | `generate_filesystem_corpus` seed 0, unchanged: NTFS, NTFS with a reused MFT record, FAT32 fragmented with and without live neighbours, exFAT, ext2, ext3, ext4, plain FAT32, two partitions, boot sector zeroed, partition table garbage, quick format. |
| Benchmark volumes | 5 | New. FAT32 255 MiB (512-byte clusters), FAT32 511 MiB (4096-byte clusters), exFAT 255 MiB (4096-byte clusters) — the geometries earlier batches proved on real sticks — plus NTFS 64 MiB (4096) and ext4 64 MiB (1024-byte blocks). Each built by the real `mkfs` and populated through `mtools`, `ntfscp`, `debugfs` or the exFAT builder. |
| Damage models over the benchmark volumes | 20 | Four models × five volumes. See below. |

Each benchmark volume holds 21 real files, 17 of them deleted:

| Format | Header in Sanctum's signature table | Files | Encoder |
|---|---|---:|---|
| JPEG | yes | 3 (+1 on FAT32) | Pillow, noise, q95; 128, 384 and 768 px |
| PNG | yes | 2 | Pillow, noise |
| GIF | yes | 1 | Pillow |
| PDF | yes | 2 | hand-built, correct xref |
| ZIP | yes | 1 | `zipfile`, deflate |
| DOCX, XLSX | yes (as ZIP) | 1 each | `zipfile`, OPC parts |
| SQLite | yes | 1 | `sqlite3`, 2000 rows |
| MP4 | yes | 1 | `ftyp` + `mdat` boxes |
| TIFF | yes, no structure parser | 1 | Pillow |
| BMP | **no** | 1 | Pillow |
| WebP | **no** | 1 | Pillow, lossy |
| WAV | **no** | 1 | `wave`, 1 s PCM |
| GZIP | **no** | 1 | `gzip` |
| TAR | **no** | 1 | `tarfile`, ustar |
| HTML | **no** | 1 | text |
| RTF | **no** | 1 | text |

On both FAT32 volumes a fifth JPEG, `frag.jpg`, is written **in exactly two runs** the
way a real volume splits a file: two 64 KiB pads are written, the first is deleted, and
the JPEG is written into the hole with the free-cluster hint cleared, so its head lands
in the hole and its tail after the live second pad. The manifest verifies it is two
runs. On exFAT, `shot.png` is stored with a FAT chain and a one-cluster gap between
each run (28 runs).

## Damage models

`testkit/damage.py`. Each model rewrites a copy of a benchmark volume. **Every manifest
records, for every planted object, whether it is FULL, PARTIAL or GONE, and the status is
computed, not declared**: the planted bytes are searched for on the medium, their runs
are recorded as extents, and the status is the overlap between those extents and the
byte ranges the model destroyed.

* **FULL** — every stored byte is where it was written. Only these can come back
  byte-identical, and only these are in a recall denominator.
* **PARTIAL** — some bytes survive. A tool can return something, never the original.
* **GONE** — nothing survives. Anything returned for it did not come from this medium.

| Model | What is done | Parameters recorded |
|---|---|---|
| Truncation | The image ends halfway through the median file in medium order: files before it survive, it is cut, files after it are gone. An acquisition that died. | kept bytes, percentage lost |
| Zeroed regions | Eight zero-filled bands: one covering the median-sized contiguous file whole, one over the middle third of the largest file, six of 256 KiB placed by seed. How unreadable sectors look after imaging with a zero-filling tool. | every band, seed |
| Metadata destroyed | Every structure the volume needs to find its files is zeroed, located from the volume's own boot record and descriptors: FAT32 reserved region, both FAT copies and the root directory; exFAT main and backup boot regions, FAT, allocation bitmap, up-case table and root directory; NTFS boot sector, backup boot sector, `$MFT` and `$MFTMirr`; ext4 every superblock and descriptor copy, block and inode bitmaps and inode tables. File data is not touched. | every range and its label |
| Interleaved overwrite | Two later files written on the cluster grid: a 160 px JPEG over the head of `contacts.sqlite` (as first-fit reuse of freed clusters does), and a 128 px PNG starting halfway through `photo-large.jpg`. No filesystem record is updated. | victim, later file, offset, length |

The metadata model is tested against each filesystem's own reader: The Sleuth Kit opens
every volume before damage and none after, the planted bytes are unchanged, and the
ranges cover structures found independently — both FAT media-byte signatures, every
sector-aligned `FILE` record, and every inode table and superblock `dumpe2fs` lists
(`tests/testkit/test_damage_models.py`).

Status of the formatted planted files in every image, as the manifests record it:

| Image | Model | Filesystem, cluster | FULL | PARTIAL | GONE | Fragmented | Unformatted plants |
|---|---|---|---:|---:|---:|---:|---:|
| `flat-aligned.img` | none | none, 512 | 18 | 3 | 0 | 0 | 0 |
| `flat-offset1337.img` | none | none, 512 | 18 | 3 | 0 | 0 | 0 |
| `fs-exfat.img` | delete | exfat, 4096 | 3 | 0 | 0 | 1 | 0 |
| `fs-ext2.img` | delete | ext2, 1024 | 3 | 0 | 0 | 1 | 0 |
| `fs-ext3.img` | delete | ext3, 1024 | 3 | 0 | 0 | 1 | 0 |
| `fs-ext4.img` | delete | ext4, 1024 | 3 | 0 | 0 | 0 | 0 |
| `fs-fat32-neighbours.img` | delete | fat32, 512 | 3 | 0 | 0 | 0 | 158 |
| `fs-fat32-plain.img` | delete | fat32, 512 | 3 | 0 | 0 | 0 | 0 |
| `fs-fat32.img` | delete | fat32, 512 | 3 | 0 | 0 | 0 | 158 |
| `fs-ntfs-reused.img` | delete | ntfs, 4096 | 17 | 3 | 0 | 0 | 0 |
| `fs-ntfs.img` | delete | ntfs, 4096 | 17 | 3 | 0 | 0 | 0 |
| `fs-two-partitions.img` | delete | fat32+ntfs, 512 | 20 | 3 | 0 | 0 | 0 |
| `fs-damaged-boot.img` | boot_sector_zeroed | ntfs, 512 | 17 | 3 | 0 | 0 | 0 |
| `fs-damaged-parttable.img` | partition_table_garbage | fat32+ntfs, 512 | 20 | 3 | 0 | 0 | 0 |
| `fs-quick-formatted.img` | quick_format | fat32, 512 | 3 | 0 | 0 | 0 | 0 |
| `media-exfat-255m.img` | delete | exfat, 4096 | 21 | 0 | 0 | 1 | 0 |
| `media-ext4-64m.img` | delete | ext4, 1024 | 21 | 0 | 0 | 0 | 0 |
| `media-fat32-255m.img` | delete | fat32, 512 | 22 | 0 | 0 | 1 | 2 |
| `media-fat32-511m.img` | delete | fat32, 4096 | 22 | 0 | 0 | 1 | 2 |
| `media-ntfs-64m.img` | delete | ntfs, 4096 | 17 | 4 | 0 | 0 | 0 |
| `dmg-interleave-exfat-255m.img` | interleaved_overwrite | exfat, 4096 | 21 | 2 | 0 | 1 | 0 |
| `dmg-interleave-ext4-64m.img` | interleaved_overwrite | ext4, 1024 | 21 | 2 | 0 | 0 | 0 |
| `dmg-interleave-fat32-255m.img` | interleaved_overwrite | fat32, 512 | 22 | 2 | 0 | 1 | 2 |
| `dmg-interleave-fat32-511m.img` | interleaved_overwrite | fat32, 4096 | 22 | 2 | 0 | 1 | 2 |
| `dmg-interleave-ntfs-64m.img` | interleaved_overwrite | ntfs, 4096 | 17 | 6 | 0 | 0 | 0 |
| `dmg-metadata-exfat-255m.img` | metadata_destroyed | exfat, 4096 | 21 | 0 | 0 | 1 | 0 |
| `dmg-metadata-ext4-64m.img` | metadata_destroyed | ext4, 1024 | 21 | 0 | 0 | 0 | 0 |
| `dmg-metadata-fat32-255m.img` | metadata_destroyed | fat32, 512 | 22 | 0 | 0 | 1 | 2 |
| `dmg-metadata-fat32-511m.img` | metadata_destroyed | fat32, 4096 | 22 | 0 | 0 | 1 | 2 |
| `dmg-metadata-ntfs-64m.img` | metadata_destroyed | ntfs, 4096 | 16 | 0 | 5 | 0 | 0 |
| `dmg-truncation-exfat-255m.img` | truncation | exfat, 4096 | 9 | 2 | 10 | 1 | 0 |
| `dmg-truncation-ext4-64m.img` | truncation | ext4, 1024 | 10 | 0 | 11 | 0 | 0 |
| `dmg-truncation-fat32-255m.img` | truncation | fat32, 512 | 11 | 0 | 11 | 1 | 2 |
| `dmg-truncation-fat32-511m.img` | truncation | fat32, 4096 | 11 | 0 | 11 | 1 | 2 |
| `dmg-truncation-ntfs-64m.img` | truncation | ntfs, 4096 | 8 | 5 | 8 | 0 | 0 |
| `dmg-zeroed-exfat-255m.img` | zeroed_regions | exfat, 4096 | 19 | 1 | 1 | 1 | 0 |
| `dmg-zeroed-ext4-64m.img` | zeroed_regions | ext4, 1024 | 19 | 1 | 1 | 0 | 0 |
| `dmg-zeroed-fat32-255m.img` | zeroed_regions | fat32, 512 | 20 | 1 | 1 | 1 | 2 |
| `dmg-zeroed-fat32-511m.img` | zeroed_regions | fat32, 4096 | 20 | 1 | 1 | 1 | 2 |
| `dmg-zeroed-ntfs-64m.img` | zeroed_regions | ntfs, 4096 | 11 | 6 | 4 | 0 | 0 |

Three things in those manifests that a reader should know:

* **NTFS resident files are PARTIAL on an undamaged volume.** A file of a few hundred
  bytes lives inside its MFT record, and NTFS's update-sequence fixups replace the last
  two bytes of each sector of that record on disk. The raw bytes on the medium are
  therefore not the file; only a reader that applies the fixups (undelete) can return
  it exactly. Four benchmark files and three filesystem-corpus ZIPs are in this state.
* **ext4 files with zero blocks are sparse.** `debugfs write` stores an all-zero block as
  a hole. `contacts.sqlite` (4096 bytes of holes) and `backup.tar` (3072) are recorded as
  FULL with `hole_bytes`: every stored byte is present, and the holes were never on the
  medium to damage.
* **The ext4 metadata model leaves the journal blocks** (the inode that locates the
  journal is in a zeroed inode table).

## Scoring

`testkit/benchmark.py:score_run`, the same for every row:

* **Byte-identical** — an output file's SHA-256 equals a FULL planted object's.
  Objects with identical content (the flat corpus's three copies of one PNG) count once.
* **Corrupt** — no identical output, but an output whose leading bytes agree with this
  object for at least 64 bytes and for longer than with any other planted object.
* **Missed** — neither.
* **PARTIAL returned** — any output identical to, or attributed to, a PARTIAL object.
  *Identical* in brackets means byte-identical, which on the raw medium is only possible
  through filesystem metadata.
* **GONE returned** — must be zero; it would mean the ground truth is wrong.
* **False positives** — outputs attributed to no planted object, split into *fragment*
  (the output's first 256 bytes lie inside a planted file: a ZIP member, an embedded
  stream), *decoy* (a planted fake header), *ambiguous* (agrees equally with two
  different planted objects) and *unrelated*.
* Unformatted planted content (FAT32 fillers, random pads, `split.bin`) is kept out of
  every recall denominator, because no signature carver can find it.

## Results

#### Flat corpus as shipped (objects at byte 1337 + k x 512 KiB)

Images: `flat-offset1337.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 15 | **15** | 0 | 0 | 100.0% | 2/3 | 0/0 | 15 (12/0/2/1) | 33 | 4.9 |
| PhotoRec | 15 | **0** | 0 | 15 | 0.0% | 0/3 | 0/0 | 0 (0/0/0/0) | 0 | 0.0 |
| Foremost | 15 | **7** | 6 | 2 | 46.7% | 1/3 | 0/0 | 0 (0/0/0/0) | 17 | 0.2 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 15 | **15** | 0 | 0 | 100.0% | 2/3 | 0/0 | 15 (12/0/2/1) | 33 | 5.0 |

#### Flat corpus, sector-aligned (objects at byte 4096 + k x 512 KiB)

Images: `flat-aligned.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 15 | **15** | 0 | 0 | 100.0% | 2/3 | 0/0 | 15 (12/0/2/1) | 33 | 5.0 |
| PhotoRec | 15 | **15** | 0 | 0 | 100.0% | 0/3 | 0/0 | 0 (0/0/0/0) | 19 | 0.0 |
| Foremost | 15 | **7** | 6 | 2 | 46.7% | 1/3 | 0/0 | 0 (0/0/0/0) | 17 | 0.2 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 15 | **15** | 0 | 0 | 100.0% | 2/3 | 0/0 | 15 (12/0/2/1) | 33 | 5.0 |

#### Filesystem corpus (13 images, generate_filesystem_corpus)

Images: `fs-damaged-boot.img`, `fs-damaged-parttable.img`, `fs-exfat.img`, `fs-ext2.img`, `fs-ext3.img`, `fs-ext4.img`, `fs-fat32-neighbours.img`, `fs-fat32-plain.img`, `fs-fat32.img`, `fs-ntfs-reused.img`, `fs-ntfs.img`, `fs-quick-formatted.img`, `fs-two-partitions.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 115 | **112** | 3 | 0 | 97.4% | 15/15 | 0/0 | 73 (58/0/0/15) | 203 | 63.0 |
| PhotoRec | 115 | **108** | 0 | 7 | 93.9% | 0/15 | 0/0 | 0 (0/0/0/0) | 108 | 0.9 |
| Foremost | 115 | **99** | 5 | 11 | 86.1% | 15/15 | 0/0 | 0 (0/0/0/0) | 119 | 5.1 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 115 | **114** | 1 | 0 | 99.1% | 15/15 (3 identical) | 0/0 | 84 (58/0/0/26) | 445 | 70.6 |

#### Benchmark volumes, files written then some deleted (5 volumes)

Images: `media-exfat-255m.img`, `media-ext4-64m.img`, `media-fat32-255m.img`, `media-fat32-511m.img`, `media-ntfs-64m.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 103 | **56** | 12 | 35 | 54.4% | 0/4 | 0/0 | 54 (50/0/4/0) | 122 | 497.7 |
| PhotoRec | 103 | **84** | 15 | 4 | 81.6% | 0/4 | 0/0 | 0 (0/0/0/0) | 99 | 0.6 |
| Foremost | 103 | **47** | 20 | 36 | 45.6% | 0/4 | 0/0 | 9 (5/0/4/0) | 76 | 14.1 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 103 | **92** | 4 | 7 | 89.3% | 2/4 (2 identical) | 0/0 | 55 (50/0/4/1) | 162 | 495.6 |

#### Damage model: truncation (5 volumes)

Images: `dmg-truncation-exfat-255m.img`, `dmg-truncation-ext4-64m.img`, `dmg-truncation-fat32-255m.img`, `dmg-truncation-fat32-511m.img`, `dmg-truncation-ntfs-64m.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 49 | **49** | 0 | 0 | 100.0% | 1/7 | 0/51 | 49 (44/0/4/1) | 99 | 12.8 |
| PhotoRec | 49 | **46** | 0 | 3 | 93.9% | 1/7 | 0/51 | 0 (0/0/0/0) | 47 | 0.2 |
| Foremost | 49 | **36** | 12 | 1 | 73.5% | 0/7 | 0/51 | 4 (0/0/4/0) | 52 | 0.7 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 49 | **49** | 0 | 0 | 100.0% | 5/7 (2 identical) | 0/51 | 53 (44/0/4/5) | 107 | 13.8 |

#### Damage model: zeroed regions (5 volumes)

Images: `dmg-zeroed-exfat-255m.img`, `dmg-zeroed-ext4-64m.img`, `dmg-zeroed-fat32-255m.img`, `dmg-zeroed-fat32-511m.img`, `dmg-zeroed-ntfs-64m.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 89 | **46** | 12 | 31 | 51.7% | 5/10 | 0/8 | 48 (44/0/4/0) | 111 | 539.4 |
| PhotoRec | 89 | **73** | 12 | 4 | 82.0% | 0/10 | 0/8 | 0 (0/0/0/0) | 85 | 0.8 |
| Foremost | 89 | **39** | 19 | 31 | 43.8% | 5/10 | 0/8 | 9 (5/0/4/0) | 72 | 14.2 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 89 | **79** | 4 | 6 | 88.8% | 7/10 (2 identical) | 0/8 | 56 (51/0/4/1) | 155 | 544.2 |

#### Damage model: filesystem metadata destroyed (5 volumes)

Images: `dmg-metadata-exfat-255m.img`, `dmg-metadata-ext4-64m.img`, `dmg-metadata-fat32-255m.img`, `dmg-metadata-fat32-511m.img`, `dmg-metadata-ntfs-64m.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 102 | **55** | 12 | 35 | 53.9% | 0/0 | 0/5 | 46 (46/0/0/0) | 113 | 487.9 |
| PhotoRec | 102 | **84** | 15 | 3 | 82.4% | 0/0 | 0/5 | 0 (0/0/0/0) | 99 | 0.6 |
| Foremost | 102 | **46** | 20 | 36 | 45.1% | 0/0 | 0/5 | 5 (5/0/0/0) | 71 | 14.1 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 102 | **55** | 12 | 35 | 53.9% | 0/0 | 0/5 | 46 (46/0/0/0) | 113 | 498.1 |

#### Damage model: interleaved overwrite (5 volumes)

Images: `dmg-interleave-exfat-255m.img`, `dmg-interleave-ext4-64m.img`, `dmg-interleave-fat32-255m.img`, `dmg-interleave-fat32-511m.img`, `dmg-interleave-ntfs-64m.img`

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum, carve only | 103 | **57** | 11 | 35 | 55.3% | 5/14 | 0/0 | 55 (51/0/4/0) | 128 | 534.0 |
| PhotoRec | 103 | **85** | 14 | 4 | 82.5% | 0/14 | 0/0 | 0 (0/0/0/0) | 99 | 0.7 |
| Foremost | 103 | **52** | 20 | 31 | 50.5% | 5/14 | 0/0 | 9 (5/0/4/0) | 86 | 14.1 |
| *not a carver's peer:* | | | | | | | | | | |
| Sanctum, undelete + carve | 103 | **93** | 3 | 7 | 90.3% | 7/14 (2 identical) | 0/0 | 56 (51/0/4/1) | 172 | 537.4 |

#### By format: benchmark volumes and every damage model (25 images)

Cells are byte-identical / corrupt, over FULL objects of that format.

| Format | Header in Sanctum's table | FULL | Sanctum carve | PhotoRec | Foremost | Sanctum full |
|---|---|---:|---:|---:|---:|---:|
| DOCX | yes | 20 | 20/0 | 20/0 | 0/20 | 20/0 |
| GIF | yes | 24 | 24/0 | 20/0 | 24/0 | 24/0 |
| JPEG | yes | 79 | 79/0 | 69/0 | 69/10 | 79/0 |
| MP4 | yes | 20 | 0/20 | 20/0 | 20/0 | 12/8 |
| PDF | yes | 40 | 40/0 | 40/0 | 40/0 | 40/0 |
| PNG | yes | 51 | 47/4 | 47/0 | 47/0 | 47/4 |
| SQLite | yes | 15 | 12/3 | 12/3 | 0/0 | 12/3 |
| TIFF | yes | 20 | 0/20 | 0/20 | 0/0 | 12/8 |
| XLSX | yes | 17 | 17/0 | 17/0 | 0/17 | 17/0 |
| ZIP | yes | 24 | 24/0 | 24/0 | 0/24 | 24/0 |
| BMP | no | 20 | 0/0 | 20/0 | 20/0 | 12/0 |
| GZIP | no | 20 | 0/0 | 0/20 | 0/0 | 12/0 |
| HTML | no | 20 | 0/0 | 20/0 | 0/0 | 12/0 |
| RTF | no | 20 | 0/0 | 20/0 | 0/0 | 12/0 |
| TAR | no | 16 | 0/0 | 3/13 | 0/0 | 9/0 |
| WAV | no | 20 | 0/0 | 20/0 | 0/20 | 12/0 |
| WebP | no | 20 | 0/0 | 20/0 | 0/0 | 12/0 |

#### Object by object: who returned a FULL file byte-identical

| Pair | Both | Only Sanctum carve | Only the other tool | Neither |
|---|---:|---:|---:|---:|
| Sanctum carve vs PhotoRec | 375 | 36 | 123 | 63 |
| Sanctum carve vs Foremost | 299 | 112 | 40 | 146 |

<details><summary>PhotoRec only (Sanctum carve vs PhotoRec): 123 objects</summary>

`dmg-interleave-exfat-255m.img:clip.mp4`, `dmg-interleave-exfat-255m.img:icon.bmp`, `dmg-interleave-exfat-255m.img:memo.rtf`, `dmg-interleave-exfat-255m.img:page.html`, `dmg-interleave-exfat-255m.img:photo.webp`, `dmg-interleave-exfat-255m.img:voice.wav`, `dmg-interleave-ext4-64m.img:clip.mp4`, `dmg-interleave-ext4-64m.img:icon.bmp`, `dmg-interleave-ext4-64m.img:memo.rtf`, `dmg-interleave-ext4-64m.img:page.html`, `dmg-interleave-ext4-64m.img:photo.webp`, `dmg-interleave-ext4-64m.img:voice.wav`, `dmg-interleave-fat32-255m.img:backup.tar`, `dmg-interleave-fat32-255m.img:clip.mp4`, `dmg-interleave-fat32-255m.img:icon.bmp`, `dmg-interleave-fat32-255m.img:memo.rtf`, `dmg-interleave-fat32-255m.img:page.html`, `dmg-interleave-fat32-255m.img:photo.webp`, `dmg-interleave-fat32-255m.img:voice.wav`, `dmg-interleave-fat32-511m.img:clip.mp4`, `dmg-interleave-fat32-511m.img:icon.bmp`, `dmg-interleave-fat32-511m.img:memo.rtf`, `dmg-interleave-fat32-511m.img:page.html`, `dmg-interleave-fat32-511m.img:photo.webp`, `dmg-interleave-fat32-511m.img:voice.wav`, `dmg-interleave-ntfs-64m.img:clip.mp4`, `dmg-interleave-ntfs-64m.img:icon.bmp`, `dmg-interleave-ntfs-64m.img:memo.rtf`, `dmg-interleave-ntfs-64m.img:page.html`, `dmg-interleave-ntfs-64m.img:photo.webp`, `dmg-interleave-ntfs-64m.img:voice.wav`, `dmg-metadata-exfat-255m.img:clip.mp4`, `dmg-metadata-exfat-255m.img:icon.bmp`, `dmg-metadata-exfat-255m.img:memo.rtf`, `dmg-metadata-exfat-255m.img:page.html`, `dmg-metadata-exfat-255m.img:photo.webp`, `dmg-metadata-exfat-255m.img:voice.wav`, `dmg-metadata-ext4-64m.img:clip.mp4`, `dmg-metadata-ext4-64m.img:icon.bmp`, `dmg-metadata-ext4-64m.img:memo.rtf`, `dmg-metadata-ext4-64m.img:page.html`, `dmg-metadata-ext4-64m.img:photo.webp`, `dmg-metadata-ext4-64m.img:voice.wav`, `dmg-metadata-fat32-255m.img:backup.tar`, `dmg-metadata-fat32-255m.img:clip.mp4`, `dmg-metadata-fat32-255m.img:icon.bmp`, `dmg-metadata-fat32-255m.img:memo.rtf`, `dmg-metadata-fat32-255m.img:page.html`, `dmg-metadata-fat32-255m.img:photo.webp`, `dmg-metadata-fat32-255m.img:voice.wav`, `dmg-metadata-fat32-511m.img:clip.mp4`, `dmg-metadata-fat32-511m.img:icon.bmp`, `dmg-metadata-fat32-511m.img:memo.rtf`, `dmg-metadata-fat32-511m.img:page.html`, `dmg-metadata-fat32-511m.img:photo.webp`, `dmg-metadata-fat32-511m.img:voice.wav`, `dmg-metadata-ntfs-64m.img:clip.mp4`, `dmg-metadata-ntfs-64m.img:icon.bmp`, `dmg-metadata-ntfs-64m.img:memo.rtf`, `dmg-metadata-ntfs-64m.img:page.html`, `dmg-metadata-ntfs-64m.img:photo.webp`, `dmg-metadata-ntfs-64m.img:voice.wav`, `dmg-zeroed-exfat-255m.img:clip.mp4`, `dmg-zeroed-exfat-255m.img:icon.bmp`, `dmg-zeroed-exfat-255m.img:memo.rtf`, `dmg-zeroed-exfat-255m.img:page.html`, `dmg-zeroed-exfat-255m.img:photo.webp`, `dmg-zeroed-exfat-255m.img:voice.wav`, `dmg-zeroed-ext4-64m.img:clip.mp4`, `dmg-zeroed-ext4-64m.img:icon.bmp`, `dmg-zeroed-ext4-64m.img:memo.rtf`, `dmg-zeroed-ext4-64m.img:page.html`, `dmg-zeroed-ext4-64m.img:photo.webp`, `dmg-zeroed-ext4-64m.img:voice.wav`, `dmg-zeroed-fat32-255m.img:clip.mp4`, `dmg-zeroed-fat32-255m.img:icon.bmp`, `dmg-zeroed-fat32-255m.img:memo.rtf`, `dmg-zeroed-fat32-255m.img:page.html`, `dmg-zeroed-fat32-255m.img:photo.webp`, `dmg-zeroed-fat32-255m.img:voice.wav`, `dmg-zeroed-fat32-511m.img:clip.mp4`, `dmg-zeroed-fat32-511m.img:icon.bmp`, `dmg-zeroed-fat32-511m.img:memo.rtf`, `dmg-zeroed-fat32-511m.img:page.html`, `dmg-zeroed-fat32-511m.img:photo.webp`, `dmg-zeroed-fat32-511m.img:voice.wav`, `dmg-zeroed-ntfs-64m.img:clip.mp4`, `dmg-zeroed-ntfs-64m.img:icon.bmp`, `dmg-zeroed-ntfs-64m.img:memo.rtf`, `dmg-zeroed-ntfs-64m.img:page.html`, `dmg-zeroed-ntfs-64m.img:photo.webp`, `dmg-zeroed-ntfs-64m.img:voice.wav`, `media-exfat-255m.img:clip.mp4`, `media-exfat-255m.img:icon.bmp`, `media-exfat-255m.img:memo.rtf`, `media-exfat-255m.img:page.html`, `media-exfat-255m.img:photo.webp`, `media-exfat-255m.img:voice.wav`, `media-ext4-64m.img:clip.mp4`, `media-ext4-64m.img:icon.bmp`, `media-ext4-64m.img:memo.rtf`, `media-ext4-64m.img:page.html`, `media-ext4-64m.img:photo.webp`, `media-ext4-64m.img:voice.wav`, `media-fat32-255m.img:backup.tar`, `media-fat32-255m.img:clip.mp4`, `media-fat32-255m.img:icon.bmp`, `media-fat32-255m.img:memo.rtf`, `media-fat32-255m.img:page.html`, `media-fat32-255m.img:photo.webp`, `media-fat32-255m.img:voice.wav`, `media-fat32-511m.img:clip.mp4`, `media-fat32-511m.img:icon.bmp`, `media-fat32-511m.img:memo.rtf`, `media-fat32-511m.img:page.html`, `media-fat32-511m.img:photo.webp`, `media-fat32-511m.img:voice.wav`, `media-ntfs-64m.img:clip.mp4`, `media-ntfs-64m.img:icon.bmp`, `media-ntfs-64m.img:memo.rtf`, `media-ntfs-64m.img:page.html`, `media-ntfs-64m.img:photo.webp`, `media-ntfs-64m.img:voice.wav`

</details>

<details><summary>Sanctum carve only (Sanctum carve vs PhotoRec): 36 objects</summary>

`dmg-interleave-fat32-255m.img:frag.jpg`, `dmg-interleave-fat32-511m.img:frag.jpg`, `dmg-interleave-ntfs-64m.img:sticker.gif`, `dmg-metadata-fat32-255m.img:frag.jpg`, `dmg-metadata-fat32-511m.img:frag.jpg`, `dmg-truncation-fat32-255m.img:frag.jpg`, `dmg-truncation-fat32-511m.img:frag.jpg`, `dmg-truncation-ntfs-64m.img:sticker.gif`, `dmg-zeroed-fat32-255m.img:frag.jpg`, `dmg-zeroed-fat32-511m.img:frag.jpg`, `dmg-zeroed-ntfs-64m.img:sticker.gif`, `flat-offset1337.img:archive.zip@1574201`, `flat-offset1337.img:budget-macros.docm@2622777`, `flat-offset1337.img:chart.png@5768505`, `flat-offset1337.img:clip.mp4@4195641`, `flat-offset1337.img:contacts.sqlite@3147065`, `flat-offset1337.img:invoice.pdf@6292793`, `flat-offset1337.img:letter.docx@2098489`, `flat-offset1337.img:locked.zip@12059961`, `flat-offset1337.img:messages.sqlite@7341369`, `flat-offset1337.img:photo-with-gps.jpg@1337`, `flat-offset1337.img:screenshot.png@525625`, `flat-offset1337.img:screenshot.png@7865657`, `flat-offset1337.img:screenshot.png@8389945`, `flat-offset1337.img:second-photo.jpg@4719929`, `flat-offset1337.img:sheet.xlsx@6817081`, `flat-offset1337.img:statement.pdf@1049913`, `flat-offset1337.img:sticker.gif@3671353`, `flat-offset1337.img:third-photo.jpg@5244217`, `fs-damaged-parttable.img:holiday.jpg`, `fs-damaged-parttable.img:kept.gif`, `fs-two-partitions.img:holiday.jpg`, `fs-two-partitions.img:kept.gif`, `media-fat32-255m.img:frag.jpg`, `media-fat32-511m.img:frag.jpg`, `media-ntfs-64m.img:sticker.gif`

</details>

<details><summary>Sanctum carve only (Sanctum carve vs Foremost): 112 objects</summary>

`dmg-interleave-exfat-255m.img:archive.zip`, `dmg-interleave-exfat-255m.img:budget.xlsx`, `dmg-interleave-exfat-255m.img:letter.docx`, `dmg-interleave-ext4-64m.img:archive.zip`, `dmg-interleave-ext4-64m.img:budget.xlsx`, `dmg-interleave-ext4-64m.img:letter.docx`, `dmg-interleave-fat32-255m.img:archive.zip`, `dmg-interleave-fat32-255m.img:budget.xlsx`, `dmg-interleave-fat32-255m.img:frag.jpg`, `dmg-interleave-fat32-255m.img:letter.docx`, `dmg-interleave-fat32-511m.img:archive.zip`, `dmg-interleave-fat32-511m.img:budget.xlsx`, `dmg-interleave-fat32-511m.img:frag.jpg`, `dmg-interleave-fat32-511m.img:letter.docx`, `dmg-interleave-ntfs-64m.img:archive.zip`, `dmg-metadata-exfat-255m.img:archive.zip`, `dmg-metadata-exfat-255m.img:budget.xlsx`, `dmg-metadata-exfat-255m.img:contacts.sqlite`, `dmg-metadata-exfat-255m.img:letter.docx`, `dmg-metadata-ext4-64m.img:archive.zip`, `dmg-metadata-ext4-64m.img:budget.xlsx`, `dmg-metadata-ext4-64m.img:letter.docx`, `dmg-metadata-fat32-255m.img:archive.zip`, `dmg-metadata-fat32-255m.img:budget.xlsx`, `dmg-metadata-fat32-255m.img:contacts.sqlite`, `dmg-metadata-fat32-255m.img:frag.jpg`, `dmg-metadata-fat32-255m.img:letter.docx`, `dmg-metadata-fat32-511m.img:archive.zip`, `dmg-metadata-fat32-511m.img:budget.xlsx`, `dmg-metadata-fat32-511m.img:contacts.sqlite`, `dmg-metadata-fat32-511m.img:frag.jpg`, `dmg-metadata-fat32-511m.img:letter.docx`, `dmg-metadata-ntfs-64m.img:archive.zip`, `dmg-metadata-ntfs-64m.img:contacts.sqlite`, `dmg-truncation-exfat-255m.img:archive.zip`, `dmg-truncation-exfat-255m.img:budget.xlsx`, `dmg-truncation-exfat-255m.img:letter.docx`, `dmg-truncation-ext4-64m.img:archive.zip`, `dmg-truncation-ext4-64m.img:letter.docx`, `dmg-truncation-fat32-255m.img:archive.zip`, `dmg-truncation-fat32-255m.img:frag.jpg`, `dmg-truncation-fat32-255m.img:letter.docx`, `dmg-truncation-fat32-511m.img:archive.zip`, `dmg-truncation-fat32-511m.img:frag.jpg`, `dmg-truncation-fat32-511m.img:letter.docx`, `dmg-truncation-ntfs-64m.img:archive.zip`, `dmg-truncation-ntfs-64m.img:contacts.sqlite`, `dmg-zeroed-exfat-255m.img:archive.zip`, `dmg-zeroed-exfat-255m.img:budget.xlsx`, `dmg-zeroed-exfat-255m.img:contacts.sqlite`, `dmg-zeroed-exfat-255m.img:letter.docx`, `dmg-zeroed-ext4-64m.img:archive.zip`, `dmg-zeroed-ext4-64m.img:budget.xlsx`, `dmg-zeroed-ext4-64m.img:letter.docx`, `dmg-zeroed-fat32-255m.img:archive.zip`, `dmg-zeroed-fat32-255m.img:budget.xlsx`, `dmg-zeroed-fat32-255m.img:contacts.sqlite`, `dmg-zeroed-fat32-255m.img:frag.jpg`, `dmg-zeroed-fat32-255m.img:letter.docx`, `dmg-zeroed-fat32-511m.img:archive.zip`, `dmg-zeroed-fat32-511m.img:budget.xlsx`, `dmg-zeroed-fat32-511m.img:contacts.sqlite`, `dmg-zeroed-fat32-511m.img:frag.jpg`, `dmg-zeroed-fat32-511m.img:letter.docx`, `flat-aligned.img:archive.zip@1576960`, `flat-aligned.img:budget-macros.docm@2625536`, `flat-aligned.img:clip.mp4@4198400`, `flat-aligned.img:contacts.sqlite@3149824`, `flat-aligned.img:letter.docx@2101248`, `flat-aligned.img:locked.zip@12062720`, `flat-aligned.img:messages.sqlite@7344128`, `flat-aligned.img:sheet.xlsx@6819840`, `flat-offset1337.img:archive.zip@1574201`, `flat-offset1337.img:budget-macros.docm@2622777`, `flat-offset1337.img:clip.mp4@4195641`, `flat-offset1337.img:contacts.sqlite@3147065`, `flat-offset1337.img:letter.docx@2098489`, `flat-offset1337.img:locked.zip@12059961`, `flat-offset1337.img:messages.sqlite@7341369`, `flat-offset1337.img:sheet.xlsx@6817081`, `fs-damaged-boot.img:18-contacts.sqlite`, `fs-damaged-boot.img:19-messages.sqlite`, `fs-damaged-parttable.img:18-contacts.sqlite`, `fs-damaged-parttable.img:19-messages.sqlite`, `fs-ext2.img:kept.zip`, `fs-ext3.img:kept.zip`, `fs-ext4.img:kept.zip`, `fs-ntfs-reused.img:18-contacts.sqlite`, `fs-ntfs-reused.img:19-messages.sqlite`, `fs-ntfs.img:18-contacts.sqlite`, `fs-ntfs.img:19-messages.sqlite`, `fs-two-partitions.img:18-contacts.sqlite`, `fs-two-partitions.img:19-messages.sqlite`, `media-exfat-255m.img:archive.zip`, `media-exfat-255m.img:budget.xlsx`, `media-exfat-255m.img:contacts.sqlite`, `media-exfat-255m.img:letter.docx`, `media-ext4-64m.img:archive.zip`, `media-ext4-64m.img:budget.xlsx`, `media-ext4-64m.img:letter.docx`, `media-fat32-255m.img:archive.zip`, `media-fat32-255m.img:budget.xlsx`, `media-fat32-255m.img:contacts.sqlite`, `media-fat32-255m.img:frag.jpg`, `media-fat32-255m.img:letter.docx`, `media-fat32-511m.img:archive.zip`, `media-fat32-511m.img:budget.xlsx`, `media-fat32-511m.img:contacts.sqlite`, `media-fat32-511m.img:frag.jpg`, `media-fat32-511m.img:letter.docx`, `media-ntfs-64m.img:archive.zip`, `media-ntfs-64m.img:contacts.sqlite`

</details>

<details><summary>Foremost only (Sanctum carve vs Foremost): 40 objects</summary>

`dmg-interleave-exfat-255m.img:clip.mp4`, `dmg-interleave-exfat-255m.img:icon.bmp`, `dmg-interleave-ext4-64m.img:clip.mp4`, `dmg-interleave-ext4-64m.img:icon.bmp`, `dmg-interleave-fat32-255m.img:clip.mp4`, `dmg-interleave-fat32-255m.img:icon.bmp`, `dmg-interleave-fat32-511m.img:clip.mp4`, `dmg-interleave-fat32-511m.img:icon.bmp`, `dmg-interleave-ntfs-64m.img:clip.mp4`, `dmg-interleave-ntfs-64m.img:icon.bmp`, `dmg-metadata-exfat-255m.img:clip.mp4`, `dmg-metadata-exfat-255m.img:icon.bmp`, `dmg-metadata-ext4-64m.img:clip.mp4`, `dmg-metadata-ext4-64m.img:icon.bmp`, `dmg-metadata-fat32-255m.img:clip.mp4`, `dmg-metadata-fat32-255m.img:icon.bmp`, `dmg-metadata-fat32-511m.img:clip.mp4`, `dmg-metadata-fat32-511m.img:icon.bmp`, `dmg-metadata-ntfs-64m.img:clip.mp4`, `dmg-metadata-ntfs-64m.img:icon.bmp`, `dmg-zeroed-exfat-255m.img:clip.mp4`, `dmg-zeroed-exfat-255m.img:icon.bmp`, `dmg-zeroed-ext4-64m.img:clip.mp4`, `dmg-zeroed-ext4-64m.img:icon.bmp`, `dmg-zeroed-fat32-255m.img:clip.mp4`, `dmg-zeroed-fat32-255m.img:icon.bmp`, `dmg-zeroed-fat32-511m.img:clip.mp4`, `dmg-zeroed-fat32-511m.img:icon.bmp`, `dmg-zeroed-ntfs-64m.img:clip.mp4`, `dmg-zeroed-ntfs-64m.img:icon.bmp`, `media-exfat-255m.img:clip.mp4`, `media-exfat-255m.img:icon.bmp`, `media-ext4-64m.img:clip.mp4`, `media-ext4-64m.img:icon.bmp`, `media-fat32-255m.img:clip.mp4`, `media-fat32-255m.img:icon.bmp`, `media-fat32-511m.img:clip.mp4`, `media-fat32-511m.img:icon.bmp`, `media-ntfs-64m.img:clip.mp4`, `media-ntfs-64m.img:icon.bmp`

</details>

#### Supplementary, not comparable: Sanctum HIGH and MEDIUM only

| Row | FULL files | Byte-identical | Corrupt | Missed | Identical / FULL | PARTIAL returned | GONE returned | False positives (frag/decoy/amb/unrel) | Outputs | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sanctum carve, HIGH+MEDIUM only (all 40 images) | 591 | **405** | 20 | 166 | 68.5% | 0/56 | 0/64 | 31 (22/0/8/1) | 456 | 2144.7 |
| Sanctum full, HIGH+MEDIUM only (all 40 images) | 591 | **512** | 12 | 67 | 86.6% | 14/56 (11 identical) | 0/64 | 39 (22/0/8/9) | 581 | 2169.6 |

#### Every run

| Image | Row | FULL | Identical | Corrupt | Missed | PARTIAL ret. | FP | Outputs | Time (s) | Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `dmg-interleave-exfat-255m.img` | Sanctum, carve only | 21 | 11 | 3 | 7 | 1/2 | 10 | 25 | 117.10 | 0 |
| `dmg-interleave-exfat-255m.img` | PhotoRec | 21 | 17 | 3 | 1 | 0/2 | 0 | 20 | 0.12 | 0 |
| `dmg-interleave-exfat-255m.img` | Foremost | 21 | 10 | 4 | 7 | 1/2 | 1 | 16 | 3.12 | 0 |
| `dmg-interleave-exfat-255m.img` | Sanctum, undelete + carve | 21 | 20 | 1 | 0 | 1/2 | 10 | 36 | 118.34 | 0 |
| `dmg-interleave-ext4-64m.img` | Sanctum, carve only | 21 | 12 | 2 | 7 | 1/2 | 10 | 25 | 36.15 | 0 |
| `dmg-interleave-ext4-64m.img` | PhotoRec | 21 | 18 | 3 | 0 | 0/2 | 0 | 21 | 0.06 | 0 |
| `dmg-interleave-ext4-64m.img` | Foremost | 21 | 11 | 4 | 6 | 1/2 | 1 | 17 | 0.82 | 0 |
| `dmg-interleave-ext4-64m.img` | Sanctum, undelete + carve | 21 | 12 | 2 | 7 | 1/2 | 11 | 26 | 36.00 | 0 |
| `dmg-interleave-fat32-255m.img` | Sanctum, carve only | 22 | 13 | 2 | 7 | 1/2 | 10 | 26 | 115.49 | 0 |
| `dmg-interleave-fat32-255m.img` | PhotoRec | 22 | 19 | 2 | 1 | 0/2 | 0 | 21 | 0.21 | 0 |
| `dmg-interleave-fat32-255m.img` | Foremost | 22 | 11 | 5 | 6 | 1/2 | 1 | 18 | 3.12 | 0 |
| `dmg-interleave-fat32-255m.img` | Sanctum, undelete + carve | 22 | 22 | 0 | 0 | 1/2 | 10 | 36 | 116.69 | 0 |
| `dmg-interleave-fat32-511m.img` | Sanctum, carve only | 22 | 13 | 2 | 7 | 1/2 | 11 | 27 | 237.62 | 0 |
| `dmg-interleave-fat32-511m.img` | PhotoRec | 22 | 18 | 3 | 1 | 0/2 | 0 | 21 | 0.21 | 0 |
| `dmg-interleave-fat32-511m.img` | Foremost | 22 | 11 | 5 | 6 | 1/2 | 1 | 18 | 6.18 | 0 |
| `dmg-interleave-fat32-511m.img` | Sanctum, undelete + carve | 22 | 22 | 0 | 0 | 1/2 | 11 | 37 | 237.83 | 0 |
| `dmg-interleave-ntfs-64m.img` | Sanctum, carve only | 17 | 8 | 2 | 7 | 1/6 | 14 | 25 | 27.68 | 0 |
| `dmg-interleave-ntfs-64m.img` | PhotoRec | 17 | 13 | 3 | 1 | 0/6 | 0 | 16 | 0.06 | 0 |
| `dmg-interleave-ntfs-64m.img` | Foremost | 17 | 9 | 2 | 6 | 1/6 | 5 | 17 | 0.82 | 0 |
| `dmg-interleave-ntfs-64m.img` | Sanctum, undelete + carve | 17 | 17 | 0 | 0 | 3/6 | 14 | 37 | 28.53 | 0 |
| `dmg-metadata-exfat-255m.img` | Sanctum, carve only | 21 | 11 | 3 | 7 | 0/0 | 10 | 24 | 109.83 | 0 |
| `dmg-metadata-exfat-255m.img` | PhotoRec | 21 | 17 | 3 | 1 | 0/0 | 0 | 20 | 0.11 | 0 |
| `dmg-metadata-exfat-255m.img` | Foremost | 21 | 9 | 4 | 8 | 0/0 | 1 | 14 | 3.12 | 0 |
| `dmg-metadata-exfat-255m.img` | Sanctum, undelete + carve | 21 | 11 | 3 | 7 | 0/0 | 10 | 24 | 111.52 | 0 |
| `dmg-metadata-ext4-64m.img` | Sanctum, carve only | 21 | 11 | 3 | 7 | 0/0 | 10 | 24 | 27.89 | 0 |
| `dmg-metadata-ext4-64m.img` | PhotoRec | 21 | 17 | 4 | 0 | 0/0 | 0 | 21 | 0.06 | 0 |
| `dmg-metadata-ext4-64m.img` | Foremost | 21 | 10 | 4 | 7 | 0/0 | 1 | 15 | 0.82 | 0 |
| `dmg-metadata-ext4-64m.img` | Sanctum, undelete + carve | 21 | 11 | 3 | 7 | 0/0 | 10 | 24 | 28.03 | 0 |
| `dmg-metadata-fat32-255m.img` | Sanctum, carve only | 22 | 13 | 2 | 7 | 0/0 | 10 | 25 | 109.62 | 0 |
| `dmg-metadata-fat32-255m.img` | PhotoRec | 22 | 19 | 2 | 1 | 0/0 | 0 | 21 | 0.21 | 0 |
| `dmg-metadata-fat32-255m.img` | Foremost | 22 | 10 | 5 | 7 | 0/0 | 1 | 16 | 3.12 | 0 |
| `dmg-metadata-fat32-255m.img` | Sanctum, undelete + carve | 22 | 13 | 2 | 7 | 0/0 | 10 | 25 | 113.14 | 0 |
| `dmg-metadata-fat32-511m.img` | Sanctum, carve only | 22 | 13 | 2 | 7 | 0/0 | 10 | 25 | 230.67 | 0 |
| `dmg-metadata-fat32-511m.img` | PhotoRec | 22 | 18 | 3 | 1 | 0/0 | 0 | 21 | 0.11 | 0 |
| `dmg-metadata-fat32-511m.img` | Foremost | 22 | 10 | 5 | 7 | 0/0 | 1 | 16 | 6.23 | 0 |
| `dmg-metadata-fat32-511m.img` | Sanctum, undelete + carve | 22 | 13 | 2 | 7 | 0/0 | 10 | 25 | 235.52 | 0 |
| `dmg-metadata-ntfs-64m.img` | Sanctum, carve only | 16 | 7 | 2 | 7 | 0/0 | 6 | 15 | 9.89 | 0 |
| `dmg-metadata-ntfs-64m.img` | PhotoRec | 16 | 13 | 3 | 0 | 0/0 | 0 | 16 | 0.06 | 0 |
| `dmg-metadata-ntfs-64m.img` | Foremost | 16 | 7 | 2 | 7 | 0/0 | 1 | 10 | 0.82 | 0 |
| `dmg-metadata-ntfs-64m.img` | Sanctum, undelete + carve | 16 | 7 | 2 | 7 | 0/0 | 6 | 15 | 9.89 | 0 |
| `dmg-truncation-exfat-255m.img` | Sanctum, carve only | 9 | 9 | 0 | 0 | 1/2 | 10 | 20 | 1.27 | 0 |
| `dmg-truncation-exfat-255m.img` | PhotoRec | 9 | 9 | 0 | 0 | 1/2 | 0 | 10 | 0.03 | 0 |
| `dmg-truncation-exfat-255m.img` | Foremost | 9 | 6 | 3 | 0 | 0/2 | 0 | 9 | 0.06 | 0 |
| `dmg-truncation-exfat-255m.img` | Sanctum, undelete + carve | 9 | 9 | 0 | 0 | 2/2 | 11 | 22 | 1.52 | 0 |
| `dmg-truncation-ext4-64m.img` | Sanctum, carve only | 10 | 10 | 0 | 0 | 0/0 | 8 | 18 | 1.27 | 0 |
| `dmg-truncation-ext4-64m.img` | PhotoRec | 10 | 10 | 0 | 0 | 0/0 | 0 | 10 | 0.03 | 0 |
| `dmg-truncation-ext4-64m.img` | Foremost | 10 | 8 | 2 | 0 | 0/0 | 0 | 10 | 0.12 | 0 |
| `dmg-truncation-ext4-64m.img` | Sanctum, undelete + carve | 10 | 10 | 0 | 0 | 0/0 | 9 | 19 | 1.42 | 0 |
| `dmg-truncation-fat32-255m.img` | Sanctum, carve only | 11 | 11 | 0 | 0 | 0/0 | 8 | 19 | 1.47 | 0 |
| `dmg-truncation-fat32-255m.img` | PhotoRec | 11 | 10 | 0 | 1 | 0/0 | 0 | 10 | 0.03 | 0 |
| `dmg-truncation-fat32-255m.img` | Foremost | 11 | 8 | 3 | 0 | 0/0 | 0 | 11 | 0.06 | 0 |
| `dmg-truncation-fat32-255m.img` | Sanctum, undelete + carve | 11 | 11 | 0 | 0 | 0/0 | 8 | 19 | 1.47 | 0 |
| `dmg-truncation-fat32-511m.img` | Sanctum, carve only | 11 | 11 | 0 | 0 | 0/0 | 8 | 19 | 1.47 | 0 |
| `dmg-truncation-fat32-511m.img` | PhotoRec | 11 | 10 | 0 | 1 | 0/0 | 0 | 10 | 0.03 | 0 |
| `dmg-truncation-fat32-511m.img` | Foremost | 11 | 8 | 3 | 0 | 0/0 | 0 | 11 | 0.03 | 0 |
| `dmg-truncation-fat32-511m.img` | Sanctum, undelete + carve | 11 | 11 | 0 | 0 | 0/0 | 9 | 20 | 1.72 | 0 |
| `dmg-truncation-ntfs-64m.img` | Sanctum, carve only | 8 | 8 | 0 | 0 | 0/5 | 15 | 23 | 7.33 | 0 |
| `dmg-truncation-ntfs-64m.img` | PhotoRec | 8 | 7 | 0 | 1 | 0/5 | 0 | 7 | 0.03 | 0 |
| `dmg-truncation-ntfs-64m.img` | Foremost | 8 | 6 | 1 | 1 | 0/5 | 4 | 11 | 0.47 | 0 |
| `dmg-truncation-ntfs-64m.img` | Sanctum, undelete + carve | 8 | 8 | 0 | 0 | 3/5 | 16 | 27 | 7.63 | 0 |
| `dmg-zeroed-exfat-255m.img` | Sanctum, carve only | 19 | 10 | 3 | 6 | 1/1 | 10 | 24 | 117.52 | 0 |
| `dmg-zeroed-exfat-255m.img` | PhotoRec | 19 | 16 | 2 | 1 | 0/1 | 0 | 18 | 0.12 | 0 |
| `dmg-zeroed-exfat-255m.img` | Foremost | 19 | 8 | 4 | 7 | 1/1 | 1 | 14 | 3.12 | 0 |
| `dmg-zeroed-exfat-255m.img` | Sanctum, undelete + carve | 19 | 18 | 1 | 0 | 1/1 | 11 | 34 | 119.73 | 0 |
| `dmg-zeroed-ext4-64m.img` | Sanctum, carve only | 19 | 10 | 3 | 6 | 1/1 | 10 | 24 | 37.15 | 0 |
| `dmg-zeroed-ext4-64m.img` | PhotoRec | 19 | 16 | 3 | 0 | 0/1 | 0 | 19 | 0.12 | 0 |
| `dmg-zeroed-ext4-64m.img` | Foremost | 19 | 9 | 4 | 6 | 1/1 | 1 | 15 | 0.82 | 0 |
| `dmg-zeroed-ext4-64m.img` | Sanctum, undelete + carve | 19 | 10 | 3 | 6 | 1/1 | 11 | 25 | 37.75 | 0 |
| `dmg-zeroed-fat32-255m.img` | Sanctum, carve only | 20 | 12 | 2 | 6 | 1/1 | 10 | 25 | 117.09 | 0 |
| `dmg-zeroed-fat32-255m.img` | PhotoRec | 20 | 17 | 2 | 1 | 0/1 | 0 | 19 | 0.21 | 0 |
| `dmg-zeroed-fat32-255m.img` | Foremost | 20 | 9 | 5 | 6 | 1/1 | 1 | 16 | 3.12 | 0 |
| `dmg-zeroed-fat32-255m.img` | Sanctum, undelete + carve | 20 | 20 | 0 | 0 | 1/1 | 11 | 34 | 118.24 | 0 |
| `dmg-zeroed-fat32-511m.img` | Sanctum, carve only | 20 | 12 | 2 | 6 | 1/1 | 10 | 25 | 247.83 | 0 |
| `dmg-zeroed-fat32-511m.img` | PhotoRec | 20 | 17 | 2 | 1 | 0/1 | 0 | 19 | 0.21 | 0 |
| `dmg-zeroed-fat32-511m.img` | Foremost | 20 | 9 | 5 | 6 | 1/1 | 1 | 16 | 6.33 | 0 |
| `dmg-zeroed-fat32-511m.img` | Sanctum, undelete + carve | 20 | 20 | 0 | 0 | 1/1 | 11 | 34 | 244.48 | 0 |
| `dmg-zeroed-ntfs-64m.img` | Sanctum, carve only | 11 | 2 | 2 | 7 | 1/6 | 8 | 13 | 19.80 | 0 |
| `dmg-zeroed-ntfs-64m.img` | PhotoRec | 11 | 7 | 3 | 1 | 0/6 | 0 | 10 | 0.11 | 0 |
| `dmg-zeroed-ntfs-64m.img` | Foremost | 11 | 4 | 1 | 6 | 1/6 | 5 | 11 | 0.82 | 0 |
| `dmg-zeroed-ntfs-64m.img` | Sanctum, undelete + carve | 11 | 11 | 0 | 0 | 3/6 | 12 | 28 | 23.97 | 0 |
| `flat-aligned.img` | Sanctum, carve only | 15 | 15 | 0 | 0 | 2/3 | 15 | 33 | 4.98 | 0 |
| `flat-aligned.img` | PhotoRec | 15 | 15 | 0 | 0 | 0/3 | 0 | 19 | 0.03 | 0 |
| `flat-aligned.img` | Foremost | 15 | 7 | 6 | 2 | 1/3 | 0 | 17 | 0.17 | 0 |
| `flat-aligned.img` | Sanctum, undelete + carve | 15 | 15 | 0 | 0 | 2/3 | 15 | 33 | 4.98 | 0 |
| `flat-offset1337.img` | Sanctum, carve only | 15 | 15 | 0 | 0 | 2/3 | 15 | 33 | 4.93 | 0 |
| `flat-offset1337.img` | PhotoRec | 15 | 0 | 0 | 15 | 0/3 | 0 | 0 | 0.03 | 0 |
| `flat-offset1337.img` | Foremost | 15 | 7 | 6 | 2 | 1/3 | 0 | 17 | 0.17 | 0 |
| `flat-offset1337.img` | Sanctum, undelete + carve | 15 | 15 | 0 | 0 | 2/3 | 15 | 33 | 4.98 | 0 |
| `fs-damaged-boot.img` | Sanctum, carve only | 17 | 17 | 0 | 0 | 3/3 | 11 | 31 | 6.63 | 0 |
| `fs-damaged-boot.img` | PhotoRec | 17 | 17 | 0 | 0 | 0/3 | 0 | 17 | 0.03 | 0 |
| `fs-damaged-boot.img` | Foremost | 17 | 15 | 0 | 2 | 3/3 | 0 | 18 | 0.31 | 0 |
| `fs-damaged-boot.img` | Sanctum, undelete + carve | 17 | 17 | 0 | 0 | 3/3 | 11 | 31 | 6.83 | 0 |
| `fs-damaged-parttable.img` | Sanctum, carve only | 20 | 20 | 0 | 0 | 3/3 | 12 | 35 | 17.00 | 0 |
| `fs-damaged-parttable.img` | PhotoRec | 20 | 18 | 0 | 2 | 0/3 | 0 | 18 | 0.11 | 0 |
| `fs-damaged-parttable.img` | Foremost | 20 | 18 | 0 | 2 | 3/3 | 0 | 21 | 0.87 | 0 |
| `fs-damaged-parttable.img` | Sanctum, undelete + carve | 20 | 20 | 0 | 0 | 3/3 | 12 | 35 | 17.05 | 0 |
| `fs-exfat.img` | Sanctum, carve only | 3 | 2 | 1 | 0 | 0/0 | 0 | 3 | 0.47 | 0 |
| `fs-exfat.img` | PhotoRec | 3 | 2 | 0 | 1 | 0/0 | 0 | 2 | 0.06 | 0 |
| `fs-exfat.img` | Foremost | 3 | 2 | 0 | 1 | 0/0 | 0 | 2 | 0.17 | 0 |
| `fs-exfat.img` | Sanctum, undelete + carve | 3 | 2 | 1 | 0 | 0/0 | 0 | 4 | 0.52 | 0 |
| `fs-ext2.img` | Sanctum, carve only | 3 | 2 | 1 | 0 | 0/0 | 4 | 7 | 2.17 | 0 |
| `fs-ext2.img` | PhotoRec | 3 | 2 | 0 | 1 | 0/0 | 0 | 2 | 0.07 | 0 |
| `fs-ext2.img` | Foremost | 3 | 1 | 2 | 0 | 0/0 | 0 | 3 | 0.16 | 0 |
| `fs-ext2.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 4 | 7 | 2.07 | 0 |
| `fs-ext3.img` | Sanctum, carve only | 3 | 2 | 1 | 0 | 0/0 | 4 | 7 | 2.02 | 0 |
| `fs-ext3.img` | PhotoRec | 3 | 2 | 0 | 1 | 0/0 | 0 | 2 | 0.06 | 0 |
| `fs-ext3.img` | Foremost | 3 | 1 | 2 | 0 | 0/0 | 0 | 3 | 0.17 | 0 |
| `fs-ext3.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 4 | 7 | 1.87 | 0 |
| `fs-ext4.img` | Sanctum, carve only | 3 | 3 | 0 | 0 | 0/0 | 4 | 7 | 1.72 | 0 |
| `fs-ext4.img` | PhotoRec | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.03 | 0 |
| `fs-ext4.img` | Foremost | 3 | 2 | 1 | 0 | 0/0 | 0 | 3 | 0.16 | 0 |
| `fs-ext4.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 5 | 8 | 1.72 | 0 |
| `fs-fat32-neighbours.img` | Sanctum, carve only | 3 | 3 | 0 | 0 | 0/0 | 1 | 4 | 0.82 | 0 |
| `fs-fat32-neighbours.img` | PhotoRec | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.12 | 0 |
| `fs-fat32-neighbours.img` | Foremost | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.36 | 0 |
| `fs-fat32-neighbours.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 5 | 83 | 2.92 | 0 |
| `fs-fat32-plain.img` | Sanctum, carve only | 3 | 3 | 0 | 0 | 0/0 | 1 | 4 | 0.77 | 0 |
| `fs-fat32-plain.img` | PhotoRec | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.06 | 0 |
| `fs-fat32-plain.img` | Foremost | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.52 | 0 |
| `fs-fat32-plain.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 1 | 4 | 0.82 | 0 |
| `fs-fat32.img` | Sanctum, carve only | 3 | 3 | 0 | 0 | 0/0 | 1 | 4 | 0.77 | 0 |
| `fs-fat32.img` | PhotoRec | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.12 | 0 |
| `fs-fat32.img` | Foremost | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.36 | 0 |
| `fs-fat32.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 6 | 161 | 4.92 | 0 |
| `fs-ntfs-reused.img` | Sanctum, carve only | 17 | 17 | 0 | 0 | 3/3 | 11 | 31 | 6.58 | 0 |
| `fs-ntfs-reused.img` | PhotoRec | 17 | 17 | 0 | 0 | 0/3 | 0 | 17 | 0.03 | 0 |
| `fs-ntfs-reused.img` | Foremost | 17 | 15 | 0 | 2 | 3/3 | 0 | 18 | 0.32 | 0 |
| `fs-ntfs-reused.img` | Sanctum, undelete + carve | 17 | 17 | 0 | 0 | 3/3 | 12 | 33 | 7.28 | 0 |
| `fs-ntfs.img` | Sanctum, carve only | 17 | 17 | 0 | 0 | 3/3 | 11 | 31 | 6.63 | 0 |
| `fs-ntfs.img` | PhotoRec | 17 | 17 | 0 | 0 | 0/3 | 0 | 17 | 0.03 | 0 |
| `fs-ntfs.img` | Foremost | 17 | 15 | 0 | 2 | 3/3 | 0 | 18 | 0.32 | 0 |
| `fs-ntfs.img` | Sanctum, undelete + carve | 17 | 17 | 0 | 0 | 3/3 | 11 | 32 | 6.78 | 0 |
| `fs-quick-formatted.img` | Sanctum, carve only | 3 | 3 | 0 | 0 | 0/0 | 1 | 4 | 0.77 | 0 |
| `fs-quick-formatted.img` | PhotoRec | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.06 | 0 |
| `fs-quick-formatted.img` | Foremost | 3 | 3 | 0 | 0 | 0/0 | 0 | 3 | 0.52 | 0 |
| `fs-quick-formatted.img` | Sanctum, undelete + carve | 3 | 3 | 0 | 0 | 0/0 | 1 | 4 | 0.77 | 0 |
| `fs-two-partitions.img` | Sanctum, carve only | 20 | 20 | 0 | 0 | 3/3 | 12 | 35 | 16.65 | 0 |
| `fs-two-partitions.img` | PhotoRec | 20 | 18 | 0 | 2 | 0/3 | 0 | 18 | 0.12 | 0 |
| `fs-two-partitions.img` | Foremost | 20 | 18 | 0 | 2 | 3/3 | 0 | 21 | 0.87 | 0 |
| `fs-two-partitions.img` | Sanctum, undelete + carve | 20 | 20 | 0 | 0 | 3/3 | 12 | 36 | 17.10 | 0 |
| `media-exfat-255m.img` | Sanctum, carve only | 21 | 11 | 3 | 7 | 0/0 | 10 | 24 | 110.93 | 0 |
| `media-exfat-255m.img` | PhotoRec | 21 | 17 | 3 | 1 | 0/0 | 0 | 20 | 0.11 | 0 |
| `media-exfat-255m.img` | Foremost | 21 | 9 | 4 | 8 | 0/0 | 1 | 14 | 3.12 | 0 |
| `media-exfat-255m.img` | Sanctum, undelete + carve | 21 | 20 | 1 | 0 | 0/0 | 10 | 34 | 108.82 | 0 |
| `media-ext4-64m.img` | Sanctum, carve only | 21 | 11 | 3 | 7 | 0/0 | 10 | 24 | 27.78 | 0 |
| `media-ext4-64m.img` | PhotoRec | 21 | 17 | 4 | 0 | 0/0 | 0 | 21 | 0.06 | 0 |
| `media-ext4-64m.img` | Foremost | 21 | 10 | 4 | 7 | 0/0 | 1 | 15 | 0.82 | 0 |
| `media-ext4-64m.img` | Sanctum, undelete + carve | 21 | 11 | 3 | 7 | 0/0 | 11 | 25 | 28.43 | 0 |
| `media-fat32-255m.img` | Sanctum, carve only | 22 | 13 | 2 | 7 | 0/0 | 10 | 25 | 108.07 | 0 |
| `media-fat32-255m.img` | PhotoRec | 22 | 19 | 2 | 1 | 0/0 | 0 | 21 | 0.21 | 0 |
| `media-fat32-255m.img` | Foremost | 22 | 10 | 5 | 7 | 0/0 | 1 | 16 | 3.12 | 0 |
| `media-fat32-255m.img` | Sanctum, undelete + carve | 22 | 22 | 0 | 0 | 0/0 | 10 | 34 | 109.81 | 0 |
| `media-fat32-511m.img` | Sanctum, carve only | 22 | 13 | 2 | 7 | 0/0 | 10 | 25 | 230.75 | 0 |
| `media-fat32-511m.img` | PhotoRec | 22 | 18 | 3 | 1 | 0/0 | 0 | 21 | 0.11 | 0 |
| `media-fat32-511m.img` | Foremost | 22 | 10 | 5 | 7 | 0/0 | 1 | 16 | 6.17 | 0 |
| `media-fat32-511m.img` | Sanctum, undelete + carve | 22 | 22 | 0 | 0 | 0/0 | 10 | 34 | 228.28 | 0 |
| `media-ntfs-64m.img` | Sanctum, carve only | 17 | 8 | 2 | 7 | 0/4 | 14 | 24 | 20.14 | 0 |
| `media-ntfs-64m.img` | PhotoRec | 17 | 13 | 3 | 1 | 0/4 | 0 | 16 | 0.06 | 0 |
| `media-ntfs-64m.img` | Foremost | 17 | 8 | 2 | 7 | 0/4 | 5 | 15 | 0.82 | 0 |
| `media-ntfs-64m.img` | Sanctum, undelete + carve | 17 | 17 | 0 | 0 | 2/4 | 14 | 35 | 20.26 | 0 |

#### What "corrupt" means, output by output

Over all 40 images. An output counted *corrupt* is attributed to a FULL planted file
but is not identical to it. Classified by comparing the output's recorded first 64 KiB
and its size with the planted file. JPEG rows whose outputs agree only in the first 163
bytes (the header every Pillow q95 JPEG shares) are left out: they are an artefact of
this classification, which reads only stored payloads, not of the scorer.

| Row | Format | How the output differs | Outputs |
|---|---|---|---:|
| Foremost | DOCX, XLSX, ZIP | the whole file, then **1 extra byte** | 24, 19, 31 |
| Foremost | WAV | correct prefix, **8 bytes short** | 20 |
| Foremost | JPEG (`frag.jpg`) | head + the gap's bytes: first 64 KiB agree, then foreign data | 10 |
| PhotoRec | GZIP | the whole file, then block padding (3,316 B file, 172,032 B output) | 20 |
| PhotoRec | TIFF | the whole file, then block padding (49,292 → 53,248 B) | 20 |
| PhotoRec | TAR | the whole file, then padding (10); correct prefix, short (3, ext4 sparse file) | 13 |
| All four rows | SQLite (ext4 `contacts.sqlite`) | 4096 B of the file were holes on ext4; a raw read puts other bytes there | 3 each |
| Sanctum carve, Sanctum full | MP4 | the whole 4,132 B file, then **the rest of the image** (up to 263,958,528 B) | 20 |
| Sanctum carve, Sanctum full | TIFF | the whole 49,292 B file, then **the rest of the image** | 20 |
| Sanctum carve / full | PNG (exFAT `shot.png`, 28 runs) | first cluster right, then the next cluster on the medium | 5 / 9 |
| Sanctum carve, Sanctum full | PDF (flat corpus) | 180 B agree; bounded by a later object | 2 |

Strict byte-identity is the only success in the tables above. A reader who would accept
a whole file followed by padding would move Foremost's 74 archives, PhotoRec's 53
GZIP/TIFF/TAR files and Sanctum's 40 MP4/TIFF files into the success column — but
Sanctum's 40 carry **up to 252 MiB each** of bytes that are not the file.

## Where Sanctum wins, where it loses, where the three are equivalent

All comparisons in this section are between the three carve-only rows. Totals are
over the 25 benchmark volumes (5 undamaged + 20 damaged), 446 FULL files, unless a
corpus is named.

**Byte-identical recoveries, 25 volumes:**

| Row | Byte-identical / 446 FULL | Signature formats, 310 FULL | Formats without a Sanctum signature, 136 FULL | False positives | Wall time |
|---|---:|---:|---:|---:|---:|
| Sanctum, carve only | **263 (59.0%)** | 263 | 0 | 252 | 2,071.8 s |
| PhotoRec | **372 (83.4%)** | 269 | 103 | 0 | 2.9 s |
| Foremost | **220 (49.3%)** | 200 | 20 | 36 | 57.2 s |

### Where Sanctum loses

1. **Against PhotoRec, overall and on every damage model.** 263 against 372 over 25
   volumes; 207 against 288 over the 20 damaged volumes. Object by object over all 40
   images, PhotoRec returned 123 FULL files identical that Sanctum did not, and Sanctum
   36 that PhotoRec did not.
2. **Formats with no signature.** BMP, WebP, WAV, HTML, RTF, GZIP and TAR: Sanctum
   carve 0 of 136. PhotoRec returned 103 identical (all BMP, WebP, WAV, HTML and RTF;
   3 TAR; its GZIP, TIFF and most TAR outputs carry padding). Foremost returned BMP
   only (20). This is 120 of PhotoRec's 123 object-level wins.
3. **MP4, a format Sanctum has a signature and a parser for.** Sanctum 0 of 20; PhotoRec
   and Foremost 20 of 20. The parser reads the zero bytes after the file in its last
   cluster as an ISO-BMFF box of size 0 — "extends to end of file" — and the candidate
   runs to the end of the image. See finding B2 in `BENCHMARK_REPORT.md`.
4. **TIFF.** No tool returned a TIFF identical. PhotoRec's 20 are the file plus block
   padding; Sanctum's 20 are the file plus the rest of the image, because TIFF has a
   signature and no structure parser.
5. **False positives and output volume.** Sanctum's carve returned 252 outputs matching no
   planted file on the 25 volumes (235 of them fragments: headers inside a planted file,
   mostly ZIP members), PhotoRec 0, Foremost 36. Over all 40 images Sanctum wrote
   **48.0 GiB** of output; PhotoRec and Foremost wrote 0.03 GiB each. On one 255 MiB
   FAT32 volume Sanctum wrote 2,750 MiB in 25 files: nine ZIP member headers inside
   `archive.zip` and `budget.xlsx`, the MP4 and the TIFF were each written as the span
   to the end of the image. The HIGH+MEDIUM view (supplementary table) removes most of
   them but also 166 of 591 FULL files.
6. **Time.** 2,071.8 s against 2.9 s (PhotoRec) and 57.2 s (Foremost) on the same 25
   volumes. See [Time](#time).

### Where Sanctum wins

1. **A file in two runs.** `frag.jpg`, one JPEG split around a live 64 KiB pad on both
   FAT32 volumes, in every image where it is FULL: Sanctum carve 10 of 10 identical,
   scored MEDIUM as designed; PhotoRec 0 of 10 (not returned); Foremost 0 of 10 (head
   plus the pad's bytes). This is the only structural capability measured here that
   neither other tool has.
2. **Against Foremost, on containers.** ZIP 24, DOCX 20, XLSX 17 and SQLite 12
   identical against Foremost's 0 each: Foremost appends one byte to every ZIP-family
   file and returned no SQLite. Object by object Sanctum returned 112 files Foremost did
   not; Foremost 40 that Sanctum did not (20 BMP, 20 MP4).
3. **Truncated images.** 49 of 49 FULL files identical, against PhotoRec 46 (it missed
   `frag.jpg` twice and one NTFS resident `sticker.gif`) and Foremost 36. Truncation left
   mostly signature formats alive, because of the order files were written in; that is
   a property of this corpus, not a general result.
4. **Small files off a block boundary.** PhotoRec did not return small files that do not
   start where it expects a block: `sticker.gif` inside NTFS MFT records, and a JPEG and
   GIF in the second partition of the two-partition images.
5. **The shipped flat corpus.** 15 of 15 against PhotoRec 0 and Foremost 7. The objects
   sit at byte 1337 + k × 512 KiB, off every sector boundary, and PhotoRec only looks for
   headers at block starts. On the same corpus moved to byte 4096 PhotoRec also returns
   15 of 15. The shipped layout is unrepresentative of a filesystem, and Sanctum's
   calibration was measured on it.

### Where the three are equivalent

* **PDF**: 40 of 40 identical for all three.
* **JPEG and PNG laid down contiguously**: equal apart from the fragmented and off-grid
  cases above (PNG 47 of 51 each; the 4 misses are exFAT's 28-run `shot.png`).
* **SQLite against PhotoRec**: 12 of 15 each; the 3 misses are the ext4 sparse file no
  raw read can reproduce.
* **The filesystem corpus**: Sanctum 112, PhotoRec 108, Foremost 99 of 115 — within a
  handful of files of each other on 13 small images.
* **Nothing returned for GONE files**: 0 outputs for 64 GONE objects, for every tool.
  That is the check that the ground truth does not credit recoveries that cannot exist.

### Sanctum's full pipeline, against its own carve row only

Undelete adds files where filesystem metadata survives and nothing where it does not:

| Model | Carve only | Undelete + carve | Added by undelete |
|---|---:|---:|---:|
| Undamaged volumes (delete) | 56 | 92 | +36 |
| Truncation | 49 | 49 | 0 |
| Zeroed regions | 46 | 79 | +33 |
| **Metadata destroyed** | **55** | **55** | **0** |
| Interleaved overwrite | 57 | 93 | +36 |

On the model that the PS's "corrupted media" describes most closely — boot sector, FATs,
MFT and inode tables gone, data intact — Sanctum's product is exactly its carver, and
its carver is the row above that loses to PhotoRec.

## Time

Wall-clock seconds per run, minimum to maximum over each volume's undamaged image and
its four damaged copies:

| Volume | Sanctum carve | PhotoRec | Foremost | Sanctum full |
|---|---|---|---|---|
| FAT32 255 MiB | 1.47 – 117.09 | 0.03 – 0.21 | 0.06 – 3.12 | 1.47 – 118.24 |
| FAT32 511 MiB | 1.47 – 247.83 | 0.03 – 0.21 | 0.03 – 6.33 | 1.72 – 244.48 |
| exFAT 255 MiB | 1.27 – 117.52 | 0.03 – 0.12 | 0.06 – 3.12 | 1.52 – 119.73 |
| NTFS 64 MiB | 7.33 – 27.68 | 0.03 – 0.11 | 0.47 – 0.82 | 7.63 – 28.53 |
| ext4 64 MiB | 1.27 – 37.15 | 0.03 – 0.12 | 0.12 – 0.82 | 1.42 – 37.75 |

The minimum is always the truncated image (5–30 MiB kept). Sanctum's time scales with
the size of the candidates it builds, not with the image: under `cProfile`, 236.5 s of a
249.9 s carve of `media-fat32-255m.img` was `core/carve/score.py:measure_entropy`, a
pure-Python byte histogram run over the 262 MiB runaway candidates described in
finding B2. The signature scan itself took 2.4 s. PhotoRec's times are real: its log
shows the whole 521,032-sector image analysed. All images were in the page cache.

## What this means for "increase recovery rates from damaged storage media"

The phrase is comparative, so it needs a baseline. The baseline here is PhotoRec and
Foremost at their defaults on the same images, scored the same way.

**On these corpora and these four damage models, Sanctum does not increase the recovery
rate from damaged media over PhotoRec.** Over the 20 damaged volumes, Sanctum's
carver returned 207 of 343 surviving files byte-identical (60.3%), PhotoRec 288
(84.0%), Foremost 173 (50.4%). On each model separately PhotoRec is ahead: truncation is
the exception, 49 against 46.

**It does increase it over Foremost**, by 34 files over the 20 damaged volumes (+9.9
points), almost all ZIP-family and SQLite files that Foremost returns one byte long or
not at all.

**It recovers one class of file neither tool recovers**: a JPEG in exactly two runs on a
volume whose cluster size is known, 10 of 10 against 0 and 0. That is one file per
FAT32 volume here, and it is the whole of the measured advantage in reconstruction.

**Where metadata survives the damage, Sanctum's undelete adds 33–36 files per model**
over its own carver. That is a real increase in what Sanctum recovers from damaged
media, but it is not an increase over carving tools, and it is zero on the model where
metadata is destroyed.

**Not measured, and not claimed:** real damaged media; unreadable sectors met during
acquisition (every damaged image here was already an image); media larger than 511 MiB;
fragmentation beyond one two-run JPEG and one chained PNG; camera or phone files;
Scalpel or any commercial tool. The defects behind most of Sanctum's losses (no
signature for seven common formats, MP4 and TIFF candidates running to the end of the
image, finding B2) are identified and not fixed in this batch, so no figure here says
what a fixed build would score.

A sentence that the tables support: *"On synthetic FAT32, exFAT, NTFS and ext4 images
with modelled truncation, zeroed sectors, destroyed metadata and overwrite, Sanctum's
carver recovered 60% of surviving files byte-for-byte against PhotoRec's 84% and
Foremost's 50%; it was the only tool to reassemble a two-fragment JPEG."*

## Limits

Each of these bounds what the tables can be quoted for.

* **Synthetic content.** Every planted file comes from a real encoder, but images are
  noise, documents are a few lines, and no file came from a camera or a phone: no EXIF
  thumbnails, no MPF secondary images, no HEIC, no progressive JPEG. The camera-JPEG
  verdicts are in `CAMERA_JPEG_REPORT.md` and are not re-measured here.
* **Small populations.** 21 files per benchmark volume, 3 per small filesystem image. A
  difference of one or two files in a row is not a rate.
* **Image files, not media.** Every image is an ordinary file on this host's NVMe disk.
  No loop device, USB stick, card or disk was read, so no controller, no read error and
  no acquisition is in any figure. "Damaged" here means damage **modelled** in the image
  bytes: zero-filled bands placed deliberately, a truncation placed relative to the
  files, metadata zeroed rather than overwritten with garbage, and an overwrite written
  without the filesystem record that would accompany it. Sanctum's retry-and-fill
  acquisition path for unreadable sectors (`core/carve/acquire.py`) is not exercised.
* **One machine, one run.** Every figure is a single run. Earlier batches measured
  run-to-run noise of about ±0.4 s per image for Sanctum.
* **Fragmentation is thin.** One two-run JPEG on each FAT32 volume and one 28-run PNG on
  exFAT. NTFS and ext4 allocation was not controlled.
* **Builder artefacts.** `debugfs write` stores zero blocks as holes, so two ext4 files
  are sparse; NTFS resident files carry fixup bytes. Both are recorded in the manifests
  and neither is how an application usually writes a file.
* **Strict scoring.** Byte-identical is the only success. A tool that returns a usable
  file with extra trailing bytes scores *corrupt*. An output that starts inside a
  planted file is a *fragment* false positive, so a recovery of a PARTIAL file's
  surviving tail is not credited as a recovery of that file.
* **Tool coverage.** PhotoRec 7.2 in `/cmd` mode and Foremost 1.5.7 only, each at
  defaults. Scalpel, PhotoRec's paranoid-off and brute-force options, and Foremost's
  quick mode were not run. Foremost was run from an unpacked Fedora RPM, not an
  installed package.
* **Not measured:** volumes larger than 511 MiB, real damaged media, multi-process
  carving, and memory use.

## Reproduce

```
dnf download foremost && rpm2cpio foremost-*.rpm | cpio -idm   # if Foremost is not installed
python -m testkit.benchmark build  --work /var/tmp/sanctum-bench/work
python -m testkit.benchmark run    --work /var/tmp/sanctum-bench/work --foremost ./usr/bin/foremost
python -m testkit.benchmark score  --work /var/tmp/sanctum-bench/work --csv docs/performance/benchmark.csv
python -m testkit.benchmark report --work /var/tmp/sanctum-bench/work --out tables.md
```

The build takes about 30 seconds and needs no root. The run took 74 minutes (19:38:46 to 20:52:47), almost all of it Sanctum on this host.
