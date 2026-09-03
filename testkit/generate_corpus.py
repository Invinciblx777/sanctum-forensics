"""Build a synthetic disk-image corpus for evaluating the carving pipeline.

Produces images with known ground truth (planted files, deletions, fragmentation,
slack) so :mod:`testkit.evaluate` can score recall/precision. Never touches a
real device. Deferred to M3 evaluation work.
"""

from __future__ import annotations

import argparse
from pathlib import Path

__all__ = ["generate_corpus", "main"]


def generate_corpus(out_dir: Path, *, seed: int = 0) -> None:
    """Write synthetic images plus a ``ground_truth.json`` into ``out_dir``."""
    raise NotImplementedError


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate a synthetic carving corpus.")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    generate_corpus(args.out_dir, seed=args.seed)


if __name__ == "__main__":
    main()
