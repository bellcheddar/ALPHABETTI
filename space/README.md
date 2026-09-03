---
title: ALPHABETTI fold service
emoji: 🔤
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.9.1
app_file: app.py
pinned: false
license: mit
short_description: ESMFold + ESM-2 650M behind a JSON API for ALPHABETTI
---

# ALPHABETTI fold service

The GPU half of [ALPHABETTI](https://alphabetti.mdeller.com), which renders a
protein sequence as a rotatable three-dimensional sequence logo.

This Space does two things and nothing else:

| Model | Returns |
|---|---|
| `facebook/esmfold_v1` | backbone coordinates as PDB text, pLDDT in the B-factor column normalised to 0-100 |
| `facebook/esm2_t33_650M_UR50D` | a 20 x L probability matrix over the canonical amino acids |

Information content, entropy, variant effect scores, solvent accessibility,
secondary structure and the per-residue orientation frames are all computed by
the calling application, not here. That keeps the science in a repository where
it can be unit-tested without a GPU, and keeps this Space replaceable.

It exists because the droplet that serves the app has 3.9 GB of RAM and the
`esmfold_v1` checkpoint alone is 7.9 GB of fp32 weights.

## API

```bash
curl -X POST https://dellboy-alphabetti-fold.hf.space/gradio_api/call/fold \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": ["MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG", false]}'
```

Send the bearer token on the follow-up GET as well as the POST. The anonymous
ZeroGPU quota is a handful of GPU-seconds and it runs out with an error that
does not mention quota.

Maximum 400 residues, minimum 10.
