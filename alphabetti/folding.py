"""Fold backends: the one place in the app that needs a GPU.

Each backend takes a sequence and returns exactly two things -- backbone
coordinates as PDB text, and a 20 x L probability matrix from ESM-2. Everything
else the app displays is derived from those downstream, in modules that need
neither torch nor a network. That split is what lets the compute move without
the science moving with it.

See config.py for why the fold does not run on the droplet.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import numpy as np
import requests


class FoldError(RuntimeError):
    """A fold that failed, with a message fit to show a user."""


@dataclass
class FoldResult:
    """What every backend returns, regardless of where it ran."""

    pdb: str
    probs: np.ndarray                    # (L, 20) over ALPHABET order
    fold_seconds: float = 0.0
    lm_seconds: float = 0.0
    backend: str = "unknown"
    device: str = "unknown"
    notes: list[str] = field(default_factory=list)


class FoldBackend:
    """Interface. Subclasses implement fold() and describe themselves."""

    name = "base"
    #: Whether this backend can fold something not already in the cache.
    can_fold = True

    def fold(self, sequence: str, *, masked: bool = True) -> FoldResult:
        raise NotImplementedError

    def health(self) -> dict:
        return {"backend": self.name, "can_fold": self.can_fold}


# ---------------------------------------------------------------------------
# Production: a Hugging Face ZeroGPU Space
# ---------------------------------------------------------------------------


class HFSpaceBackend(FoldBackend):
    """Calls a Gradio Space that holds ESMFold and ESM-2 on a ZeroGPU slice.

    Two things about this are worth knowing before debugging it:

    The token is effectively mandatory. ZeroGPU's anonymous quota is a handful
    of GPU-seconds per IP, and once it is gone the Space returns an error that
    says nothing about quota. The Authorization header goes on the GET as well
    as the POST, because Gradio's queue is two calls and the second one is
    where the work is actually awaited.

    A cold Space takes 30 to 60 seconds to wake before it does any folding at
    all, which is most of the wait on the first request of the day. That is why
    the job's stage labels distinguish waking from folding: a user watching
    "folding" for a minute assumes it has hung.
    """

    name = "hf_space"

    def __init__(self, space: str, token: str | None = None, timeout: int = 900):
        self.space = space
        self.token = token
        self.timeout = timeout
        # Gradio Spaces are served from a predictable host derived from the id.
        self.base = f"https://{space.replace('/', '-').lower()}.hf.space"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fold(self, sequence: str, *, masked: bool = True) -> FoldResult:
        started = time.time()
        try:
            post = requests.post(
                f"{self.base}/gradio_api/call/fold",
                json={"data": [sequence, bool(masked)]},
                headers=self._headers(),
                timeout=60,
            )
            post.raise_for_status()
            event_id = post.json().get("event_id")
            if not event_id:
                raise FoldError("The folding service accepted the job but returned no id.")

            # The result arrives as a server-sent event stream. Auth goes on
            # this request too; without it the stream 401s after the POST has
            # already succeeded, which reads like a Space bug rather than a
            # missing header.
            stream = requests.get(
                f"{self.base}/gradio_api/call/fold/{event_id}",
                headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
                timeout=self.timeout,
                stream=True,
            )
            stream.raise_for_status()

            payload = None
            for raw in stream.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("event: error"):
                    raise FoldError("The folding service reported an error.")
                if raw.startswith("data: "):
                    body = raw[6:]
                    if body in {"null", ""}:
                        continue
                    parsed = json.loads(body)
                    # Gradio wraps a single return value in a list.
                    payload = parsed[0] if isinstance(parsed, list) and parsed else parsed
            if payload is None:
                raise FoldError("The folding service returned nothing.")

        except FoldError:
            raise
        except requests.Timeout as exc:
            raise FoldError(
                f"The fold took longer than {self.timeout} s and was given up on."
            ) from exc
        except Exception as exc:
            raise FoldError(
                f"Could not reach the folding service ({exc.__class__.__name__})."
            ) from exc

        # A dict is the success shape; anything else means the Space raised and
        # Gradio serialised the message instead.
        if not isinstance(payload, dict) or "pdb" not in payload:
            message = payload.get("error") if isinstance(payload, dict) else str(payload)
            raise FoldError(f"The folding service failed: {message}")

        probs = np.asarray(payload["probs"], dtype=np.float64)
        return FoldResult(
            pdb=payload["pdb"],
            probs=probs,
            fold_seconds=float(payload.get("fold_seconds", 0.0)),
            lm_seconds=float(payload.get("lm_seconds", 0.0)),
            backend=self.name,
            device=payload.get("device", "zerogpu"),
            notes=payload.get("notes", []) + [f"Folded on {self.space} (ZeroGPU)."],
        )

    def health(self) -> dict:
        info = {"backend": self.name, "space": self.space, "can_fold": True,
                "authenticated": bool(self.token)}
        try:
            response = requests.get(f"{self.base}/", timeout=6,
                                    headers=self._headers())
            info["reachable"] = response.status_code < 500
            info["status_code"] = response.status_code
        except Exception as exc:
            info["reachable"] = False
            info["error"] = exc.__class__.__name__
        return info


# ---------------------------------------------------------------------------
# Bulk pre-warming: torch in this process
# ---------------------------------------------------------------------------


class LocalTorchBackend(FoldBackend):
    """ESMFold and ESM-2 loaded here. Needs roughly 16 GB and the weights.

    Not for the droplet. This exists so a machine that has both checkpoints can
    build the example cache offline, and so the app is not permanently welded
    to one hosting provider.

    Imports are deliberately inside the methods: importing torch costs seconds
    and a great deal of memory, and the web process must never pay that just
    because this class is defined.
    """

    name = "local"

    def __init__(self, device: str | None = None, chunk_size: int = 64):
        self.device = device
        self.chunk_size = chunk_size
        self._folder = None
        self._tokeniser = None
        self._language = None
        self._lm_tokeniser = None

    def _pick_device(self):
        import torch

        if self.device:
            return torch.device(self.device)
        if torch.cuda.is_available():
            return torch.device("cuda")
        # MPS is not used for ESMFold: its attention path has repeatedly given
        # wrong results rather than errors on this stack, and a silently wrong
        # structure is worse than a slow one.
        return torch.device("cpu")

    def _load(self):
        if self._folder is not None:
            return
        import torch
        from transformers import AutoTokenizer, EsmForProteinFolding, EsmForMaskedLM

        device = self._pick_device()
        self._tokeniser = AutoTokenizer.from_pretrained("facebook/esmfold_v1")
        folder = EsmForProteinFolding.from_pretrained(
            "facebook/esmfold_v1", low_cpu_mem_usage=True
        )
        folder.eval()

        if device.type == "cuda":
            folder = folder.to(device)
            folder.esm = folder.esm.half()
        else:
            # Build spec section 3: the standard CPU mitigations. Keeping the
            # ESM trunk in fp32 is not optional on CPU -- half precision on CPU
            # is both slower and numerically unreliable -- and chunked attention
            # is what keeps the LxL pair representation inside available memory.
            folder.esm = folder.esm.float()
            folder.trunk.set_chunk_size(self.chunk_size)
            import os
            torch.set_num_threads(os.cpu_count() or 4)

        self._folder = folder
        self._device = device

        self._lm_tokeniser = AutoTokenizer.from_pretrained(
            "facebook/esm2_t33_650M_UR50D"
        )
        language = EsmForMaskedLM.from_pretrained("facebook/esm2_t33_650M_UR50D")
        language.eval()
        self._language = language.to(device) if device.type == "cuda" else language

    def fold(self, sequence: str, *, masked: bool = True) -> FoldResult:
        import torch
        from .language_model import restrict_to_canonical

        self._load()
        notes: list[str] = []

        started = time.time()
        with torch.no_grad():
            tokens = self._tokeniser(
                [sequence], return_tensors="pt", add_special_tokens=False
            )
            tokens = {k: v.to(self._device) for k, v in tokens.items()}
            output = self._folder(**tokens)
            pdb = self._folder.output_to_pdb(output)[0]
        fold_seconds = time.time() - started

        started = time.time()
        with torch.no_grad():
            encoded = self._lm_tokeniser(sequence, return_tensors="pt")
            encoded = {k: v.to(self._language.device) for k, v in encoded.items()}
            logits = self._language(**encoded).logits[0].float().cpu().numpy()
            # Strip the leading <cls> and trailing <eos> that ESM-2 adds.
            logits = logits[1:-1]
            vocabulary = self._lm_tokeniser.convert_ids_to_tokens(
                range(logits.shape[-1])
            )
            probs = restrict_to_canonical(logits, "".join(
                t if len(t) == 1 else "\x00" for t in vocabulary
            ))
        lm_seconds = time.time() - started

        if masked:
            notes.append(
                "Masked marginals are not implemented in the local backend; "
                "these are wild-type marginals and will read as over-confident."
            )

        return FoldResult(
            pdb=pdb, probs=probs, fold_seconds=fold_seconds, lm_seconds=lm_seconds,
            backend=self.name, device=str(self._device), notes=notes,
        )


# ---------------------------------------------------------------------------
# The honest fallback
# ---------------------------------------------------------------------------


class NullBackend(FoldBackend):
    """Serves the cache and refuses everything else, in as many words.

    Not a degraded mode to be ashamed of: with the examples pre-warmed the app
    is still fully usable and every tab still works. It just cannot fold
    anything new, and says so.
    """

    name = "none"
    can_fold = False

    def fold(self, sequence: str, *, masked: bool = True) -> FoldResult:
        raise FoldError(
            "Folding is switched off on this instance, so only proteins that are "
            "already cached can be shown. The examples all work. If you own this "
            "deployment, set ALPHABETTI_FOLD_BACKEND to hf_space."
        )


def make_backend(config) -> FoldBackend:
    """Build the backend named by the configuration."""
    kind = getattr(config, "FOLD_BACKEND", "hf_space")
    if kind == "hf_space":
        return HFSpaceBackend(
            space=config.HF_SPACE,
            token=config.HF_TOKEN,
            timeout=config.FOLD_TIMEOUT_SECONDS,
        )
    if kind == "local":
        return LocalTorchBackend()
    if kind == "none":
        return NullBackend()
    raise ValueError(
        f"Unknown fold backend {kind!r}. Use hf_space, local or none."
    )
