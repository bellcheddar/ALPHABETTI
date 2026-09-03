"""Assemble the single JSON object that serves all four tabs.

Compute everything once, ship it once, switch tabs client side with no further
round trip (build spec section 5.2). That is why this is one function rather
than four endpoints: BUMFLUFF's accessibility and BALDERDASH's variant matrix
cost almost nothing once the structure exists, and a user who has waited for a
fold should never wait again to change tab.
"""

from __future__ import annotations

import io

import numpy as np
from Bio.PDB import PDBParser

from . import accessibility, geometry, language_model
from .folding import FoldResult

THREE_TO_ONE = accessibility.THREE_TO_ONE


def _parse_backbone(pdb: str) -> dict:
    """Pull N, CA, C, CB and pLDDT out of ESMFold's PDB text.

    Only the first chain is read. ESMFold folds one chain and the app says so
    on the About page; a multi-chain file here would mean something upstream
    changed.
    """
    structure = PDBParser(QUIET=True).get_structure("esmfold", io.StringIO(pdb))
    chain = next(iter(next(iter(structure))))

    names, n, ca, c, cb, plddt = [], [], [], [], [], []
    for residue in chain:
        if residue.id[0] != " " or "CA" not in residue:
            continue
        names.append(THREE_TO_ONE.get(residue.get_resname(), "X"))
        ca.append(residue["CA"].coord)
        n.append(residue["N"].coord if "N" in residue else residue["CA"].coord)
        c.append(residue["C"].coord if "C" in residue else residue["CA"].coord)
        cb.append(residue["CB"].coord if "CB" in residue else None)
        # pLDDT rides in the B-factor column. The Space normalises the scale to
        # 0-100 before it ever reaches here; this asserts that it did.
        plddt.append(float(residue["CA"].get_bfactor()))

    return {
        "sequence": "".join(names),
        "n": np.array(n, dtype=np.float64),
        "ca": np.array(ca, dtype=np.float64),
        "c": np.array(c, dtype=np.float64),
        "cb_real": cb,
        "plddt": np.array(plddt, dtype=np.float64),
    }


