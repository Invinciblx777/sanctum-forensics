# Carve confidence calibration

**Run date:** 2026-09-04 · **Corpus seed:** 0 · **Harness:** `testkit/calibrate.py`
· **Table:** [`calibration.csv`](calibration.csv) · **Chart:** [`calibration.png`](calibration.png)

An uncalibrated score is a number somebody made up. This document records the
measurement that turns `core/carve/score.py` into something an examiner can
check, the weights that moved because of it, and why each one moved.

Reproduce it with:

```
python -m testkit.calibrate --corpus-dir <scratch dir> --seed 0
```

## Method

`testkit/generate_corpus.py` plants 24 objects at known offsets in a 12.5 MB
image and records a manifest: SHA-256, offset, length, format, and what kind of
object it is. Fifteen distinct byte sequences are marked recoverable; the rest
are there to be scored against rather than found.

| kind | count | what it is |
|---|---|---|
| `intact` | 16 | a whole file, produced by a real encoder |
| `duplicate` | 2 | the same PNG planted at two further offsets |
| `truncated` | 3 | a real file with its tail removed |
| `decoy` | 3 | a valid header on bytes of another kind entirely |

The full pipeline then runs over the image — carve, validate, classify, score,
resolve overlaps, dedupe — exactly as the product runs it. **A candidate is a
true positive when its SHA-256 matches a recoverable manifest entry.** Nothing
softer counts: not "starts at the right offset", not "is the right type". A
recovered file that differs from the original by one byte does not open.

Precision is true positives over candidates in the slice. Recall is distinct
manifest files recovered by the slice, over the 15 that exist.

## Measured result, calibrated weights

| bucket | n | TP | precision | recall |
|---|---:|---:|---:|---:|
| HIGH | 13 | 13 | 100.0% | 86.7% |
| MEDIUM | 2 | 2 | 100.0% | 13.3% |
| LOW | 18 | 0 | 0.0% | 0.0% |
| **ALL** | 33 | 15 | 45.5% | **100.0%** |

By source and by decoder verdict:

| dimension | key | n | TP | precision | recall |
|---|---|---:|---:|---:|---:|
| source | structure | 30 | 14 | 46.7% | 93.3% |
| source | signature | 3 | 1 | 33.3% | 6.7% |
| validation | valid | 13 | 13 | 100.0% | 86.7% |
| validation | decoder_unavailable | 2 | 2 | 100.0% | 13.3% |
| validation | truncated | 1 | 0 | 0.0% | 0.0% |
| validation | corrupt | 17 | 0 | 0.0% | 0.0% |

Per-format rows are in the CSV. The one that matters for reading the rest:
`zip` shows 13 candidates for 2 true positives, because a signature scan hits
the `PK\x03\x04` at the head of *every member* of an archive as well as the
archive itself. Those inner hits are resolved as overlaps, kept, and marked —
they are what fills the LOW bucket, and every one of them is genuinely not a
file.

## Weights: before and after

| component | original | calibrated | moved |
|---|---:|---:|---|
| `header` | 2000 | 2000 | — |
| `exact_length` | 1500 | 1500 | — |
| `decoder_valid` | 3500 | **4000** | yes |
| `decoder_truncated` | 1500 | **1000** | yes |
| `decoder_unavailable` | 0 | **1000** | yes |
| `entropy` | 1000 | 1000 | — |
| `fs_metadata` | 1500 | 1500 | — |
| `no_overlap` | 500 | 500 | — |

The same corpus, same pipeline, original weights:

| bucket | n | TP | precision | recall |
|---|---:|---:|---:|---:|
| HIGH | 9 | 9 | 100.0% | 60.0% |
| MEDIUM | 5 | 4 | 80.0% | 26.7% |
| LOW | 19 | 2 | 10.5% | 13.3% |

### Why each weight moved

**`decoder_valid` 3500 → 4000.** Under the original weights, a candidate whose
header matched, whose length a parser derived, and which a real decoder read
end to end scored 2000 + 1500 + 3500 + 500 = 7500 — MEDIUM — unless its entropy
profile also matched. Every one of the 13 candidates meeting that description
was a true positive (100.0% precision on the `valid` row), and the missing 500
left four of them out of HIGH. At 4000, header + derived length + clean decode
+ no overlap is exactly 8000, the HIGH floor. HIGH recall rose from 60.0% to
86.7% with precision unchanged at 100.0%.

**`decoder_truncated` 1500 → 1000.** Truncated candidates scored zero true
positives, and at 1500 one of them reached MEDIUM (2000 + 1500 + 1000 + 500 =
5000), which is what cost MEDIUM its 80.0% precision. This is `n=1`, so it is a
small correction rather than a strong claim; the direction is not in doubt,
because a file the decoder could not finish reading is by definition not the
file that was there.

**`decoder_unavailable` 0 → 1000.** Scoring a missing verdict as zero put two
true positives — an MP4 whose box tree walks cleanly with no `ffprobe` on PATH,
and a password-protected ZIP — in LOW, alongside genuine false positives. That
is the opposite of honest: "no decoder ran" is not evidence the bytes are bad.
At 1000 they land in MEDIUM, and no decoder-unavailable candidate can reach
HIGH by construction: 2000 + 1500 + 1000 + 1000 + 1500 + 500 is 7500 even with
every other component awarded. LOW precision fell to 0.0%, which is the goal —
nothing recoverable is left in the bucket the report tells an examiner to skip.

### Two entropy thresholds also moved

Both are measurements, not preferences:

* `MIXED_ENTROPY_FLOOR_MILLIBITS` 3000 → 2000. A real SQLite database of text
  rows measures 2.96 bits/byte. The old floor rejected it for being what it is.
* `sqlite` and `evtx` moved from the `mixed` profile to `low`. A database page
  is text and padding; an event log is repeated record templates. Both sit near
  3 bits/byte, and calling them "no expectation" threw away a usable signal.

## Two parser bugs this run found

Calibration is worth running for the weights. It paid for itself on these:

* **`parse_zip` took the last EOCD record inside `max_size`.** A ZIP's cap is a
  gigabyte, so in an image holding several archives the first archive was given
  an end address belonging to another one megabytes away — a 6.8 MB "recovered"
  file of 582 bytes of content. Fixed by requiring the record's central
  directory to start at `PK\x01\x02` and to end exactly where the record
  begins, and by taking the first such record.
* **`parse_pdf` took the last `%%EOF` without the corroboration its docstring
  claimed.** Two identical PDFs in the image merged into one 5 MB candidate
  that pikepdf then validated as `valid` — a false positive scored HIGH, the
  single worst outcome this scoring exists to prevent. Fixed by following
  `startxref` from each marker and by requiring incremental revisions to be
  contiguous.

Both were shipped code that every existing test passed. Ground truth is what
found them.

## Honest limits of this measurement

* The corpus is synthetic and small: 33 candidates, 15 recoverable files. MEDIUM
  carries two candidates, so its 100.0% precision is a weak claim and is quoted
  here as "above the 70% target", not as a rate.
* No fragmented file and no filesystem-metadata source is exercised, so the
  `fs_metadata` weight is **not** calibrated. It keeps its original value, and
  will stay uncalibrated until `core/carve/fsaware.py` lands and the corpus
  grows real filesystem images.
* MP4 is scored on a structural walk alone on any machine without `ffprobe`.
  The number that machine produces is a different number, and the candidate
  says so in its `validation_detail`.
* Real media is messier than any generator: fragmentation, partial overwrites
  and slack are what a live case looks like. These figures describe this
  corpus, on this build, on this date.
