"""The carve pipeline as a Progress generator, for the job registry.

:mod:`core.carve` exposes its stages as ordinary functions, which is right for
a library and wrong for a progress bar. This adapts them into one generator
that yields :class:`~core.models.Progress` at each stage boundary and returns
the finished candidate list, without any stage learning that a UI exists.

The order is the product's order and it is not arbitrary. Undelete runs first
because it produces the allocated/unallocated map, and the signature carver is
then pointed at the unallocated regions only - the parts of the image no live
file claims, which is where content with no surviving metadata actually is.
Running the carver over the whole image instead would re-find every live file
and bury the deleted ones in them.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import structlog
from core.models import CarveCandidate, Progress

__all__ = ["carve_generator", "CARVE_PHASES"]

logger = structlog.get_logger(__name__)

#: Stage names, in execution order. The UI renders these as a stepper.
CARVE_PHASES = ("open", "undelete", "signatures", "validate", "score", "write")


def _progress(
    job_id: str, phase: str, pct_bp: int, message: str, *, done: int = 0, total: int = 0
) -> Progress:
    return Progress(
        job_id=job_id,
        phase=phase,
        pct_bp=pct_bp,
        bytes_done=done,
        bytes_total=total,
        throughput_bytes_per_sec=0,
        eta_seconds=0,
        message=message,
    )


def carve_generator(
    image: Path,
    *,
    undelete: bool = True,
    carve_signatures: bool = True,
    out_dir: Path | None = None,
    job_id: str = "carve",
) -> Generator[Progress, None, dict[str, Any]]:
    """Run the recovery pipeline over ``image``, yielding progress per stage."""
    from core.carve.classify import (
        classify_candidate,
        dedupe,
        output_filename,
        write_recovered,
    )
    from core.carve.evidence import open_evidence
    from core.carve.score import resolve_overlaps, score_candidate
    from core.carve.signature import carve_signatures as scan_signatures
    from core.carve.validate import validate_candidate

    candidates: list[CarveCandidate] = []
    limitations: list[str] = []
    partitions: list[dict[str, Any]] = []
    unallocated_bytes = 0
    payloads: dict[int, bytes] = {}

    with open_evidence(image) as handle:
        yield _progress(
            job_id, "open", 0, f"opened {image.name} ({handle.size} bytes)",
            total=handle.size,
        )

        if undelete:
            from core.carve.fsaware import read_recovered, undelete_report

            report = undelete_report(handle)
            limitations.extend(report.limitations)
            partitions = [
                {
                    "index": item.index,
                    "offset": item.offset,
                    "length": item.length,
                    "description": item.description,
                    "fs_type": item.fs_type,
                }
                for item in report.partitions
            ]
            unallocated_bytes = sum(item.length for item in report.unallocated)
            for item in report.files:
                payload = read_recovered(handle, item)
                payloads[item.candidate.offset] = payload
                candidates.append(item.candidate)
            yield _progress(
                job_id,
                "undelete",
                3000,
                f"{len(report.files)} entries from filesystem metadata; "
                f"{unallocated_bytes} bytes unallocated",
                total=handle.size,
            )

        if carve_signatures:
            scan = scan_signatures(handle)
            candidates.extend(scan.candidates)
            yield _progress(
                job_id,
                "signatures",
                6000,
                f"{len(scan.candidates)} signature candidates over "
                f"{scan.bytes_scanned} bytes",
                done=scan.bytes_scanned,
                total=handle.size,
            )

        judged: list[CarveCandidate] = []
        for index, candidate in enumerate(candidates):
            recovered_bytes = payloads.get(candidate.offset)
            # fs_metadata candidates may span several extents, so their bytes
            # cannot be re-read from offset+length. They travel with the
            # candidate instead; see core.carve.fsaware.read_recovered.
            if candidate.source == "fs_metadata" and recovered_bytes is not None:
                scored = validate_candidate(candidate, data=recovered_bytes)
                scored = classify_candidate(scored, data=recovered_bytes)
                scored = score_candidate(scored, data=recovered_bytes)
            else:
                scored = validate_candidate(candidate, handle)
                scored = classify_candidate(scored, image=handle)
                scored = score_candidate(scored, image=handle)
            judged.append(scored)
            if index % 25 == 0:
                yield _progress(
                    job_id,
                    "validate",
                    6000 + 2000 * index // max(len(candidates), 1),
                    f"validated {index}/{len(candidates)}",
                    total=handle.size,
                )

        resolved = dedupe(resolve_overlaps(judged))
        yield _progress(
            job_id, "score", 9000, f"{len(resolved)} candidates after dedupe",
            total=handle.size,
        )

        written: list[str] = []
        if out_dir is not None:
            # Candidates whose bytes travelled with them are written here:
            # write_recovered re-reads offset..offset+length, which is the
            # wrong bytes for a file the filesystem stored in several extents.
            reassembled = [item for item in resolved if item.offset in payloads]
            contiguous = [item for item in resolved if item.offset not in payloads]

            out_dir.mkdir(parents=True, exist_ok=True)
            for candidate in reassembled:
                name = output_filename(candidate)
                try:
                    destination = out_dir / name
                    destination.write_bytes(payloads[candidate.offset])
                    written.append(str(destination))
                except OSError as exc:
                    limitations.append(
                        f"Recovered object {name} could not be written: {exc}"
                    )
            try:
                for record in write_recovered(contiguous, handle, out_dir):
                    written.append(str(record["path"]))
            except (OSError, ValueError) as exc:
                limitations.append(f"Recovered objects could not be written: {exc}")

            yield _progress(
                job_id, "write", 10_000, f"wrote {len(written)} objects",
                total=handle.size,
            )

    logger.info(
        "carve_complete",
        image=str(image),
        candidates=len(resolved),
        written=len(written),
    )
    return {
        "image": str(image),
        "candidates": [item.model_dump(mode="json") for item in resolved],
        "partitions": partitions,
        "unallocated_bytes": unallocated_bytes,
        "written": written,
        "limitations": limitations,
    }
