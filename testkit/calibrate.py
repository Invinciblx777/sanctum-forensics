"""Measure what the confidence buckets actually mean, and write the evidence down.

An uncalibrated score is a number somebody made up. This harness turns it into
a measurement: build a corpus whose contents are known byte for byte, run the
whole carving pipeline over it, and count how often each bucket was right.

A candidate is a **true positive** when its SHA-256 matches a planted object
the manifest marks recoverable. Nothing softer counts - not "starts at the
right offset", not "is the right type" - because a recovered file that differs
from the original by one byte is a file that does not open.

Three tables come out, and each answers a different question an examiner asks:

* **by bucket** - does HIGH mean what it claims? Precision here is what the
  word "high confidence" is worth in a report.
* **by format** - which decoders are carrying the score, and which formats are
  being scored on structure alone.
* **by source** - whether filesystem metadata, structure parsing or a raw
  signature match found the file.

Results land in ``docs/performance/calibration.csv`` with a matplotlib chart
beside them. The reasoning, and every weight that moved because of a run, is
recorded in ``docs/performance/calibration.md``.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from core.carve.classify import classify_candidate, dedupe
from core.carve.evidence import open_evidence
from core.carve.score import WEIGHTS, ScoreWeights, resolve_overlaps, score_candidate
from core.carve.structure import carve_structures
from core.carve.validate import validate_candidate
from core.models import CarveCandidate

from testkit.generate_corpus import CorpusManifest, generate_corpus, load_manifest

__all__ = [
    "BucketRow",
    "CalibrationResult",
    "CSV_COLUMNS",
    "run_pipeline",
    "measure",
    "write_csv",
    "write_chart",
    "calibrate",
    "main",
]

#: Where a run writes by default. Committed, so a reader of the report can
#: check the numbers rather than take them on trust.
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "performance"

CSV_COLUMNS = (
    "dimension",
    "key",
    "count",
    "true_positives",
    "precision_bp",
    "recall_bp",
)

BUCKETS = ("HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True)
class BucketRow:
    """One measured row: a slice of the candidates, and how it performed."""

    dimension: str
    key: str
    count: int
    true_positives: int
    #: Share of candidates in this slice that matched a planted object, in
    #: basis points. Integer for the same reason every other rate in this
    #: codebase is one: it ends up in a report a third party reads.
    precision_bp: int
    #: Share of the corpus's recoverable objects this slice recovered, in
    #: basis points.
    recall_bp: int

    def as_row(self) -> dict[str, str | int]:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "count": self.count,
            "true_positives": self.true_positives,
            "precision_bp": self.precision_bp,
            "recall_bp": self.recall_bp,
        }


@dataclass(frozen=True)
class CalibrationResult:
    """Everything one calibration run produced."""

    weights: ScoreWeights
    manifest: CorpusManifest
    candidates: list[CarveCandidate]
    rows: list[BucketRow]
    csv_path: Path
    chart_path: Path | None

    def bucket(self, name: str) -> BucketRow:
        """The row for one confidence bucket."""
        for row in self.rows:
            if row.dimension == "bucket" and row.key == name:
                return row
        raise KeyError(name)


def run_pipeline(
    corpus_dir: Path, manifest: CorpusManifest, *, weights: ScoreWeights = WEIGHTS
) -> list[CarveCandidate]:
    """Carve, validate, classify, score, resolve overlaps, dedupe.

    The same order the product runs in. Scoring comes after validation because
    the decoder verdict is a score component, and overlap resolution comes
    after scoring because the winner of an overlap is the higher score.
    """
    image_path = Path(corpus_dir) / manifest.image
    with open_evidence(image_path) as image:
        carved = list(carve_structures(image))
        judged = [
            score_candidate(
                classify_candidate(
                    validate_candidate(candidate, image), image=image
                ),
                image=image,
                weights=weights,
            )
            for candidate in carved
        ]
    return dedupe(resolve_overlaps(judged, weights=weights))


def _rate_bp(part: int, whole: int) -> int:
    return int(round(part * 10_000 / whole)) if whole else 0


def _row(
    dimension: str,
    key: str,
    slice_: Sequence[CarveCandidate],
    truth: set[str],
    total_recoverable: int,
) -> BucketRow:
    hits = [item for item in slice_ if item.sha256 in truth]
    found = {item.sha256 for item in hits}
    return BucketRow(
        dimension=dimension,
        key=key,
        count=len(slice_),
        true_positives=len(hits),
        precision_bp=_rate_bp(len(hits), len(slice_)),
        recall_bp=_rate_bp(len(found), total_recoverable),
    )


def measure(
    candidates: Sequence[CarveCandidate], manifest: CorpusManifest
) -> list[BucketRow]:
    """Score the run against the manifest, by bucket, by format and by source."""
    truth = manifest.recoverable_digests
    total = len(truth)

    rows = [
        _row(
            "bucket",
            bucket,
            [item for item in candidates if item.bucket == bucket],
            truth,
            total,
        )
        for bucket in BUCKETS
    ]
    rows.append(_row("bucket", "ALL", list(candidates), truth, total))

    for ext in sorted({item.ext for item in candidates}):
        rows.append(
            _row(
                "format",
                ext,
                [item for item in candidates if item.ext == ext],
                truth,
                total,
            )
        )
    for source in sorted({item.source for item in candidates}):
        rows.append(
            _row(
                "source",
                source,
                [item for item in candidates if item.source == source],
                truth,
                total,
            )
        )
    for verdict in sorted({item.validation for item in candidates}):
        rows.append(
            _row(
                "validation",
                verdict,
                [item for item in candidates if item.validation == verdict],
                truth,
                total,
            )
        )
    return rows


def write_csv(rows: Iterable[BucketRow], path: Path) -> Path:
    """Write the measured table. One file, every dimension, no summarising."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return path


