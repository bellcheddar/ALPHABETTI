# ALPHABETTI: Build Specification

> **Amino-acid Letters Plotted Helically As Backbone-Embedded Text In 3D**

A Flask web application that renders a protein sequence as a rotatable, three-dimensional
sequence logo. The user supplies a FASTA sequence or a UniProt accession, ESMFold predicts
the structure on the fly, and instead of a conventional cartoon or stick representation the
app draws the **single-letter amino acid codes themselves** in 3D space, positioned and
oriented along the predicted backbone.

This document is the complete build brief. Build it in one pass.

---

## 1. Identity and scope

| Item | Value |
|------|-------|
| App name | ALPHABETTI |
| Backronym | Amino-acid Letters Plotted Helically As Backbone-Embedded Text In 3D |
| Repository | `github.com/bellcheddar/ALPHABETTI` |
| Deployment | `alphabetti.mdeller.com` (Flask, on the mdeller.com droplet) |
| Author | Marc C. Deller, D.Phil. (marc@marcdeller.com) |
| Language | British English throughout (colour, visualise, normalise, behaviour, licence) |

ALPHABETTI is the rendering engine and the always-on base layer. Four tabs sit on top of it,
each re-using the same instanced glyph renderer and differing only in what drives glyph
height, colour and count.

| Tab | Backronym | Drives glyph geometry from |
|-----|-----------|----------------------------|
| **GIBBERISH** | Glyph Interface for Bits, Entropy and Residue Information in Structural Homology | ESM-2 per-position probability distribution, heights in bits |
| **BUMFLUFF** | Buried/Unburied Mapping of Fonts, Letters, Uncovered Faces and Folds | Relative solvent accessible surface area |
| **BALDERDASH** | Bayesian Amino-acid Letter Display of Estimated Residue Deviations And Substitution Hotspots | Variant effect scores, ghost glyphs, ClinVar/gnomAD overlay |
| **FOLDEROL** | Folding Of Letters Displayed En Route, Ordered Linearly | Animated morph from flat 2D logo strip to 3D coordinates |

**Design intent:** this is a serious scientific tool wearing a silly hat. The maths must be
correct and the axes labelled with units, but the experience should be entertaining enough
that people share screenshots of it. Prioritise the visuals.

---

## 2. Architecture

```
Browser (three.js, vanilla ES6, no build step)
        |
        |  JSON over fetch()
        v
Flask + Gunicorn  ---->  Redis + RQ job queue
        |                        |
        |                        v
        |                Worker process
        |                  - ESMFold (structure + pLDDT)
        |                  - ESM-2 650M (logits)
        |                  - FreeSASA (accessibility)
        v
   SQLite cache (keyed on sequence SHA-256)
```

**All compute runs on the droplet.** No calls out to the ESM Atlas API for folding. The
only outbound network calls permitted are UniProt REST (sequence retrieval) and, optionally,
the EBI Proteins API for variant annotation.

### Stack

- **Backend:** Python 3.11, Flask, Gunicorn, RQ, Redis, SQLite
- **ML:** PyTorch, `transformers` (`facebook/esmfold_v1`, `facebook/esm2_t33_650M_UR50D`)
- **Structure:** Biopython, FreeSASA
- **Frontend:** three.js r165+ loaded as ES modules via CDN importmap. Vanilla JavaScript.
  No React, no npm, no bundler, no build step.
- **Reverse proxy:** nginx, Let's Encrypt

---

## 3. Critical performance constraint: read this first

ESMFold is the bottleneck and it dictates the whole user experience. Before writing any
frontend code, benchmark the fold on the target droplet and record the numbers in
`BENCHMARKS.md`.

- The `esmfold_v1` checkpoint is roughly 2.6 GB of weights. On GPU it wants around 16 GB
  VRAM for a 400-residue chain. CPU-only inference needs a great deal of RAM and will take
  minutes per sequence.
- On CPU, apply the standard memory mitigations:
  ```python
  model.esm = model.esm.float()          # keep ESM trunk in fp32 on CPU
  model.trunk.set_chunk_size(64)         # chunked attention, lower peak memory
  torch.set_num_threads(os.cpu_count())
  ```
- On GPU, use `model.esm = model.esm.half()` and skip the chunking unless memory is tight.

**Hard caps and behaviour:**

- Maximum sequence length: **400 residues**. Reject longer input with a clear message
  offering to truncate, and show the user exactly which range would be kept.
