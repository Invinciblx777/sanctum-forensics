"""A bifragmented JPEG comes back out of ``POST /jobs/carve``, or it does not.

The mechanism, the oracle and the search bounds are proven at their own level in
``tests/carve/signature/test_fragmentation_reachability.py``. This file proves
the only thing that matters to a judge: that driving the request through the
real route and the real job registry recovers the object, that its score
reconciles like every other candidate's, and that the three ways this can go
wrong do not.

Fragment reassembly is the one thing this tool does that a signature carver
cannot, so the claim needs an end-to-end witness rather than a unit test of the
function behind it. It also needs the negative witnesses, because the failure
mode of a reassembler is not "recovers nothing" - it is "recovers something
plausible that was never a file":

* a split the medium's cluster size cannot produce must not be guessed at;
* a tail that is genuinely gone must not be replaced with whatever unrelated
  bytes happen to precede an ``FFD9``;
* no candidate, reassembled or not, may carry a digest for bytes that cannot be
  read back off the medium at the offsets it names.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import pytest
from api.deps import AppServices
from fastapi.testclient import TestClient

from tests.carve.signature.conftest import Embedded, lay_out, make_noisy_jpeg

CLUSTER = 4096
GAP_BYTES = 32 * 1024

#: Split points a 4 KiB-cluster volume can actually produce.
ALIGNED_SPLITS = [4096, 8192]

#: Split points it cannot. Batch 2 measured all four; these two are outside what
#: the search enumerates and must therefore be *reported*, never *guessed*.
UNALIGNED_SPLITS = [1024, 2048]

EXPECTED_COMPONENTS = {
    "header",
    "exact_length",
    "decoder",
    "entropy",
    "fs_metadata",
    "no_overlap",
}


def _split_across_gap(payload: bytes, *, head_bytes: int, gap: int) -> bytes:
    filler = bytes((index * 7 + 3) & 0xFF for index in range(gap))
    return payload[:head_bytes] + filler + payload[head_bytes:]


def _fragmented_image(payload: bytes, head_bytes: int) -> bytes:
    laid = _split_across_gap(payload, head_bytes=head_bytes, gap=GAP_BYTES)
    return lay_out([Embedded(ext="jpg", offset=0, data=laid)], len(laid) + 8192)


def _carve(client: TestClient, image: Path) -> dict[str, Any]:
    """Submit one carve and drain it, through the route and the registry."""
    accepted = client.post(
        "/jobs/carve",
        json={"image": str(image), "undelete": True, "carve_signatures": True},
    )
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["job_id"]

    status: dict[str, Any] = {}
    for _ in range(600):
        status = client.get(f"/jobs/{job_id}").json()
        if status["state"] in {"complete", "failed"}:
            break
        time.sleep(0.05)

    assert status.get("state") == "complete", status.get("error")
    result: dict[str, Any] = status["result"]
    return result


@pytest.fixture(scope="module")
def original() -> bytes:
    return make_noisy_jpeg(seed=99)


# --------------------------------------------------------------------------
# It fires, and it recovers the object
# --------------------------------------------------------------------------


@pytest.mark.parametrize("head_bytes", ALIGNED_SPLITS)
def test_the_pipeline_recovers_a_bifragmented_jpeg(
    client: TestClient, tmp_path: Path, original: bytes, head_bytes: int
) -> None:
    image = tmp_path / f"frag-{head_bytes}.dd"
    image.write_bytes(_fragmented_image(original, head_bytes))

    result = _carve(client, image)

    want = hashlib.sha256(original).hexdigest()
    recovered = [item for item in result["candidates"] if item["sha256"] == want]
    assert recovered, (
        "the bifragmented JPEG was not reassembled; candidates were "
        f"{[(c['offset'], c['length'], c['bucket']) for c in result['candidates']]}"
    )

    item = recovered[0]
    assert item["length"] == len(original)
    assert item["source"] == "structure"
    assert item["validation"] == "valid"
    assert item["possibly_fragmented"] is True
    assert item["score_components"]["decoder"] > 0
    assert [(run["offset"], run["length"]) for run in item["fragments"]] == [
        (0, head_bytes),
        (head_bytes + GAP_BYTES, len(original) - head_bytes),
    ]


@pytest.mark.parametrize("head_bytes", ALIGNED_SPLITS)
def test_a_reassembled_candidate_reconciles_like_any_other(
    client: TestClient, tmp_path: Path, original: bytes, head_bytes: int
) -> None:
    """Every Batch 2 scoring invariant, applied to the reassembled object.

    It lands in HIGH, and that is defensible rather than generous: all six
    components were measured on the reassembled bytes themselves - the header
    is at the start of them, the decoder consumed all of them, the entropy was
    taken over all of them - and ``fragments`` says where every one of those
    bytes was, so the claim is checkable against the medium.
    """
    from core.carve.score import bucket_for

    image = tmp_path / f"score-{head_bytes}.dd"
    image.write_bytes(_fragmented_image(original, head_bytes))

    want = hashlib.sha256(original).hexdigest()
    recovered = [
        item for item in _carve(client, image)["candidates"] if item["sha256"] == want
    ]
    assert recovered

    item = recovered[0]
    components = item["score_components"]
    assert set(components) == EXPECTED_COMPONENTS
    assert sum(components.values()) == item["confidence_bp"]
    assert item["bucket"] == bucket_for(item["confidence_bp"])
    assert item["entropy_millibits_per_byte"] is not None
    assert item["confidence_bp"] not in {7500, 9500, 4000}, (
        "a structure.py placeholder survived scoring"
    )
    assert item["bucket"] == "HIGH", (
        f"reassembled object landed in {item['bucket']} at "
        f"{item['confidence_bp']} bp with {components}; if the weights moved, "
        "the report's bucket claim needs updating with them"
    )


# --------------------------------------------------------------------------
# The three ways this goes wrong
# --------------------------------------------------------------------------


@pytest.mark.parametrize("head_bytes", UNALIGNED_SPLITS)
def test_an_unaligned_split_is_reported_honestly_not_guessed_at(
    client: TestClient, tmp_path: Path, original: bytes, head_bytes: int
) -> None:
    """Fragments are sought on cluster boundaries. These are not on one.

    The requirement is not that they recover. It is that failing to recover
    them yields a candidate over a span that really is on the medium, with a
    digest of exactly those bytes, below HIGH.
    """
    blob = _fragmented_image(original, head_bytes)
    image = tmp_path / f"unaligned-{head_bytes}.dd"
    image.write_bytes(blob)

    want = hashlib.sha256(original).hexdigest()
    result = _carve(client, image)

    assert not [item for item in result["candidates"] if item["sha256"] == want]
    for item in result["candidates"]:
        assert item["bucket"] != "HIGH"
        assert not item["fragments"]
        span = blob[item["offset"] : item["offset"] + item["length"]]
        assert hashlib.sha256(span).hexdigest() == item["sha256"]


def test_a_missing_tail_fails_cleanly_and_fabricates_nothing(
    client: TestClient, tmp_path: Path, original: bytes
) -> None:
    """The head is on the medium and the rest of the file is not.

    Reassembly must fail. It must not join the head to whatever unrelated bytes
    happen to precede an ``FFD9`` - which is what a decode-only oracle did on
    this exact input: a 4842-byte "JPEG" at HIGH, digest of bytes that were
    never a file.
    """
    blob = lay_out([Embedded(ext="jpg", offset=0, data=original[:8192])], 262144)
    image = tmp_path / "no-tail.dd"
    image.write_bytes(blob)

    want = hashlib.sha256(original).hexdigest()
    result = _carve(client, image)

    assert result["candidates"], "the header should still be reported"
    for item in result["candidates"]:
        assert item["sha256"] != want
        assert not item["fragments"], "nothing was reassembled, so nothing has runs"
        assert item["bucket"] != "HIGH"
        assert item["score_components"]["exact_length"] == 0, (
            "a length nothing corroborated must not be paid for"
        )
        span = blob[item["offset"] : item["offset"] + item["length"]]
        assert hashlib.sha256(span).hexdigest() == item["sha256"], (
            "the candidate claims a digest for bytes that are not on the medium"
        )


def test_a_written_object_is_the_reassembled_bytes_not_the_span(
    client: TestClient, services: AppServices, tmp_path: Path, original: bytes
) -> None:
    """``out_dir`` must receive the file, not head + gap + tail truncated.

    ``write_recovered`` re-reads ``offset``..``offset + length``, which for a
    reassembled candidate is the right length of the wrong bytes. The pipeline
    routes these through the payload map instead; this is what proves it does.

    ``out_dir`` is relative, and therefore inside the deployment's recovered
    directory. An absolute path outside it is refused before the job starts;
    see ``tests/api/test_output_path_confinement.py``. What this test asserts
    about the *bytes* is unchanged - only where they are written moved.
    """
    image = tmp_path / "written.dd"
    image.write_bytes(_fragmented_image(original, CLUSTER))
    out_dir = services.recovered_dir / "reassembled"

    accepted = client.post(
        "/jobs/carve",
        json={
            "image": str(image),
            "undelete": True,
            "carve_signatures": True,
            "out_dir": "reassembled",
        },
    )
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["job_id"]
    status: dict[str, Any] = {}
    for _ in range(600):
        status = client.get(f"/jobs/{job_id}").json()
        if status["state"] in {"complete", "failed"}:
            break
        time.sleep(0.05)
    assert status.get("state") == "complete", status.get("error")

    want = hashlib.sha256(original).hexdigest()
    on_disk = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in out_dir.iterdir()
        if path.is_file()
    }
    assert want in on_disk, (
        "the reassembled object was not written; what landed in out_dir was "
        f"{sorted(on_disk)}"
    )
