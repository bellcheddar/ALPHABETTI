"""ALPHABETTI: Amino-acid Letters Plotted Helically As Backbone-Embedded Text In 3D.

The package splits into three kinds of module, and the split is deliberate:

  * Pure science, no heavy dependencies -- geometry, language_model, accessibility,
    sequences. Numpy and Biopython only. These carry every equation in the app and
    are unit-tested directly, with no model and no network.
  * Infrastructure -- cache, jobs. SQLite and threads.
  * Backends -- folding. The one place that talks to a GPU, wherever it lives.

The point of that first group is that the maths does not move when the compute
does. ESMFold runs on a Hugging Face ZeroGPU Space rather than the droplet (see
docs/ARCHITECTURE.md for why), and it returns nothing but coordinates and a
probability matrix. Every derived quantity -- information content, entropy,
variant scores, relative accessibility, residue frames -- is computed here.
"""

__version__ = "1.0.0"