- Minimum length: 10 residues.
- Cache aggressively. Key the cache on `sha256(uppercase_sequence)`. A repeat request must
  return in well under a second and must skip the queue entirely.
- If a fold exceeds a configurable timeout (default 900 s), fail the job cleanly with a
  useful message rather than hanging.

ESM-2 650M is cheap by comparison: around 2.5 GB of weights and a single forward pass takes
seconds even on CPU. GIBBERISH and BALDERDASH are therefore nearly free once the structure
exists.

---

## 4. Repository layout

```
ALPHABETTI/
├── app.py                     # Flask application factory, routes
├── config.py                  # Config classes, env var loading
├── worker.py                  # RQ worker entrypoint
├── requirements.txt
├── README.md                  # House standard (see section 12)
├── BENCHMARKS.md              # Recorded fold timings on the droplet
├── .env.example
├── alphabetti/
│   ├── __init__.py
│   ├── folding.py             # ESMFold wrapper, model singleton, pLDDT extraction
│   ├── language_model.py      # ESM-2 logits, entropy, variant effect scores
│   ├── geometry.py            # Cα/Cβ frames, virtual Cβ for Gly, orientation matrices
│   ├── accessibility.py       # FreeSASA wrapper, relative SASA via Tien et al. maxima
│   ├── sequences.py           # FASTA parsing, UniProt fetch, validation
│   ├── variants.py            # ClinVar/gnomAD overlay via EBI Proteins API
│   ├── cache.py               # SQLite cache layer
│   └── jobs.py                # RQ job definitions, status reporting
├── static/
│   ├── css/alphabetti.css     # Brand theme (section 10)
│   ├── fonts/baloo2-bold.typeface.json
│   ├── js/
│   │   ├── main.js            # App bootstrap, tab routing, state
│   │   ├── renderer.js        # three.js scene, camera, controls, lighting
│   │   ├── glyphs.js          # Glyph geometry cache, InstancedMesh management
│   │   ├── orientation.js     # Per-residue frame maths (mirrors geometry.py)
│   │   ├── modes/gibberish.js
│   │   ├── modes/bumfluff.js
│   │   ├── modes/balderdash.js
│   │   ├── modes/folderol.js
│   │   ├── exporters.js       # GLB, STL, PNG, GIF
│   │   └── ui.js              # Controls, tooltips, sequence ruler, loading states
│   └── img/                   # App icon, favicon, OG image
├── templates/
│   ├── base.html
│   ├── index.html
│   └── about.html
├── examples/                  # Preloaded demo payloads, cached JSON
│   ├── ubiquitin.json
│   ├── gfp.json
│   └── lysozyme.json
└── deploy/
    ├── alphabetti.service     # systemd unit, Gunicorn
    ├── alphabetti-worker.service
    └── nginx.conf
```

---

## 5. Backend specification

### 5.1 Routes

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Main application page |
| POST | `/api/submit` | Accept sequence or UniProt ID, return `job_id` (or cached result immediately) |
| GET | `/api/status/<job_id>` | Poll job state, queue position, elapsed time, stage label |
| GET | `/api/result/<job_id>` | Full result payload (section 5.2) |
| GET | `/api/example/<name>` | Preloaded example, served from cache, no queue |
| GET | `/api/uniprot/<accession>` | Resolve accession to sequence and metadata |
| GET | `/api/variants/<accession>` | ClinVar/gnomAD annotations for BALDERDASH |
| GET | `/healthz` | Liveness, model load state, queue depth |

### 5.2 Result payload

One JSON object serves all four tabs. Compute everything once, ship it once, switch tabs
client side with no further round trips.

```jsonc
{
  "id": "sha256...",
  "source": { "type": "uniprot", "accession": "P0CG48", "name": "Polyubiquitin-C" },
  "sequence": "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG",
  "length": 76,
  "truncated": false,
  "residues": [
    {
      "i": 0,                          // zero-based index
      "resnum": 1,                     // one-based residue number
      "aa": "M",
      "ca": [12.34, 5.67, -8.90],      // Å
      "cb": [13.01, 6.88, -9.42],      // virtual Cβ for Gly
      "n":  [11.20, 4.98, -9.55],
      "c":  [13.44, 4.81, -8.12],
      "plddt": 92.4,                   // 0-100
      "rsa": 0.42,                     // relative SASA, 0-1
      "sasa": 78.3,                    // Å²
      "ss": "H",                       // H/E/C from DSSP-equivalent
      "bits": 3.81,                    // information content, max log2(20) = 4.322
      "entropy": 0.51,                 // Shannon entropy, bits
      "probs": { "M": 0.87, "L": 0.06, "V": 0.03, "I": 0.02, "K": 0.01 },
      "variant_scores": { "A": -4.2, "C": -6.1, "...": 0.0 },
      "clinvar": [
        { "mut": "T", "significance": "pathogenic", "rsid": "rs..." }
      ]
    }
  ],
  "stats": {
    "mean_plddt": 88.1,
    "fold_seconds": 47.2,
    "lm_seconds": 3.1,
    "cached": false
  }
}
```

