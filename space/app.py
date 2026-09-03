"""ALPHABETTI fold service: ESMFold + ESM-2 650M on a ZeroGPU slice.

This Space is deliberately stupid. It takes a sequence and returns coordinates
and a probability matrix; it computes no entropy, no accessibility, no frames
and no payload. All of that lives in the ALPHABETTI repository, in modules that
need no GPU and are unit-tested without one. The Space exists because the
droplet that runs the app has 3.9 GB of RAM and ESMFold needs roughly four
times that.

Two models, loaded once at import and kept resident:

    facebook/esmfold_v1              structure + pLDDT   (7.9 GB fp32)
    facebook/esm2_t33_650M_UR50D     logits              (2.4 GB fp32)

The @spaces.GPU decorator is what makes this ZeroGPU: the process runs on CPU
until a decorated function is called, at which point an H200 slice is attached
for the duration. Model loading therefore happens on CPU and the move to GPU
happens inside the decorated function, not at import.
"""

from __future__ import annotations

import os
import time

import gradio as gr
import numpy as np
import spaces
import torch
from transformers import AutoTokenizer, EsmForMaskedLM, EsmForProteinFolding

# The 20 canonical amino acids, in the order every array in ALPHABETTI uses.
# This MUST match alphabetti/language_model.py ALPHABET exactly; a mismatch
# would relabel every probability without changing any number, which is the
# kind of bug that produces a beautiful, confident, entirely wrong picture.
ALPHABET = "ACDEFGHIKLMNPQRSTVWY"

MAX_LENGTH = int(os.environ.get("ALPHABETTI_MAX_LENGTH", "400"))
MIN_LENGTH = int(os.environ.get("ALPHABETTI_MIN_LENGTH", "10"))

print("Loading ESMFold ...", flush=True)
FOLD_TOKENISER = AutoTokenizer.from_pretrained("facebook/esmfold_v1")
FOLDER = EsmForProteinFolding.from_pretrained(
    "facebook/esmfold_v1", low_cpu_mem_usage=True, torch_dtype=torch.float32
)
FOLDER.eval()

print("Loading ESM-2 650M ...", flush=True)
LM_TOKENISER = AutoTokenizer.from_pretrained("facebook/esm2_t33_650M_UR50D")
LANGUAGE = EsmForMaskedLM.from_pretrained("facebook/esm2_t33_650M_UR50D")
LANGUAGE.eval()

# Map the model's vocabulary onto ALPHABET once, by token identity rather than
# by index. ESM-2's vocabulary contains special tokens plus X, B, Z, U and O,
# and its ordering is not alphabetical.
_VOCAB = LM_TOKENISER.get_vocab()
CANONICAL_COLUMNS = [_VOCAB[aa] for aa in ALPHABET]
print(f"canonical column indices: {CANONICAL_COLUMNS}", flush=True)


def _validate(sequence: str) -> str:
    sequence = "".join(sequence.split()).upper()
    if not sequence:
        raise gr.Error("Empty sequence.")
    if len(sequence) < MIN_LENGTH:
        raise gr.Error(f"Sequence is {len(sequence)} residues; the minimum is {MIN_LENGTH}.")
    if len(sequence) > MAX_LENGTH:
        raise gr.Error(f"Sequence is {len(sequence)} residues; the maximum is {MAX_LENGTH}.")
    allowed = set(ALPHABET) | {"X"}
    bad = [(i + 1, c) for i, c in enumerate(sequence) if c not in allowed]
    if bad:
        position, character = bad[0]
        raise gr.Error(f"'{character}' at position {position} is not an amino acid.")
    return sequence


def _normalise_plddt(pdb: str) -> str:
    """Force the B-factor column onto a 0-100 scale.

    Build spec section 5.3: ESMFold's pLDDT reaches the PDB text through the
    B-factor column, and depending on the code path it arrives as 0-1 or 0-100.
    Shipping whichever one happened to come out would make the confidence ramp
    silently meaningless, so the scale is detected and fixed here, at the only
    point where both possibilities are still visible.
    """
    values = []
    for line in pdb.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) >= 66:
            try:
                values.append(float(line[60:66]))
            except ValueError:
                pass
    if not values:
        return pdb
    if max(values) <= 1.5:                      # a 0-1 scale, needs multiplying
        out = []
        for line in pdb.splitlines():
            if line.startswith(("ATOM", "HETATM")) and len(line) >= 66:
                try:
                    scaled = min(100.0, max(0.0, float(line[60:66]) * 100.0))
                    line = f"{line[:60]}{scaled:6.2f}{line[66:]}"
                except ValueError:
                    pass
            out.append(line)
        return "\n".join(out)
    return pdb


