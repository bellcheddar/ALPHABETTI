"""Configuration, all of it overridable by environment variable.

The one setting that matters most is ALPHABETTI_FOLD_BACKEND, because it is
where the compute lives, and that is not where the build spec originally put it.

The spec said "all compute runs on the droplet". The droplet has 3.9 GB of RAM
in total, about 2 GB of it free, two cores, and eight other applications already
resident. The esmfold_v1 checkpoint alone is 7.9 GB of fp32 weights, roughly
4 GB at half precision, and a 400-residue fold needs several more gigabytes on
top for the LxL pair representation. It misses by a factor of four to six, and
no chunk size closes that gap.

So the fold happens on a Hugging Face ZeroGPU Space and everything else happens
here. Three backends implement the same interface:

    hf_space   a ZeroGPU Space holds both models. Roughly 10 s a fold on an
               H200 slice, plus a cold start. This is production.
    local      torch and transformers in this process. For a machine with the
               weights and the memory; used to bulk pre-warm the cache.
    none       no folding at all. Cached results are served, a miss is refused
               with a clear message. The safe fallback if the Space is down.

Everything downstream of the fold -- geometry, entropy, accessibility, the
payload -- is identical whichever backend ran, because the backend returns
coordinates and a probability matrix and nothing else.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- Flask ---------------------------------------------------------
    SECRET_KEY = os.environ.get("ALPHABETTI_SECRET_KEY", "dev-only-not-a-secret")
    JSON_SORT_KEYS = False

    # --- Where the fold happens ----------------------------------------
    FOLD_BACKEND = os.environ.get("ALPHABETTI_FOLD_BACKEND", "hf_space")

    # The ZeroGPU Space that holds ESMFold and ESM-2.
    HF_SPACE = os.environ.get("ALPHABETTI_HF_SPACE", "Dellboy/alphabetti-fold")
    # A token is not optional in practice. The anonymous ZeroGPU quota is small
    # enough that a handful of folds exhausts it, and the failure arrives as a
    # generic error rather than anything mentioning quota. Send Bearer auth on
    # every request, GET as well as POST.
    HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("ALPHABETTI_HF_TOKEN")

    # --- Limits, from build spec section 3 ------------------------------
    MAX_SEQUENCE_LENGTH = _int("ALPHABETTI_MAX_LENGTH", 400)
    MIN_SEQUENCE_LENGTH = _int("ALPHABETTI_MIN_LENGTH", 10)
    # A fold that has not finished in this long has gone wrong. Fail it with a
    # message rather than letting the client poll a dead job forever.
    FOLD_TIMEOUT_SECONDS = _int("ALPHABETTI_FOLD_TIMEOUT", 900)
    # Concurrent folds. ZeroGPU serialises anyway and the quota is per account,
    # so more than a couple of workers buys nothing and burns quota faster.
    MAX_CONCURRENT_FOLDS = _int("ALPHABETTI_MAX_CONCURRENT", 2)
    # Masked marginals by DEFAULT, which is not what the build spec said.
    #
    # The spec made them an opt-in "high fidelity" toggle on the grounds that
    # they cost L forward passes instead of one. That is true, and on a CPU it
    # would be prohibitive. Measured on the ZeroGPU Space with batching, for
    # ubiquitin (L=76):
    #
    #                        wild-type marginals   masked marginals
    #     top-1 == wild type       100 %                80 %
    #     mean information        4.01 bits            3.20 bits
    #     pseudo-perplexity        1.07                 1.78
    #     language model time      0.03 s               0.42 s
    #
    # The first row is the problem. An unmasked forward pass can read the
    # answer off its own input, so it returns the wild-type residue at every
    # single position with near-maximal confidence. GIBBERISH would then be a
    # three-dimensional rendering of the input sequence: every stack a maximal
    # single-letter tower, no conservation signal anywhere. The headline
    # feature would be decorative.
    #
    # The cost of fixing that is 0.42 s here and about 2.2 s extrapolated to a
    # 400-residue chain, against a fold of 1.5 to 12 s. It is not a trade-off
    # at GPU speed; it is just better. Set this False to compare.
    MASKED_MARGINALS_DEFAULT = _flag("ALPHABETTI_MASKED_MARGINALS", True)

    # How many amino acids per position survive into the payload. Six keeps a
    # 400-residue result comfortably inside the 5 MB budget; GIBBERISH renders
    # at most six and defaults to four.
    TOP_K = _int("ALPHABETTI_TOP_K", 6)

    # --- Cache ----------------------------------------------------------
    CACHE_PATH = Path(
        os.environ.get("ALPHABETTI_CACHE", BASE_DIR / "instance" / "alphabetti.sqlite")
    )
    # Examples are pre-warmed at deploy time and must never be evicted: they are
    # the landing state, and an empty landing state is the one thing section 8
    # of the spec forbids outright.
    PROTECTED_IDS: set[str] = set()

    # --- Outbound -------------------------------------------------------
    UNIPROT_TIMEOUT = _float("ALPHABETTI_UNIPROT_TIMEOUT", 12.0)
    ENABLE_UNIPROT = _flag("ALPHABETTI_ENABLE_UNIPROT", True)
    ENABLE_VARIANTS = _flag("ALPHABETTI_ENABLE_VARIANTS", True)
    EBI_PROTEINS_URL = "https://www.ebi.ac.uk/proteins/api/variation/{accession}"

    # --- Model provenance, shown in the UI and the About page ------------
    # Section 9 of the spec requires model names and versions on screen. They
    # live here so the UI cannot drift from what actually ran.
    MODELS = {
        "structure": {
            "name": "ESMFold",
            "checkpoint": "facebook/esmfold_v1",
            "reference": "Lin et al., Science 379:1123 (2023)",
        },
        "language": {
            "name": "ESM-2 650M",
            "checkpoint": "facebook/esm2_t33_650M_UR50D",
            "reference": "Lin et al., Science 379:1123 (2023)",
        },
    }


class ProductionConfig(Config):
    DEBUG = False


class DevelopmentConfig(Config):
    DEBUG = True
    FOLD_BACKEND = os.environ.get("ALPHABETTI_FOLD_BACKEND", "hf_space")


CONFIGS = {"production": ProductionConfig, "development": DevelopmentConfig}


def get_config(name: str | None = None) -> type[Config]:
    name = name or os.environ.get("ALPHABETTI_ENV", "production")
    return CONFIGS.get(name, ProductionConfig)