Keep `probs` to the top 6 amino acids per position to control payload size. A 400-residue
protein must serialise to well under 5 MB. Gzip the response.

### 5.3 Folding (`folding.py`)

- Load `EsmForProteinFolding` once as a module-level singleton in the worker process, never
  per request. The Flask web process must not load the model at all.
- Output PDB text, then parse with Biopython to extract N, Cα, C, Cβ coordinates.
- pLDDT arrives in the B-factor column of the ESMFold PDB output. Values may be on a 0 to 1
  scale depending on the code path used, so normalise explicitly to 0 to 100 and assert the
  range.
- Store the raw PDB text in the cache. Users will want to download it, and future features
  will want it.

### 5.4 Geometry (`geometry.py`)

The single most important function in the backend. Each residue needs an orthonormal frame
so its glyph can be placed and rotated correctly.

```
up      = normalise(CB - CA)                     # letter "up" points along the side chain
tangent = normalise(CA[i+1] - CA[i-1])           # local backbone direction
right   = normalise(cross(up, tangent))
facing  = cross(right, up)                       # glyph plane normal, faces outward
```

- Rebuild `up` as orthogonal to `tangent` if the two are near parallel (guard against a
  degenerate cross product; fall back to an arbitrary perpendicular).
- **Glycine has no Cβ.** Construct a virtual Cβ from N, Cα and C using the standard
  tetrahedral geometry, then use it exactly as for any other residue. Do not skip glycines.
- Handle chain termini: for `i = 0` use `CA[1] - CA[0]`, for the final residue use
  `CA[n-1] - CA[n-2]`.
- Return a 3x3 rotation matrix (or quaternion) per residue. Mirror this logic exactly in
  `static/js/orientation.js` so client-side recomputation during animation stays consistent.

### 5.5 Language model (`language_model.py`)

- Model: `facebook/esm2_t33_650M_UR50D`.
- **Default: wild-type marginals.** One forward pass over the unmasked sequence, take the
  softmax over the 20 canonical amino acids at each position. Fast, and adequate for the
  visualisation.
- **Optional: masked marginals.** L forward passes, one per masked position. Far more
  faithful but L times slower. Expose it as an explicit "high fidelity" toggle with an
  honest time estimate, batched where memory allows.
- Restrict the softmax to the 20 canonical residues. Exclude special tokens, X, B, Z, U, O.
- Information content per position, standard sequence logo convention:
  ```
  H_i = -Σ p_a · log2(p_a)
  R_i = log2(20) - H_i          # max 4.322 bits
  height_a = p_a · R_i          # per-letter height, bits
  ```
- Variant effect score, ESM-1v convention:
  ```
  score(wt → mut) = log p(mut) - log p(wt)
  ```
  Negative means deleterious. Compute the full 20 x L matrix; it costs nothing extra.

### 5.6 Accessibility (`accessibility.py`)

- FreeSASA on the predicted structure, default probe radius 1.4 Å.
- Relative solvent accessibility = residue SASA divided by the theoretical maximum for that
  amino acid type. Use the Tien et al. (2013) theoretical maxima and cite them in the UI.
  Hard-code the table with a comment naming the source.
- Also derive secondary structure (H / E / C) for colouring. Use Biopython DSSP if `mkdssp`
  is available on the droplet, otherwise fall back to a P-SEA style geometric assignment
  from Cα positions so the app never hard-fails on a missing binary.

### 5.7 Input handling (`sequences.py`)

- Accept: raw sequence, single-record FASTA, multi-record FASTA (take the first record and
  say so), UniProt accession (`P0CG48`), UniProt entry name (`UBC_HUMAN`).
- Detect the input type automatically. Do not make the user pick from a dropdown.
- Uppercase, strip whitespace and digits, validate against the 20 canonical letters plus X.
- Reject anything else with a message naming the offending character and its position.
- UniProt fetch via `https://rest.uniprot.org/uniprotkb/{accession}.json`, with a timeout,
  a retry, and a clear error if the accession does not exist.