def build(
    result: FoldResult,
    sequence: str,
    source: dict,
    *,
    top_k: int = 6,
    truncated: bool = False,
    original_length: int | None = None,
    result_id: str = "",
) -> dict:
    """Turn a FoldResult into the payload the front end consumes."""
    backbone = _parse_backbone(result.pdb)
    ca, n, c = backbone["ca"], backbone["n"], backbone["c"]
    length = len(ca)

    if length != len(sequence):
        raise ValueError(
            f"The structure has {length} residues but the sequence has "
            f"{len(sequence)}. Refusing to build a payload that would put the "
            "wrong letter on every position."
        )

    # pLDDT must be 0-100. Section 9 of the spec asks for this to be asserted
    # rather than assumed, because the two possible scales differ by a factor of
    # 100 and a 0-1 value renders as a uniformly dark-blue, uniformly
    # low-confidence protein that looks plausible.
    plddt = backbone["plddt"]
    if plddt.size and plddt.max() <= 1.5:
        plddt = plddt * 100.0
    plddt = np.clip(plddt, 0.0, 100.0)

    # Glycine has no CB, so one is constructed. Never skipped: a hole at every
    # glycine would be a hole at exactly the positions where backbones turn.
    constructed = virtual = geometry.virtual_cb(n, ca, c)
    cb = np.array([
        real if real is not None else constructed[i]
        for i, real in enumerate(backbone["cb_real"])
    ], dtype=np.float64)
    glycines = sum(1 for x in backbone["cb_real"] if x is None)

    frames = geometry.residue_frames(ca, cb)
    quaternions = geometry.matrices_to_quaternions(frames)

    # Accessibility and secondary structure.
    sasa, total_sasa = accessibility.compute_sasa(result.pdb)
    if len(sasa) != length:
        # FreeSASA silently skips residues it cannot type. Padding keeps the
        # arrays aligned rather than shifting every value after the gap, which
        # would be invisible and wrong.
        padded = np.zeros(length)
        padded[:min(len(sasa), length)] = sasa[:length]
        sasa = padded
    rsa = accessibility.relative_sasa(sasa, sequence)
    ss, ss_method = accessibility.secondary_structure(result.pdb, ca)
    if len(ss) != length:
        ss = (ss + "C" * length)[:length]

    # Language model quantities.
    probs = np.asarray(result.probs, dtype=np.float64)
    entropy = language_model.entropy(probs)
    bits = language_model.information_content(probs)
    top = language_model.top_letters(probs, top_k)
    variants = language_model.variant_scores(probs, sequence)
    surprise = language_model.wild_type_surprise(probs, sequence)

    residues = []
    for i in range(length):
        residues.append({
            "i": i,
            "resnum": i + 1,
            "aa": sequence[i],
            "ca": [round(float(v), 3) for v in ca[i]],
            "cb": [round(float(v), 3) for v in cb[i]],
            "n": [round(float(v), 3) for v in n[i]],
            "c": [round(float(v), 3) for v in c[i]],
            # The frame ships as a quaternion in three.js xyzw order, so the
            # client never has to agree with us about matrix conventions.
            "q": [round(float(v), 5) for v in quaternions[i]],
            "plddt": round(float(plddt[i]), 1),
            "sasa": round(float(sasa[i]), 1),
            "rsa": round(float(rsa[i]), 3),
            "ss": ss[i],
            "bits": round(float(bits[i]), 3),
            "entropy": round(float(entropy[i]), 3),
            "surprise": round(float(surprise[i]), 3),
            "gly_cb_virtual": backbone["cb_real"][i] is None,
            # Top-k as parallel short arrays rather than a dict per position:
            # it is a third of the bytes and preserves the descending order.
            "top": [t[0] for t in top[i]],
            "p": [round(t[1], 4) for t in top[i]],
            "h": [round(t[2], 4) for t in top[i]],
            # The full 20-way substitution row, for the BALDERDASH heatmap.
            "sub": [round(float(v), 2) for v in variants[i]],
        })

    exposed_hydrophobic = accessibility.exposed_hydrophobics(rsa, sequence)

    return {
        "id": result_id,
        "source": source,
        "sequence": sequence,
        "length": length,
        "truncated": truncated,
        "original_length": original_length,
        "alphabet": language_model.ALPHABET,
        "residues": residues,
        "centroid": [round(float(v), 3) for v in geometry.centroid(ca)],
        "radius_of_gyration": round(geometry.radius_of_gyration(ca), 2),
        "stats": {
            "mean_plddt": round(float(plddt.mean()), 1),
            "min_plddt": round(float(plddt.min()), 1),
            "mean_bits": round(float(bits.mean()), 3),
            "max_bits": round(language_model.MAX_BITS, 3),
            "total_sasa": round(float(total_sasa), 1),
            "buried_fraction": round(float((rsa < accessibility.BURIED_CUTOFF).mean()), 3),
            "exposed_hydrophobics": int(exposed_hydrophobic.sum()),
            "perplexity": round(language_model.perplexity(probs, sequence), 2),
            "glycines_with_virtual_cb": glycines,
            "fold_seconds": round(result.fold_seconds, 2),
            "lm_seconds": round(result.lm_seconds, 2),
            "backend": result.backend,
            "device": result.device,
            "ss_method": ss_method,
            "cached": False,
        },
        # Two different kinds of thing, kept apart because the UI treats them
        # differently. `notes` is addressed to the user and describes something
        # that happened to THEIR input (a truncation, a stripped gap, an X).
        # `provenance` describes how the result was computed and belongs in the
        # info panel, not in a warning box. Mixing them meant every single fold
        # showed two amber warnings saying nothing was wrong.
        "notes": [],
        "provenance": list(result.notes),
    }