@spaces.GPU(duration=180)
def fold(sequence: str, masked: bool = True) -> dict:
    """Fold a sequence and score it. Returns coordinates and a 20 x L matrix.

    `masked` selects masked marginals (one forward pass per masked position,
    batched) over wild-type marginals (a single pass over the unmasked
    sequence). It defaults to True, which is the opposite of what the original
    brief specified, because the measurement says the brief was wrong at GPU
    speed.

    An unmasked pass can read the answer off its own input. Measured on
    ubiquitin, it returned the wild-type residue as its top choice at 100 % of
    positions with a mean information content of 4.01 of a possible 4.322
    bits: a sequence logo with no variation in it. Masking drops that to 80 %
    and 3.20 bits, which is a real conservation signal, and costs 0.42 s
    instead of 0.03 s (about 2.2 s extrapolated to 400 residues).
    """
    sequence = _validate(sequence)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    notes = []

    folder = FOLDER.to(device)
    if device == "cuda":
        folder.esm = folder.esm.half()
    language = LANGUAGE.to(device)

    started = time.time()
    with torch.no_grad():
        tokens = FOLD_TOKENISER(
            [sequence], return_tensors="pt", add_special_tokens=False
        )
        tokens = {k: v.to(device) for k, v in tokens.items()}
        pdb = folder.output_to_pdb(folder(**tokens))[0]
    fold_seconds = time.time() - started
    pdb = _normalise_plddt(pdb)

    started = time.time()
    with torch.no_grad():
        if masked:
            probs = _masked_marginals(sequence, language, device)
            notes.append("Masked marginals: one forward pass per masked position.")
        else:
            encoded = LM_TOKENISER(sequence, return_tensors="pt").to(device)
            logits = language(**encoded).logits[0]
            # Drop <cls> at the front and <eos> at the back so the rows line up
            # one-to-one with the sequence.
            logits = logits[1:-1]
            probs = _canonical_softmax(logits)
            notes.append(
                "Wild-type marginals: a single forward pass. Fast, but the model "
                "can read each residue off its own input, so expect near-maximal "
                "information content everywhere."
            )
    lm_seconds = time.time() - started

    return {
        "pdb": pdb,
        "probs": probs.tolist(),
        "fold_seconds": round(fold_seconds, 2),
        "lm_seconds": round(lm_seconds, 2),
        "device": device,
        "length": len(sequence),
        "notes": notes,
    }


def _canonical_softmax(logits: torch.Tensor) -> np.ndarray:
    """Softmax over the 20 canonical residues only.

    Restricting the logits BEFORE the softmax, rather than renormalising a
    full-vocabulary softmax afterwards, is the difference between asking "which
    amino acid" and "which token". The two give the same ratios but different
    entropies, and entropy is exactly what GIBBERISH plots.
    """
    selected = logits[:, CANONICAL_COLUMNS].float()
    return torch.softmax(selected, dim=-1).cpu().numpy().astype(np.float64)


def _masked_marginals(sequence: str, language, device, batch_size: int = 16) -> np.ndarray:
    """One forward pass per masked position, batched."""
    encoded = LM_TOKENISER(sequence, return_tensors="pt")
    ids = encoded["input_ids"][0]
    mask_id = LM_TOKENISER.mask_token_id
    length = len(sequence)

    rows = []
    for start in range(0, length, batch_size):
        stop = min(start + batch_size, length)
        batch = ids.repeat(stop - start, 1)
        for row, position in enumerate(range(start, stop)):
            batch[row, position + 1] = mask_id          # +1 skips <cls>
        batch = batch.to(device)
        logits = language(input_ids=batch).logits
        for row, position in enumerate(range(start, stop)):
            rows.append(_canonical_softmax(logits[row, position + 1].unsqueeze(0))[0])
    return np.stack(rows)


with gr.Blocks(title="ALPHABETTI fold service") as demo:
    gr.Markdown(
        "## ALPHABETTI fold service\n"
        "ESMFold + ESM-2 650M on ZeroGPU. Returns backbone coordinates and a "
        "20 x L probability matrix; every derived quantity is computed by the "
        "app that calls this.\n\n"
        "Front end: [alphabetti.mdeller.com](https://alphabetti.mdeller.com)"
    )
    with gr.Row():
        sequence_in = gr.Textbox(
            label="Sequence", lines=4,
            placeholder="MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG",
        )
    masked_in = gr.Checkbox(
        value=True,
        label="Masked marginals (default; unmasked reads the answer off its own input)",
    )
    run = gr.Button("Fold", variant="primary")
    out = gr.JSON(label="Result")
    run.click(fold, inputs=[sequence_in, masked_in], outputs=out, api_name="fold")

if __name__ == "__main__":
    demo.launch()
