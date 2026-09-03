#!/usr/bin/env python3
"""Compute the example payloads that give the app its landing state.

Section 8 of the build spec: the landing state must never be empty. These
payloads are committed to the repository so that a fresh deployment shows a
rotating protein before it has ever reached a GPU, and so the example buttons
never queue.

Run from the repository root:
    .venv/bin/python scripts/prewarm.py            # all three
    .venv/bin/python scripts/prewarm.py ubiquitin  # just one
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A stale HF_TOKEN in the environment shadows the valid cached login and every
# call fails with an authentication error that names the wrong cause.
os.environ.pop("HF_TOKEN", None)
from huggingface_hub import get_token                       # noqa: E402

from alphabetti.cache import sequence_id                    # noqa: E402
from alphabetti.folding import HFSpaceBackend               # noqa: E402
from alphabetti.payload import build                        # noqa: E402
from alphabetti.sequences import parse_input                # noqa: E402
from config import get_config                               # noqa: E402

EXAMPLES = {
    # (accession, residue range or None). See app.py for why ubiquitin is a
    # range: P0CG48 is a nine-copy tandem precursor, not the monomer.
    "ubiquitin": ("P0CG48", (1, 76)),
    "lysozyme": ("P00698", None),
    "myoglobin": ("P02144", None),
    "gfp": ("P42212", None),
}


def main(names: list[str]) -> None:
    settings = get_config("production")
    backend = HFSpaceBackend(settings.HF_SPACE, token=get_token(), timeout=1800)
    out_dir = ROOT / "examples"
    out_dir.mkdir(exist_ok=True)
    timings = []

    for name in names:
        accession, residues = EXAMPLES[name]
        print(f"\n=== {name} ({accession}) ===", flush=True)
        parsed = parse_input(
            accession,
            residues=residues,
            allow_truncation=True,
            max_length=settings.MAX_SEQUENCE_LENGTH,
            min_length=settings.MIN_SEQUENCE_LENGTH,
        )
        print(f"  {parsed.name}  {len(parsed.sequence)} residues"
              f"{'  (TRUNCATED)' if parsed.truncated else ''}", flush=True)

        started = time.time()
        result = backend.fold(parsed.sequence, masked=True)
        wall = time.time() - started
        print(f"  folded in {wall:.1f}s wall "
              f"(gpu: fold {result.fold_seconds}s, lm {result.lm_seconds}s)", flush=True)

        payload = build(
            result, parsed.sequence, parsed.as_source(),
            top_k=settings.TOP_K, truncated=parsed.truncated,
            original_length=parsed.original_length,
            result_id=sequence_id(parsed.sequence),
        )
        payload["notes"] = list(parsed.notes) + payload.get("notes", [])

        (out_dir / f"{name}.json").write_text(
            json.dumps(payload, separators=(",", ":")))
        (out_dir / f"{name}.pdb").write_text(result.pdb)

        stats = payload["stats"]
        size = (out_dir / f"{name}.json").stat().st_size / 1024
        print(f"  mean pLDDT {stats['mean_plddt']}  mean bits {stats['mean_bits']}"
              f"  perplexity {stats['perplexity']}  SS via {stats['ss_method']}", flush=True)
        print(f"  wrote examples/{name}.json ({size:.0f} KB) and {name}.pdb", flush=True)
        timings.append((name, len(parsed.sequence), wall,
                        result.fold_seconds, result.lm_seconds, stats["mean_plddt"]))

    print("\n--- for BENCHMARKS.md ---")
    print("| Protein | Residues | Wall (s) | ESMFold (s) | ESM-2 masked (s) | Mean pLDDT |")
    print("|---|---|---|---|---|---|")
    for name, length, wall, fold, lm, plddt in timings:
        print(f"| {name} | {length} | {wall:.1f} | {fold:.2f} | {lm:.2f} | {plddt} |")


if __name__ == "__main__":
    requested = sys.argv[1:] or list(EXAMPLES)
    unknown = [n for n in requested if n not in EXAMPLES]
    if unknown:
        raise SystemExit(f"Unknown example(s): {unknown}. Choose from {list(EXAMPLES)}.")
    main(requested)