### 5.8 Queue and caching

- RQ with Redis. One worker, `--burst` disabled, running under its own systemd unit.
- Job stages reported back to the client as human-readable labels: `queued`,
  `fetching sequence`, `folding (this is the slow bit)`, `computing entropy`,
  `measuring accessibility`, `done`.
- SQLite cache table: `id TEXT PRIMARY KEY, payload BLOB, pdb TEXT, created_at, hits INTEGER`.
  Gzip the payload before storing.
- Cache lookup happens in the **web** process before enqueuing, so a hit never touches Redis.
- Pre-warm the cache at deploy time with the three examples.

---

## 6. Frontend: the ALPHABETTI renderer

This is the heart of the app. Spend the effort here.

### 6.1 Glyph geometry (`glyphs.js`)

- Convert Baloo 2 Bold to `typeface.json` (facetype.js) and commit it to
  `static/fonts/`. Baloo 2 is the house display face and the letterforms are chunky enough
  to stay legible when small and rotated.
- Build 20 `ExtrudeGeometry` objects once at startup, one per amino acid, depth ~0.35 Å,
  slight bevel. Centre each geometry on its own bounding box so scaling is symmetric about
  the anchor point.
- Render with **one `InstancedMesh` per amino acid type**, not one mesh per residue. A
  400-residue protein in GIBBERISH mode with 5 stacked letters per position is ~2000
  instances spread across 20 instanced meshes. That is comfortable on a mobile GPU.
- Per-instance colour via `instanceColor`. Per-instance transform via `setMatrixAt` with a
  composed position/quaternion/scale matrix.
- Material: `MeshStandardMaterial`, low roughness, slight metalness. Letters should catch
  the light as they rotate. That specular glint is most of the appeal.

### 6.2 Scene (`renderer.js`)

- Perspective camera, `OrbitControls` with damping, auto-rotate toggle (default on, stops
  on first user interaction).
- Three-point lighting: key directional, softer fill, rim light to separate glyphs from the
  background. Add a subtle hemisphere light so the underside of letters is never pure black.
- Background: soft vertical gradient using the brand palette, not flat white and not a
  default three.js grey.
- Optional ghost backbone: a thin, low-opacity `TubeGeometry` through the Cα positions so
  the fold reads even when glyphs are sparse. Toggle, default on at 15% opacity.
- Fog at distance to give depth cues on long chains.
- `devicePixelRatio` capped at 2. Resize observer on the container.
- Raycast on pointer move for the hover tooltip. Throttle to ~30 Hz.

### 6.3 Shared controls (all tabs)

- Colour scheme selector: **Chemistry** (Taylor), **Clustal**, **pLDDT** (blue-to-orange
  confidence ramp), **Secondary structure**, **Hydrophobicity** (Kyte-Doolittle).
- Glyph scale slider, global multiplier.
- Ghost backbone opacity.
- Auto-rotate speed.
- Sequence ruler along the bottom: the linear sequence, one character per residue, coloured
  by the active scheme, horizontally scrollable. Hovering a residue in 3D highlights it in
  the ruler and vice versa. Clicking flies the camera to that residue.
- Info panel: mean pLDDT, length, source, fold time, cache status.
- Screenshot button (PNG, transparent background option).

---

## 7. Tab specifications

### 7.1 GIBBERISH (default tab, the headline feature)

The actual 3D sequence logo, and the scientifically novel part: evolutionary constraint
mapped onto predicted structure with **no multiple sequence alignment anywhere in the
pipeline**. Say that explicitly in the UI, it is the selling point.

- At each position, stack the top N amino acids (N configurable 1 to 6, default 4) by
  probability, tallest at the bottom, ascending along the `up` vector.
- Letter height = `p_a · R_i` bits, converted to Ångströms by a configurable scale
  (default 1 bit = 0.9 Å, giving a maximum stack of ~3.9 Å).
- Cumulative offset: each letter sits on top of the one below, so the stack total equals the
  position's information content.
- Controls: stack depth N, bits-per-Å scale, minimum bit threshold slider (hides noise),
  bits versus raw probability toggle, high-fidelity masked-marginal toggle.
- Colour by amino acid chemistry by default here, since the point is comparing residue
  identities.
- Legend showing the bits scale with a labelled reference bar, exactly as a 2D logo would.

**Expected visual:** conserved buried core positions become tall single-letter towers,
tolerant surface positions splay into short scruffy stacks. Helices become legible spiral
staircases. This should be genuinely beautiful.

