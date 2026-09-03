# Third-party software and data

ALPHABETTI is MIT licensed. It depends on the following, whose licences and
citations are reproduced here.

---

## WebLogo 3

The **HOGWASH** tab is a presentation layer over WebLogo. Every number it shows
comes from WebLogo's own code, unmodified: the column counts, the composition
priors and pseudocounts, the small-sample correction, the unit conversions, the
colour schemes and the rendered EPS/PDF/PNG output. ALPHABETTI adds the input
plumbing, a 3D rendering of the same `LogoData`, and nothing else.

| | |
|---|---|
| **Source** | [github.com/gecrooks/weblogo](https://github.com/gecrooks/weblogo) |
| **Version used** | 3.9.0 (from PyPI, unmodified) |
| **Licence** | MIT |
| **Maintainer** | Gavin E. Crooks |

### Please cite

> Crooks GE, Hon G, Chandonia JM, Brenner SE (2004).
> **WebLogo: a sequence logo generator.**
> *Genome Research* **14**(6):1188-1190.
> doi:[10.1101/gr.849004](https://doi.org/10.1101/gr.849004)
> · [PMC419797](https://pmc.ncbi.nlm.nih.gov/articles/PMC419797/)

And the paper that defined the sequence logo itself:

> Schneider TD, Stephens RM (1990).
> **Sequence logos: a new way to display consensus sequences.**
> *Nucleic Acids Research* **18**(20):6097-6100.
> doi:[10.1093/nar/18.20.6097](https://doi.org/10.1093/nar/18.20.6097)

### Licence text

```
The MIT Open Source License
===========================

Parts of the code are covered by the MIT license. Please refer to
individual source code files for further details.

Copyright (c) 2003-2004 The Regents of the University of California.
Copyright (c) 2005 Gavin E. Crooks
Copyright (c) 2006 David Ding
Copyright (c) 2006 Clare Gollnick

This software is distributed under the MIT Open Source License.
<http://www.opensource.org/licenses/mit-license.html>

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
IN THE SOFTWARE.
```

### Bundled example alignments

`examples/alignments/*.fa` are WebLogo's own example files, copied unmodified
from `weblogo/htdocs/examples/` and covered by the same MIT licence. They are
included so the tab has something to show when the InterPro API is slow or down,
which it measurably is.

| File | Contents |
|---|---|
| `globins.fa` | Globin family, 56 sequences. Pairs with the myoglobin structure. |
| `cap_hth.fa` | CAP helix-turn-helix, 101 sequences |
| `cap_dna.fa` | CAP binding sites, 49 DNA sequences |
| `hth.fa` | Helix-turn-helix motif, 30 sequences |
| `lexa.fa` | LexA binding sites, 19 DNA sequences |

---

## StageCamera

`static/js/StageCamera.js` is copied unmodified from Marc Deller's own ButtFold,
which ported it from PhoneFold's `StageCamera.swift`. Same author, same MIT
licence as this repository.

---

## Models

| Model | Checkpoint | Reference |
|---|---|---|
| ESMFold | `facebook/esmfold_v1` | Lin Z *et al.* (2023) *Science* **379**:1123-1130 |
| ESM-2 650M | `facebook/esm2_t33_650M_UR50D` | Lin Z *et al.* (2023) *Science* **379**:1123-1130 |

Both are used through Hugging Face `transformers`, under the terms of their
respective model licences, and run on a Hugging Face ZeroGPU Space rather than
being redistributed here.

---

## Libraries

| Library | Licence | Used for |
|---|---|---|
| [three.js](https://threejs.org) | MIT | the 3D renderer |
| [Biopython](https://biopython.org) | Biopython License (BSD-like) | PDB parsing, Shrake-Rupley SASA |
| [NumPy](https://numpy.org) | BSD-3-Clause | all the array maths |
| [SciPy](https://scipy.org) | BSD-3-Clause | required by WebLogo |
| [Flask](https://flask.palletsprojects.com) | BSD-3-Clause | the web application |
| [gif.js](https://github.com/jnordberg/gif.js) | MIT | animated GIF export |
| [Baloo 2](https://fonts.google.com/specimen/Baloo+2) | SIL Open Font License 1.1 | the 3D letterforms |
| Ghostscript, pdf2svg | AGPL / GPL | invoked as external programs to convert WebLogo's EPS. Not linked, not redistributed. |

Ghostscript and pdf2svg are called as separate processes via `subprocess`, by
WebLogo itself, exactly as the WebLogo command line does. Neither is bundled
with or linked into this software, so their copyleft terms do not extend to it.
