"""Routes, cache behaviour and the payload contract.

Runs against the null fold backend, so nothing here touches a GPU or a network:
the examples committed to the repository are enough to exercise every path that
matters except the fold itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphabetti.cache import Cache, sequence_id            # noqa: E402
from app import create_app                                  # noqa: E402
from config import get_config                               # noqa: E402

UBIQUITIN = ("MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQ"
             "KESTLHLVLRLRGG")


@pytest.fixture
def client(tmp_path):
    settings = get_config("development")
    settings.CACHE_PATH = tmp_path / "test.sqlite"
    settings.FOLD_BACKEND = "none"          # never fold in tests
    application = create_app(settings)
    application.config["TESTING"] = True
    return application.test_client()


def test_index_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"ALPHABETTI" in response.data
    # The claim the whole app rests on must be on the page.
    assert b"no alignment used" in response.data


def test_about_renders_with_model_versions(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert b"esmfold_v1" in response.data
    assert b"esm2_t33_650M_UR50D" in response.data
    assert b"Tien" in response.data          # the RSA citation is required


def test_healthz_reports_state(client):
    body = json.loads(client.get("/healthz").data)
    assert body["ok"] is True
    assert "queue_depth" in body
    assert body["fold"]["backend"] == "none"


def test_examples_are_prewarmed(client):
    """The landing state must never be empty, so these must be in the cache."""
    body = json.loads(client.get("/healthz").data)
    assert "ubiquitin" in body["examples_warm"]
    for name in ("ubiquitin", "lysozyme", "myoglobin", "tim"):
        response = client.get(f"/api/example/{name}")
        assert response.status_code == 200, name
        assert len(json.loads(response.data)["residues"]) > 10


def test_unknown_example_is_a_clean_404(client):
    response = client.get("/api/example/nonesuch")
    assert response.status_code == 404
    assert "error" in json.loads(response.data)


def test_cached_sequence_returns_immediately_without_folding(client):
    """Acceptance criterion: a repeat submission comes back from cache."""
    response = client.post("/api/submit", json={"input": UBIQUITIN})
    assert response.status_code == 200
    body = json.loads(response.data)
    assert body["cached"] is True
    assert body["length"] == 76
    assert body["stats"]["cached"] is True


def test_same_sequence_by_any_route_is_one_cache_entry(client):
    """Raw, lower-case and FASTA must all hit the same entry."""
    a = sequence_id(UBIQUITIN)
    b = sequence_id(UBIQUITIN.lower())
    assert a == b
    response = client.post("/api/submit", json={"input": f">ubq\n{UBIQUITIN}\n"})
    assert json.loads(response.data)["cached"] is True


def test_short_sequence_is_refused_with_a_useful_message(client):
    response = client.post("/api/submit", json={"input": "AAAA"})
    assert response.status_code == 400
    assert "10" in json.loads(response.data)["error"]


def test_over_length_offers_truncation(client):
    response = client.post("/api/submit", json={"input": "A" * 500})
    body = json.loads(response.data)
    assert response.status_code == 400
    assert body["can_truncate"] is True
    assert body["max_length"] == 400


def test_uncached_sequence_is_refused_when_folding_is_off(client):
    """The null backend must say so rather than hanging or 500ing."""
    response = client.post("/api/submit", json={"input": "MKV" * 20})
    assert response.status_code == 503
    assert json.loads(response.data)["can_fold"] is False


def test_payload_contract(client):
    """Everything the four tabs need must be in one payload."""
    payload = json.loads(client.get("/api/example/lysozyme").data)
    for key in ("id", "source", "sequence", "length", "residues", "stats",
                "alphabet", "centroid", "radius_of_gyration"):
        assert key in payload, key

    residue = payload["residues"][0]
    for key in ("i", "resnum", "aa", "ca", "cb", "n", "c", "q", "plddt",
                "sasa", "rsa", "ss", "bits", "entropy", "surprise",
                "top", "p", "h", "sub"):
        assert key in residue, key

    assert len(residue["q"]) == 4          # quaternion, xyzw
    assert len(residue["sub"]) == 20       # full substitution row
    assert len(payload["alphabet"]) == 20


def test_plddt_is_on_a_0_to_100_scale(client):
    """A 0-1 scale would render as a uniformly unconfident protein."""
    payload = json.loads(client.get("/api/example/ubiquitin").data)
    values = [r["plddt"] for r in payload["residues"]]
    assert 0.0 <= min(values) <= max(values) <= 100.0
    assert max(values) > 1.5, "looks like a 0-1 scale that was not normalised"


def test_every_glycine_has_a_beta_carbon(client):
    """Glycines are never skipped: a hole at every turn would be the result."""
    payload = json.loads(client.get("/api/example/ubiquitin").data)
    glycines = [r for r in payload["residues"] if r["aa"] == "G"]
    assert glycines
    for residue in glycines:
        assert residue["gly_cb_virtual"] is True
        assert residue["cb"] != residue["ca"]


def test_stack_heights_sum_to_information_content_in_the_payload(client):
    """The same acceptance criterion, checked on what is actually shipped.

    The payload carries only the top 6 of 20 letters, so the sum is a lower
    bound on R_i rather than equal to it. It must never exceed it.

    The tolerance is set by the payload's own rounding, not by floating point:
    each of the six heights is rounded to 4 dp and the total to 3 dp, so the
    sum of the rounded parts can legitimately exceed the rounded whole by
    6 * 5e-5 + 5e-4 = 8e-4. Anything beyond that is a real error.
    """
    ROUNDING = 8e-4
    payload = json.loads(client.get("/api/example/lysozyme").data)
    for residue in payload["residues"]:
        total = sum(residue["h"])
        assert total <= residue["bits"] + ROUNDING, residue["resnum"]

    # And the unrounded relationship holds exactly, which is the real claim:
    # a full 20-letter stack sums to the information content.
    import numpy as np
    from alphabetti import language_model as lm
    rng = np.random.default_rng(11)
    probs = rng.dirichlet(np.ones(20) * 0.4, size=100)
    assert np.abs(lm.letter_heights(probs).sum(axis=1)
                  - lm.information_content(probs)).max() < 1e-12


def test_downloads(client):
    payload = json.loads(client.get("/api/example/myoglobin").data)
    result_id = payload["id"]

    csv = client.get(f"/api/download/{result_id}.csv")
    assert csv.status_code == 200
    lines = csv.data.decode().strip().split("\n")
    assert lines[0] == "resnum,aa,plddt,rsa,sasa_A2,ss,bits,entropy,surprise"
    assert len(lines) == payload["length"] + 1

    assert client.get(f"/api/download/{result_id}.json").status_code == 200
    assert client.get(f"/api/download/{result_id}.pdb").status_code == 200
    assert client.get(f"/api/download/{result_id}.xyz").status_code == 400


def test_examples_use_mature_chains_not_precursors(client):
    """Every example names a residue range, and it must actually be applied.

    The database sequence is the precursor. Ubiquitin's entry is a nine-copy
    tandem polyprotein and lysozyme's carries an 18-residue signal peptide that
    is cleaved in vivo, has no structure of its own, and trails off the fold as
    a disordered tail. Both look plausible if the range is silently dropped.
    """
    lengths = {"ubiquitin": 76, "lysozyme": 129, "myoglobin": 153, "tim": 248}
    for name, expected in lengths.items():
        payload = json.loads(client.get(f"/api/example/{name}").data)
        assert payload["length"] == expected, name

    lysozyme = json.loads(client.get("/api/example/lysozyme").data)
    # The signal peptide is MRSLLILVLCFLPLAALG; the mature chain starts KVFGRC.
    assert lysozyme["sequence"].startswith("KVFGRC")
    assert "MRSLLILVLCFLPLAALG" not in lysozyme["sequence"]

    # Ubiquitin's monomer must not be followed by a second copy of itself.
    ubiquitin = json.loads(client.get("/api/example/ubiquitin").data)
    assert ubiquitin["sequence"].startswith("MQIFVKTLTGK")
    assert ubiquitin["sequence"].count("MQIFVKTLTGK") == 1


def test_examples_all_fold_confidently(client):
    """No example may ship with a fold nobody should trust.

    GFP was removed for exactly this: mean pLDDT 42.8, because ESM-2 has almost
    no signal for it and ESMFold shares that trunk. An example is a claim about
    what the app does well.
    """
    for name in ("ubiquitin", "lysozyme", "myoglobin", "tim"):
        payload = json.loads(client.get(f"/api/example/{name}").data)
        assert payload["stats"]["mean_plddt"] > 85, (name, payload["stats"]["mean_plddt"])


def test_missing_result_is_a_clean_404(client):
    assert client.get("/api/result/" + "0" * 64).status_code == 404
    assert client.get("/api/status/nope").status_code == 404


def test_cache_counts_hits(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.put("abc", "MKV", {"stats": {}}, "PDB")
    assert cache.stats()["entries"] == 1
    cache.get("abc")
    cache.get("abc")
    assert cache.stats()["hits"] == 2


def test_stale_jobs_are_reaped(tmp_path):
    """A restart must not leave clients polling a job nothing can finish."""
    cache = Cache(tmp_path / "c.sqlite")
    cache.create_job("j1", "MKV", {})
    cache.update_job("j1", state="running", created_at=0)
    assert cache.reap_stale_jobs(timeout=1) == 1
    assert cache.get_job("j1")["state"] == "failed"
    assert "restarted" in cache.get_job("j1")["error"]
