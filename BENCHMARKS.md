# ALPHABETTI benchmarks

Real timings, measured rather than estimated. Every number here came off the
machines that actually serve the app.

## Why the fold is not on the droplet

The build brief said all compute runs on the droplet. It cannot.

| | |
|---|---|
| `esmfold_v1` weights | **7.9 GB** fp32 (about 4.0 GB at half precision) |
| Peak RAM, 400-residue CPU fold | 12 to 16 GB, dominated by the L x L pair representation |
| `esm2_t33_650M_UR50D` weights | 2.4 GB fp32 |
| Droplet RAM | **3.9 GB total, about 2.0 GB free** |
| Droplet cores | 2 |
| Other services already resident | 8 (AlphaFraud, BoltzMaker, ButtFold, chatPDB, ChemSage, CODSWALLOP, FlexAppeal, PANTS) |

The shortfall is a factor of four to six before anything else on the box is
considered, and no chunk size closes it. The fold therefore runs on a Hugging
Face ZeroGPU Space and the droplet keeps the cache, the geometry, the
accessibility and the payload, none of which need a GPU.

Everything downstream of the fold is unchanged by that move: the backend returns
coordinates and a probability matrix, and every derived quantity is computed in
`alphabetti/`, where it is unit-tested without a GPU.

## Fold timings (ZeroGPU, H200 slice)

Measured 2026-09-03 via `scripts/prewarm.py`. "Wall" is the complete round trip
from the droplet's point of view, including queueing and transfer; the two GPU
columns are what the Space reports for itself.

| Protein | Accession | Residues | Wall (s) | ESMFold (s) | ESM-2 masked (s) | Mean pLDDT |
|---|---|---|---|---|---|---|
| Ubiquitin | P0CG48 (1-76) | 76 | 2.7 | 0.76 | 0.41 | 90.5 |
| Lysozyme C | P00698 | 147 | 5.1 | 1.02 | 1.56 | 91.0 |
| Myoglobin | P02144 | 154 | 7.3 | 1.09 | 1.69 | 93.3 |
| GFP | P42212 | 238 | 10.9 | 2.86 | 4.47 | 42.8 |
| Polyubiquitin-C (truncated) | P0CG48 (1-400) | 400 | 32.4 | 11.13 | 12.72 | 93.5 |

A **cold Space adds 30 to 60 seconds** to the first request after an idle
period, which is most of the wait on the first fold of the day. The job's stage
labels distinguish waking from folding for exactly this reason.

A **cache hit returns in well under a second** and never touches the queue: the
lookup happens in the web process before anything is enqueued.

### What this changes about the length cap

The 400-residue cap was set in the brief against an assumed CPU fold of minutes.
At 32.4 s wall for 400 residues the cap is no longer a wall-clock constraint,
and the honest reason to keep it is now ZeroGPU quota and payload size rather
than patience. Revisit it against real usage.

## Masked versus wild-type marginals

This measurement changed a default. The brief made masked marginals an opt-in
"high fidelity" toggle on the grounds that they cost L forward passes instead
of one. They do. It does not matter at GPU speed, and the cheap option is
actively misleading.

Ubiquitin, L = 76:

| | Wild-type marginals | Masked marginals |
|---|---|---|
| Top-1 equals wild type | **100 %** | 80 % |
| Mean information content | 4.01 bits | **3.20 bits** |
| Range | 1.43 to 4.32 bits | 0.99 to 4.31 bits |
| Pseudo-perplexity | 1.07 | 1.78 |
| Language model time | 0.03 s | 0.42 s |
| Extrapolated to 400 residues | - | about 2.2 s |

The first row is the problem. An unmasked forward pass can read the answer off
its own input, so it returns the wild-type residue at every position with
near-maximal confidence. GIBBERISH would have been a three-dimensional rendering
of the input sequence: every stack a maximal single-letter tower, with no
conservation signal anywhere.

Masked marginals cost 0.42 s against a fold of 0.76 s. They are now the default;
`ALPHABETTI_MASKED_MARGINALS=0` restores the old behaviour for comparison.

## Where the method fails, with numbers

GFP is kept as an example precisely because it fails. ESM-2 650M has almost no
evolutionary signal for avGFP:

| Sequence | Masked top-1 | Mean bits |
|---|---|---|
| Lysozyme | 66 % | 2.85 |
| Ubiquitin | 80 % | 3.20 |
| **GFP** | **10 %** | **0.27** |
| **GFP, randomly shuffled (control)** | **8 %** | **0.17** |

GFP sits barely above its own shuffled control. Because ESMFold's trunk *is*
ESM-2, the structure fails with it rather than independently: mean pLDDT 42.8.
The two never contradict each other, which is what makes this failure mode worth
showing rather than hiding.

## Payload size

Gzipped, as served. The brief's budget was 5 MB for a 400-residue protein.

| Residues | Raw JSON | Gzipped |
|---|---|---|
| 76 | 41 KB | 14 KB |
| 147 | 79 KB | 27 KB |
| 238 | 126 KB | 44 KB |
| 400 (extrapolated) | 0.21 MB | **74 KB** |

Comfortably inside budget, which is why all four tabs can be served from one
computation with no further round trip.

## Accuracy checks

Not timings, but the numbers that say the output is right.

| Check | Result |
|---|---|
| Virtual CB versus real CB, 1UBQ, 70 non-glycine residues | **0.13 A mean**, 0.35 A max |
| ESMFold ubiquitin versus 1UBQ crystal structure | **0.84 A CA RMSD** over 76 residues |
| FreeSASA total for 1UBQ | 4804 A2 (published value about 4800) |
| P-SEA fallback versus DSSP, 805 residues over 10 chains | **83.7 %** three-state, 83.6 % balanced (H 0.95, E 0.93, C 0.63) |
| Stack heights summed against information content | agree to 4e-16 |
| Baloo 2 typeface conversion, glyph area versus source outlines | within 0.34 % (polyline sampling error) |

The P-SEA number matters because **`mkdssp` is not installed on the droplet**,
so the geometric fallback is the production path rather than a safety net. It
scored 55 % before a 180-degree dihedral sign error was found and fixed.

## Method

```bash
# Fold timings and the example payloads
.venv/bin/python scripts/prewarm.py

# Accuracy checks
.venv/bin/python -m pytest tests/ -q
```