def write_chart(rows: Sequence[BucketRow], path: Path) -> Path | None:
    """Plot precision and recall per bucket. Returns None if matplotlib is absent."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    buckets = [row for row in rows if row.dimension == "bucket" and row.key != "ALL"]
    labels = [f"{row.key}\n(n={row.count})" for row in buckets]
    precision = [row.precision_bp / 100 for row in buckets]
    recall = [row.recall_bp / 100 for row in buckets]
    positions = range(len(buckets))
    width = 0.38

    figure, axes = plt.subplots(figsize=(7.5, 4.5))
    axes.bar(
        [pos - width / 2 for pos in positions],
        precision,
        width,
        label="precision %",
        color="#2f6f4f",
    )
    axes.bar(
        [pos + width / 2 for pos in positions],
        recall,
        width,
        label="recall %",
        color="#7a4f8f",
    )
    axes.axhline(95, linestyle="--", linewidth=1, color="#444444")
    axes.text(len(buckets) - 0.5, 95.6, "HIGH target 95%", fontsize=8, ha="right")
    axes.axhline(70, linestyle=":", linewidth=1, color="#444444")
    axes.text(len(buckets) - 0.5, 70.6, "MEDIUM target 70%", fontsize=8, ha="right")
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_ylim(0, 105)
    axes.set_ylabel("percent")
    axes.set_title("Carve confidence buckets: measured precision and recall")
    axes.legend(loc="lower left")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=140)
    plt.close(figure)
    return path


def calibrate(
    corpus_dir: Path,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    seed: int = 0,
    regenerate: bool = True,
    chart: bool = True,
    weights: ScoreWeights = WEIGHTS,
) -> CalibrationResult:
    """Build the corpus, run the pipeline over it, and write the measurement."""
    corpus_dir = Path(corpus_dir)
    manifest = (
        generate_corpus(corpus_dir, seed=seed)
        if regenerate
        else load_manifest(corpus_dir)
    )
    candidates = run_pipeline(corpus_dir, manifest, weights=weights)
    rows = measure(candidates, manifest)
    csv_path = write_csv(rows, Path(out_dir) / "calibration.csv")
    chart_path = write_chart(rows, Path(out_dir) / "calibration.png") if chart else None
    return CalibrationResult(
        weights=weights,
        manifest=manifest,
        candidates=candidates,
        rows=rows,
        csv_path=csv_path,
        chart_path=chart_path,
    )


def format_table(rows: Sequence[BucketRow]) -> str:
    """Render the measured rows as fixed-width text, for a commit message."""
    header = (
        f"{'dimension':<11}{'key':<22}{'n':>5}{'TP':>5}"
        f"{'precision':>11}{'recall':>9}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.dimension:<11}{row.key:<22}{row.count:>5}{row.true_positives:>5}"
            f"{row.precision_bp / 100:>10.1f}%{row.recall_bp / 100:>8.1f}%"
        )
    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Calibrate carve confidence buckets against a known corpus."
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        required=True,
        help="directory the corpus is written to and carved from",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--reuse-corpus",
        action="store_true",
        help="carve an existing corpus instead of regenerating it",
    )
    parser.add_argument("--no-chart", action="store_true")
    args = parser.parse_args()

    result = calibrate(
        args.corpus_dir,
        args.out_dir,
        seed=args.seed,
        regenerate=not args.reuse_corpus,
        chart=not args.no_chart,
    )
    print(format_table(result.rows))  # noqa: T201 - this is a CLI, not a core layer
    print(f"\nwrote {result.csv_path}")  # noqa: T201
    if result.chart_path is not None:
        print(f"wrote {result.chart_path}")  # noqa: T201


if __name__ == "__main__":
    main()
