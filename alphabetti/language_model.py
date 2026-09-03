"""Information content, entropy and variant effect scores.

Everything GIBBERISH and BALDERDASH draw is computed here, from one input: a
20 x L matrix of amino acid probabilities. That matrix comes off the GPU (see
folding.py); nothing in this module needs torch, a GPU or a network, which is
what makes all of it unit-testable against hand-calculated values.

The model is ESM-2 650M (facebook/esm2_t33_650M_UR50D). The headline claim of
the app rests on what is NOT in that sentence: there is no multiple sequence
alignment anywhere in this pipeline. A conventional sequence logo measures
conservation by counting residues in an alignment column. This one asks a
protein language model what it expects at each position, which is a different
quantity computed a different way, and the UI says so.
"""

from __future__ import annotations

import numpy as np

# The 20 canonical amino acids, in the fixed order used by every array in this
# module. ESM-2's vocabulary includes special tokens, X, B, Z, U and O; the
# softmax is restricted to these twenty before anything else happens, so the
# probabilities sum to one over the alphabet a sequence logo is defined on.
ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
INDEX = {aa: i for i, aa in enumerate(ALPHABET)}

# Maximum information content of a position, log2(20). Quoted in the UI and the
# GIBBERISH legend to three decimal places, because a scale bar with no maximum
# is not a scale bar.
MAX_BITS = float(np.log2(20))          # 4.321928...

# Guards a log of zero. Smaller than any probability the model will realistically
# emit, so it never perturbs a real value.
_EPSILON = 1e-12


def restrict_to_canonical(logits: np.ndarray, token_order: str) -> np.ndarray:
    """Softmax over the 20 canonical residues only, from raw model logits.

    `token_order` is the model's own vocabulary as a string, so the columns can
    be picked by identity rather than by a hard-coded set of indices that would
    silently point at the wrong residues if the tokeniser ever changed.

    Restricting BEFORE the softmax rather than after is deliberate: renormalising
    a full-vocabulary softmax gives the same ratios but a different entropy,
    because the probability mass held by special tokens is redistributed rather
    than never existing. The logits are the model's actual preferences; the
    twenty-way softmax is the right way to read them.
    """
    columns = [token_order.index(aa) for aa in ALPHABET]
    selected = np.asarray(logits, dtype=np.float64)[..., columns]
    # Subtract the max for numerical stability before exponentiating.
    shifted = selected - selected.max(axis=-1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / exponentiated.sum(axis=-1, keepdims=True)


def entropy(probs: np.ndarray) -> np.ndarray:
    """Shannon entropy per position, in bits.

    H_i = -sum_a p_a log2(p_a)

    No small-sample correction is applied, and that is a deliberate decision
    rather than an oversight (build spec section 9). The usual e_n correction in
    a sequence logo compensates for estimating a distribution from a finite
    number of aligned sequences. Here there is no alignment and no sample: the
    distribution is the model's output, known exactly. Correcting it would be
    subtracting a bias that does not exist.
    """
    probs = np.asarray(probs, dtype=np.float64)
    return -np.sum(probs * np.log2(probs + _EPSILON), axis=-1)


def information_content(probs: np.ndarray) -> np.ndarray:
    """R_i = log2(20) - H_i, in bits. The height of a whole logo stack.

    Clipped at zero on the low side only. A position with entropy above log2(20)
    is impossible for a normalised distribution, so a tiny negative here is
    floating-point noise, and a negative stack height would render as a letter
    growing downwards through the backbone.
    """
    return np.maximum(MAX_BITS - entropy(probs), 0.0)


def letter_heights(probs: np.ndarray) -> np.ndarray:
    """Per-letter height in bits: height_a = p_a * R_i.

    This is the standard sequence logo convention (Schneider & Stephens 1990).
    The heights at a position sum to that position's information content, which
    is the property the GIBBERISH acceptance test checks and the reason the
    stack is meaningful rather than decorative.
    """
    probs = np.asarray(probs, dtype=np.float64)
    return probs * information_content(probs)[..., None]


def top_letters(probs: np.ndarray, n: int = 6) -> list[list[tuple[str, float, float]]]:
    """Per position, the n most probable residues as (letter, probability, bits).

    Descending by probability, so the client can stack them tallest-first
    without sorting. `n` defaults to 6 because that is what the payload carries;
    GIBBERISH renders at most 6 and defaults to 4.
    """
    probs = np.asarray(probs, dtype=np.float64)
    heights = letter_heights(probs)
    order = np.argsort(-probs, axis=-1)[:, :n]

    out: list[list[tuple[str, float, float]]] = []
    for position, indices in enumerate(order):
        out.append([
            (ALPHABET[i], float(probs[position, i]), float(heights[position, i]))
            for i in indices
        ])
    return out


def variant_scores(probs: np.ndarray, sequence: str) -> np.ndarray:
    """Full L x 20 matrix of log-ratio substitution scores.

        score(wt -> mut) = log p(mut) - log p(wt)

    NEGATIVE MEANS DELETERIOUS: the model considers the mutant less likely than
    the residue actually there. The wild-type position in each row is exactly
    zero by construction, which is a useful thing to assert in a test.

    Natural log, following the ESM-1v convention (Meier et al., NeurIPS 2021),
    NOT log2 as used for the bits above. The two live side by side in this
    module and mixing them would be easy and invisible, so: bits are log2 and
    are a height in Angstroms; variant scores are natural log and are a colour.

    These are language model scores. They are not clinical predictions, and the
    BALDERDASH panel says so.
    """
    probs = np.asarray(probs, dtype=np.float64)
    log_probs = np.log(probs + _EPSILON)
    wild_type = np.array([INDEX.get(aa, -1) for aa in sequence])

    scores = np.zeros_like(log_probs)
    for position, wt in enumerate(wild_type):
        if wt < 0:
            # An X has no wild-type probability to compare against. Zero is the
            # honest answer: no evidence either way, and the UI greys it out.
            continue
        scores[position] = log_probs[position] - log_probs[position, wt]
    return scores


def wild_type_surprise(probs: np.ndarray, sequence: str) -> np.ndarray:
    """-log p(wild type) per position: how surprised the model is to see this residue.

    Drives the BALDERDASH emissive glow. Large means the model strongly expected
    something else, which is the definition of a hotspot for this display.
    Natural log, matching variant_scores.
    """
    probs = np.asarray(probs, dtype=np.float64)
    out = np.zeros(len(sequence))
    for position, aa in enumerate(sequence):
        index = INDEX.get(aa)
        if index is None:
            continue
        out[position] = -np.log(probs[position, index] + _EPSILON)
    return out


def perplexity(probs: np.ndarray, sequence: str) -> float:
    """Pseudo-perplexity of the sequence under the model. One number for the info panel.

    Low means the model finds the sequence unremarkable, which for a natural
    protein it usually should. A high value on a natural sequence is worth
    noticing: it means either a genuinely unusual protein or a bad input.
    """
    surprise = wild_type_surprise(probs, sequence)
    known = [s for s, aa in zip(surprise, sequence) if aa in INDEX]
    if not known:
        return float("nan")
    return float(np.exp(np.mean(known)))