### 7.2 BUMFLUFF

Solvent accessibility mode. Single letter per position, height driven by relative SASA.

- Height = `rsa` scaled by a slider, so buried residues shrink to nubs and exposed ones
  bristle outward. The protein ends up looking like a hairy tribble made of vowels.
- Colour ramp: buried (deep blue) to exposed (amber), diverging at RSA 0.25 which is the
  conventional buried/exposed cut-off. Label the cut-off in the legend.
- Extra toggle: **hydrophobic patch highlight**, which flags exposed residues with
  Kyte-Doolittle > 1.8 in orange. Directly useful for spotting aggregation-prone faces,
  crystallisation-hostile surfaces and candidate epitopes.
- Sidebar readout: total SASA in Å², percentage buried, count of exposed hydrophobics.

### 7.3 BALDERDASH

Variant mode.

- Wild-type glyph at full opacity, plus **ghost glyphs** beneath at 35% opacity showing the
  residues the model would have preferred, ranked by probability.
- Positions where the wild-type sits low in the model distribution glow red. Use an emissive
  material driven by `-log p(wt)` so hotspots genuinely appear to light up.
- If a UniProt accession was supplied, fetch annotations from the EBI Proteins API and add a
  second glyph rank for reported variants, colour-coded: pathogenic (red), benign (green),
  uncertain (grey). Show the rsID in the tooltip.
- Substitution matrix panel: a 20 x L heatmap of `log p(mut) - log p(wt)`, clickable, which
  flies the camera to the selected position.
- Be explicit in the UI that these are language model scores, not clinical predictions. One
  clear line, not a wall of disclaimer.

### 7.4 FOLDEROL

The animation, and the thing most likely to get shared.

- Frame 0: a flat 2D sequence logo strip, letters in a line along the x axis, exactly as
  WebLogo would draw it.
- Frame 1: full 3D ALPHABETTI/GIBBERISH arrangement.
- Interpolate position with an eased lerp and orientation with a quaternion slerp. Stagger
  the per-residue start times slightly (residue index times a small delay) so the fold
  ripples along the chain rather than snapping all at once. That stagger is what makes it
  look deliberate instead of jittery.
- Scrubbable timeline, play/pause, loop toggle, speed control.
- Camera dollies out as the structure forms.
- Exports:
  - **GLB** via `GLTFExporter` (for Blender, three.js embeds, AR viewers)
  - **STL** via `STLExporter` (for 3D printing; merge instanced meshes first and warn if
    the geometry is not manifold)
  - **PNG** at 1x, 2x, 4x
  - **Animated GIF** via `gif.js`, capturing canvas frames over one loop, with a resolution
    selector because full-size GIFs get large fast

---

## 8. UX requirements

- **Landing state must never be empty.** Load ubiquitin from the pre-warmed cache
  immediately so the first thing anyone sees is a rotating 3D logo, not an input box.
