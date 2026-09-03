"""The maths, checked against hand-calculated values and real structures.

None of this needs a GPU, a network or a model, which is the point of keeping
every derived quantity out of the fold service.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphabetti import accessibility, geometry, language_model as lm   # noqa: E402
from alphabetti.sequences import SequenceError, clean_sequence, parse_input  # noqa: E402


# ---------------------------------------------------------------- information

def test_certain_position_is_maximally_informative():
    """A position the model is certain about carries log2(20) bits."""
    probs = np.zeros((1, 20))
    probs[0, lm.INDEX["M"]] = 1.0
    assert lm.entropy(probs)[0] == pytest.approx(0.0, abs=1e-9)
    assert lm.information_content(probs)[0] == pytest.approx(math.log2(20), abs=1e-9)


def test_uniform_position_carries_no_information():
    probs = np.full((1, 20), 1 / 20)
    assert lm.entropy(probs)[0] == pytest.approx(math.log2(20), abs=1e-9)
    assert lm.information_content(probs)[0] == pytest.approx(0.0, abs=1e-9)


def test_two_way_tie_is_exactly_one_bit():
    """Hand-calculable: two equally likely outcomes is 1 bit of entropy."""
    probs = np.zeros((1, 20))
    probs[0, lm.INDEX["A"]] = 0.5
    probs[0, lm.INDEX["V"]] = 0.5
    assert lm.entropy(probs)[0] == pytest.approx(1.0, abs=1e-9)
    assert lm.information_content(probs)[0] == pytest.approx(math.log2(20) - 1, abs=1e-9)


def test_max_bits_constant():
    assert lm.MAX_BITS == pytest.approx(4.321928, abs=1e-5)


def test_stack_heights_sum_to_information_content():
    """THE acceptance criterion: a GIBBERISH stack's total height IS R_i.

    Build spec section 13. If this fails the picture is decorative rather than
    quantitative, whatever else still works.
    """
    rng = np.random.default_rng(0)
    probs = rng.dirichlet(np.ones(20) * 0.4, size=200)
    heights = lm.letter_heights(probs)
    expected = lm.information_content(probs)
    assert np.abs(heights.sum(axis=1) - expected).max() < 1e-12


def test_stack_heights_by_hand():
    """A worked example small enough to check on paper.

    Two residues at p = 0.5, everything else zero:
        H = 1 bit, R = log2(20) - 1 = 3.3219 bits
        each letter's height = 0.5 * 3.3219 = 1.6610 bits
        the two together = 3.3219 = R.
    """
    probs = np.zeros((1, 20))
    probs[0, lm.INDEX["A"]] = 0.5
    probs[0, lm.INDEX["V"]] = 0.5
    heights = lm.letter_heights(probs)[0]
    expected_each = 0.5 * (math.log2(20) - 1)
    assert heights[lm.INDEX["A"]] == pytest.approx(expected_each, abs=1e-9)
    assert heights[lm.INDEX["V"]] == pytest.approx(expected_each, abs=1e-9)
    assert heights.sum() == pytest.approx(math.log2(20) - 1, abs=1e-9)


def test_top_letters_are_descending_and_labelled():
    rng = np.random.default_rng(1)
    probs = rng.dirichlet(np.ones(20) * 0.3, size=10)
    top = lm.top_letters(probs, 6)
    for row in top:
        assert len(row) == 6
        probabilities = [p for _, p, _ in row]
        assert probabilities == sorted(probabilities, reverse=True)
        assert all(letter in lm.ALPHABET for letter, _, _ in row)


# --------------------------------------------------------------- variants

def test_wild_type_variant_score_is_exactly_zero():
    """Sign convention: score(wt -> wt) = log p - log p = 0, exactly."""
    rng = np.random.default_rng(2)
    sequence = "MQIFVKTLTG"
    probs = rng.dirichlet(np.ones(20), size=len(sequence))
    scores = lm.variant_scores(probs, sequence)
    for i, aa in enumerate(sequence):
        assert scores[i, lm.INDEX[aa]] == 0.0


def test_variant_score_sign_convention():
    """Negative means deleterious: the mutant is less likely than the wild type."""
    probs = np.full((1, 20), 0.01)
    probs[0, lm.INDEX["W"]] = 0.5          # wild type, likely
    probs[0, lm.INDEX["P"]] = 0.001        # mutant, unlikely
    probs /= probs.sum()
    scores = lm.variant_scores(probs, "W")
    assert scores[0, lm.INDEX["P"]] < 0
    assert scores[0, lm.INDEX["W"]] == 0.0


def test_restrict_to_canonical_normalises_over_twenty():
    vocabulary = lm.ALPHABET + "XBZUO"
    logits = np.zeros((5, len(vocabulary)))
    logits[:, len(lm.ALPHABET):] = 50.0     # huge mass on non-canonical tokens
    probs = lm.restrict_to_canonical(logits, vocabulary)
    assert probs.shape == (5, 20)
    # Restricting before the softmax means the non-canonical tokens never
    # existed, so the twenty are uniform rather than jointly tiny.
    assert np.allclose(probs.sum(axis=1), 1.0)
    assert np.allclose(probs, 1 / 20)


# --------------------------------------------------------------- geometry

def test_virtual_cb_matches_ideal_geometry():
    """Against Engh & Huber ideal backbone geometry."""
    angle = math.radians(111.0)
    ca = np.array([0.0, 0.0, 0.0])
    n = np.array([1.458, 0.0, 0.0])
    c = np.array([1.525 * math.cos(angle), 1.525 * math.sin(angle), 0.0])
    cb = geometry.virtual_cb(n, ca, c)
    assert np.linalg.norm(cb - ca) == pytest.approx(1.53, abs=0.01)


def test_frames_are_orthonormal_and_right_handed():
    """Everything downstream composes these into matrices and assumes both."""
    rng = np.random.default_rng(3)
    ca = np.cumsum(rng.normal(0, 1, (40, 3)), axis=0) * 1.2
    cb = ca + rng.normal(0, 1, (40, 3))
    frames = geometry.residue_frames(ca, cb)
    identity = np.einsum("nij,nik->njk", frames, frames)
    assert np.abs(identity - np.eye(3)).max() < 1e-9
    # +1 not -1: a determinant of -1 is a reflection, and every letter would
    # render mirrored.
    assert np.allclose(np.linalg.det(frames), 1.0)


def test_degenerate_frame_does_not_produce_nan():
    """up parallel to the tangent: the cross product is worthless there."""
    ca = np.array([[0, 0, 0], [0, 0, 3.8], [0, 0, 7.6]], dtype=float)
    cb = ca + np.array([0, 0, 1.53])
    frames = geometry.residue_frames(ca, cb)
    assert np.isfinite(frames).all()
    identity = np.einsum("nij,nik->njk", frames, frames)
    assert np.abs(identity - np.eye(3)).max() < 1e-9


@pytest.mark.parametrize("length", [1, 2, 3])
def test_short_chains_do_not_raise(length):
    """Termini are the classic index error and a 1-residue chain has no tangent."""
    ca = np.arange(length * 3, dtype=float).reshape(length, 3)
    cb = ca + 1.0
    assert geometry.residue_frames(ca, cb).shape == (length, 3, 3)


def test_quaternion_round_trip():
    rng = np.random.default_rng(4)
    ca = np.cumsum(rng.normal(0, 1, (25, 3)), axis=0)
    cb = ca + rng.normal(0, 1, (25, 3))
    frames = geometry.residue_frames(ca, cb)
    quaternions = geometry.matrices_to_quaternions(frames)
    assert np.allclose(np.linalg.norm(quaternions, axis=1), 1.0)

    x, y, z, w = quaternions.T
    rebuilt = np.empty_like(frames)
    rebuilt[:, 0, 0] = 1 - 2 * (y * y + z * z)
    rebuilt[:, 0, 1] = 2 * (x * y - z * w)
    rebuilt[:, 0, 2] = 2 * (x * z + y * w)
    rebuilt[:, 1, 0] = 2 * (x * y + z * w)
    rebuilt[:, 1, 1] = 1 - 2 * (x * x + z * z)
    rebuilt[:, 1, 2] = 2 * (y * z - x * w)
    rebuilt[:, 2, 0] = 2 * (x * z - y * w)
    rebuilt[:, 2, 1] = 2 * (y * z + x * w)
    rebuilt[:, 2, 2] = 1 - 2 * (x * x + y * y)
    assert np.abs(rebuilt - frames).max() < 1e-9


def test_ca_torsion_sign_convention():
    """A right-handed alpha helix must give a POSITIVE CA virtual torsion.

    This is a regression test for a real bug. The dihedral was computed with
    b0 = p1 - p0 instead of p0 - p1, which negates one vector and returns a
    torsion exactly 180 degrees out. Helices sat at -129 degrees where P-SEA's
    published reference is +50, every angle criterion silently failed, and the
    CA-only secondary structure assignment scored 55 % against DSSP.
    """
    turn = np.deg2rad(100.0 * np.arange(12))
    ca = np.stack([2.3 * np.cos(turn), 2.3 * np.sin(turn), 1.5 * np.arange(12)], axis=1)
    torsions = accessibility._ca_torsions(ca)
    interior = torsions[~np.isnan(torsions)]
    assert np.median(interior) == pytest.approx(50.0, abs=15.0)


# ----------------------------------------------------------- accessibility

def test_tien_maxima_cover_every_residue():
    for aa in lm.ALPHABET:
        assert aa in accessibility.TIEN_MAX_ASA
    assert accessibility.TIEN_MAX_ASA["G"] == 104.0    # smallest
    assert accessibility.TIEN_MAX_ASA["W"] == 285.0    # largest


def test_relative_sasa_is_not_clipped():
    """A hyper-exposed residue is real and must survive as a value above 1."""
    sasa = np.array([300.0])
    assert accessibility.relative_sasa(sasa, "G")[0] > 1.0


def test_psea_finds_a_helix():
    """An ideal alpha helix must come back as helix, not coil."""
    turn = np.deg2rad(100.0 * np.arange(24))
    ca = np.stack([2.3 * np.cos(turn), 2.3 * np.sin(turn), 1.5 * np.arange(24)], axis=1)
    assignment = accessibility.psea(ca)
    assert assignment.count("H") / len(assignment) > 0.7


def test_psea_handles_a_chain_too_short_to_assign():
    assert accessibility.psea(np.zeros((3, 3))) == "CCC"


def test_exposed_hydrophobics_needs_both_conditions():
    rsa = np.array([0.9, 0.9, 0.1])
    sequence = "IRI"          # exposed Ile, exposed Arg, buried Ile
    mask = accessibility.exposed_hydrophobics(rsa, sequence)
    assert list(mask) == [True, False, False]


# --------------------------------------------------------------- sequences

def test_gap_characters_are_stripped_with_a_note():
    cleaned, notes = clean_sequence("MQIF--VKT..LTGK")
    assert cleaned == "MQIFVKTLTGK"
    assert any("gap" in note for note in notes)


def test_bad_character_names_itself_and_its_position():
    with pytest.raises(SequenceError) as error:
        clean_sequence("MQIFVKTLTGKZ")
    assert "'Z'" in str(error.value)
    assert "12" in str(error.value)


def test_over_length_refuses_before_it_truncates():
    """Truncation is a decision the user makes, not one made for them."""
    with pytest.raises(SequenceError) as error:
        parse_input("A" * 500, max_length=400, fetch=False)
    assert "400" in str(error.value)


def test_truncation_reports_what_it_kept():
    parsed = parse_input("A" * 500, allow_truncation=True, max_length=400, fetch=False)
    assert len(parsed.sequence) == 400
    assert parsed.truncated
    assert parsed.original_length == 500
    assert any("400" in note for note in parsed.notes)


def test_multi_record_fasta_says_which_record_it_used():
    text = ">first\nMQIFVKTLTGKT\n>second\nAAAAAAAAAAAA\n"
    parsed = parse_input(text, fetch=False)
    assert parsed.sequence == "MQIFVKTLTGKT"
    assert any("2 records" in note for note in parsed.notes)


def test_accession_is_not_mistaken_for_a_sequence():
    from alphabetti.sequences import looks_like_accession, looks_like_entry_name
    assert looks_like_accession("P0CG48")
    assert looks_like_entry_name("UBC_HUMAN")
    # A short peptide made only of letters that happen to look accession-like
    # must not be sent to UniProt.
    assert not looks_like_accession("MQIFVKTLTGK")