- **Loading states must be entertaining.** ESMFold will grind for minutes. Fill that time:
  animate the glyphs assembling from scattered positions, show a live queue position and
  elapsed timer, and rotate through honest, mildly funny status lines ("folding, this is the
  slow bit", "asking the language model what it would have preferred"). Never show a bare
  spinner.
- Example buttons for ubiquitin, GFP and lysozyme, served instantly from cache.
- Mobile: touch orbit and pinch zoom must work properly. Collapse the control panel into a
  bottom sheet below 768 px. Test on a real phone.
- Empty and error states must say something useful and suggest the next action.
- Download buttons for the predicted PDB, the result JSON, and a CSV of per-residue metrics
  (resnum, aa, pLDDT, RSA, bits, entropy).
- An About page explaining the method, listing the models used with versions, and stating
  the limitations plainly: single chain only, no ligands, no assemblies, ESMFold confidence
  degrades on disordered and low-homology sequences.

---

## 9. Scientific correctness checklist

- [ ] pLDDT explicitly normalised to 0 to 100 and asserted in range
- [ ] Information content uses log base 2, maximum stated as 4.322 bits
- [ ] Small-sample entropy correction deliberately **not** applied (this is a model
      distribution, not an alignment count) and that decision noted in a comment
- [ ] Relative SASA uses Tien et al. (2013) theoretical maxima, cited in the UI
- [ ] Glycine virtual Cβ constructed, never skipped
- [ ] Chain termini handled without index errors
- [ ] Variant scores are log-ratios, sign convention documented (negative = deleterious)
- [ ] All lengths in Ångströms and labelled as such
- [ ] Colour scales appropriate: sequential for magnitude, diverging for deviation
- [ ] Model names and versions displayed in the UI and the About page

---

## 10. Brand theme

Apply the marcdeller.com design system. Server-rendered pages use the standard sticky brand
header and footer.

```css
:root {
  --md-primary:       #1e73be;
  --md-primary-dark:  #155a9c;
  --md-primary-light: #4a9fd4;
  --md-bg:            #ffffff;
  --md-bg-alt:        #f4f7fb;
  --md-surface:       #ffffff;
  --md-border:        #dde4ed;
  --md-text:          #1a1a2e;
  --md-text-muted:    #6b7c93;
  --md-text-light:    #ffffff;
  --md-accent-green:  #00d084;
  --md-accent-orange: #ff6900;
  --md-accent-purple: #9b51e0;
  --md-accent-amber:  #fcb900;
  --md-shadow-sm:     6px 6px 9px rgba(0,0,0,0.12);
  --md-shadow-md:     12px 12px 50px rgba(0,0,0,0.18);
  --md-radius:        8px;
  --md-radius-lg:     16px;
}
```

- Body text: Inter. Monospace: Roboto Mono. 3D glyphs: Baloo 2 Bold.
- Header: sticky, `linear-gradient(135deg, var(--md-primary-dark), var(--md-primary))`,
  amber logo dot, brand name linking to marcdeller.com, contact link to marc@marcdeller.com.
  Hide the logo text below 600 px.
- Footer: "Built by Marc C. Deller, D.Phil." with site and email links.
- Consider a dark mode for the 3D canvas specifically. Glowing letters against dark navy
  will look considerably better than against white, particularly for BALDERDASH hotspots.
  Make it a toggle and default the canvas to dark while the surrounding chrome stays light.

---

## 11. Deployment

- Gunicorn, 2 web workers, 120 s timeout, bound to a unix socket.
- Separate systemd unit for the RQ worker. The worker holds the models; the web process
  must stay light.
- nginx reverse proxy on `alphabetti.mdeller.com`, Let's Encrypt via certbot, gzip on for
  JSON responses.
- Both systemd units set to restart on failure with a sane backoff.
- `/healthz` reports model load state and queue depth for monitoring.
- Set `HF_HOME` to a persistent path so model weights survive restarts and are not
  re-downloaded.
- `.env.example` documenting every environment variable. No secrets committed.
- Log fold timings to `BENCHMARKS.md` during the first week so the length cap can be tuned
  against real numbers rather than a guess.

---

## 12. Deliverables

1. Working Flask application, deployed and reachable at `alphabetti.mdeller.com`
2. All four tabs functional against a single computed payload
3. Three pre-warmed examples loading instantly
4. `README.md` to the house GitHub standard: H1 with leading emoji, bold blockquote tagline,
   shields.io badge row ending with the navy author badge, branding contact table, an intro
   paragraph opening "Why it matters:" and closing with an "It is useful for ...:" clause,
   option and output tables, section emoji, author footer. Site palette (navy `#1C244B`,
   accent `#467FF7`), British English, no em dashes, `rel="noopener noreferrer"` on every
   `target="_blank"`.
5. `BENCHMARKS.md` with real fold timings from the droplet
6. App icon in house style (300 x 300 SVG plus PNG, flat navy-outlined cartoon iconography,
   monochromatic blue palette, Baloo 2 wordmark outlined to paths) plus favicon and an
   Open Graph image

---

## 13. Acceptance criteria

- Paste the ubiquitin sequence, get a rotating 3D logo. Total time from submit to render on
  a cache miss is within the benchmarked fold time plus 5 seconds.
- Enter `P0CG48`, get the same result via the UniProt path.
- Submit the same sequence twice; the second submission renders in under one second and the
  UI says it came from cache.
- All four tabs switch instantly with no network round trip.
- GIBBERISH stack heights sum to the position's information content, verifiable against a
  hand-calculated example in a unit test.
- Glycine positions render glyphs correctly oriented.
- A 400-residue protein maintains 30 fps or better on a mid-range laptop and stays
  interactive on a modern phone.
- GLB and STL exports open correctly in Blender.
- Every error path shows a useful message. Nothing ever renders a blank canvas.

---

*Specification prepared for Claude Code. Build ALPHABETTI as the engine, GIBBERISH as the
headline, and make the whole thing look good enough that people share it.*
