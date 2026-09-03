"""ALPHABETTI: Flask application factory and routes.

The web process is deliberately light. It never imports torch, never loads a
model and never folds anything itself; it parses input, serves the cache, and
hands genuine misses to a small thread pool that waits on the GPU service.
That is what lets it share a 3.9 GB droplet with eight other applications.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from flask import (Flask, Response, jsonify, render_template, request,
                   send_from_directory)

from alphabetti import __version__
from alphabetti.cache import Cache, sequence_id
from alphabetti.folding import make_backend
from alphabetti.jobs import JobRunner
from alphabetti.sequences import SequenceError, parse_input
from config import Config, get_config

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
# The three examples named in the build spec. Loaded into the cache at start-up
# so the landing state is never empty and the buttons never queue.
# Four examples, chosen to span the fold classes rather than to be famous:
# a beta-grasp, a mixed alpha/beta, an all-alpha, and a beta barrel.
#
# The fourth is there for a different reason. ESM-2 650M has almost no
# evolutionary signal for avGFP: masked marginals recover the wild-type residue
# at 10 % of positions against 8 % for a shuffled control, and mean information
# content is 0.27 of a possible 4.322 bits. ESMFold's trunk IS ESM-2, so the
# structure degrades with it (mean pLDDT 43). It is the clearest possible
# demonstration of the method's limits, and showing it is more honest than
# picking four proteins that all flatter the model.
EXAMPLES = {
    "ubiquitin": {
        "label": "Ubiquitin", "accession": "P0CG48", "residues": (1, 76),
        "note": "Beta-grasp fold. The monomer, not the polyubiquitin precursor.",
    },
    "lysozyme": {
        "label": "Lysozyme", "accession": "P00698",
        "note": "Mixed alpha/beta. Well represented in UniRef, so the logo is rich.",
    },
    "myoglobin": {
        "label": "Myoglobin", "accession": "P02144",
        "note": "All-alpha globin fold. Helices read as spiral staircases.",
    },
    "gfp": {
        "label": "GFP", "accession": "P42212",
        "note": "Beta barrel, and the honest failure case: ESM-2 has almost no "
                "signal for this jellyfish protein, so both the logo and the "
                "structure are poor. Kept deliberately.",
    },
}

STARTED_AT = time.time()


def create_app(config: type[Config] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config or get_config())
    settings = config or get_config()

    cache = Cache(settings.CACHE_PATH)
    backend = make_backend(settings)
    runner = JobRunner(cache, backend, settings)

    app.extensions["alphabetti"] = {
        "cache": cache, "backend": backend, "runner": runner, "settings": settings,
    }

    _load_examples(cache, settings)

    # ---------------------------------------------------------------- pages

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            version=__version__,
            models=settings.MODELS,
            examples=EXAMPLES,
            max_length=settings.MAX_SEQUENCE_LENGTH,
            min_length=settings.MIN_SEQUENCE_LENGTH,
            can_fold=backend.can_fold,
        )

    @app.route("/about")
    def about():
        return render_template(
            "about.html", version=__version__, models=settings.MODELS,
            max_length=settings.MAX_SEQUENCE_LENGTH,
            backend=backend.name,
        )

    # ------------------------------------------------------------------ api

    @app.post("/api/submit")
    def submit():
        """Accept a sequence or accession. Return a cached result or a job id.

        The cache is checked in THIS process, before anything is queued, so a
        repeat request never touches the pool and comes back in one round trip.
        """
        body = request.get_json(silent=True) or {}
        raw = (body.get("input") or "").strip()
        allow_truncation = bool(body.get("truncate"))
        # Masked marginals unless explicitly turned off; see config.py for the
        # measurement behind that default.
        masked = body.get("masked")
        masked = settings.MASKED_MARGINALS_DEFAULT if masked is None else bool(masked)

        try:
            parsed = parse_input(
                raw,
                allow_truncation=allow_truncation,
                max_length=settings.MAX_SEQUENCE_LENGTH,
                min_length=settings.MIN_SEQUENCE_LENGTH,
                fetch=settings.ENABLE_UNIPROT,
            )
        except SequenceError as exc:
            # `too_long` lets the client offer truncation rather than making the
            # user edit and re-paste their own sequence.
            too_long = "cap is" in str(exc)
            return jsonify({
                "error": str(exc),
                "can_truncate": too_long,
                "max_length": settings.MAX_SEQUENCE_LENGTH,
            }), 400

        result_id = sequence_id(parsed.sequence)
        cached = cache.get(result_id)
        if cached is not None:
            cached["notes"] = list(parsed.notes) + cached.get("notes", [])
            return _gzipped(cached, {"cached": True})

        if not backend.can_fold:
            return jsonify({
                "error": (
                    "That protein is not in the cache and this instance cannot "
                    "fold new ones. The examples all work."
                ),
                "can_fold": False,
            }), 503

        job_id = runner.submit(parsed, masked=masked)
        return jsonify({
            "job_id": job_id,
            "cached": False,
            "length": len(parsed.sequence),
            "truncated": parsed.truncated,
            "notes": parsed.notes,
            "source": parsed.as_source(),
        }), 202

    @app.get("/api/status/<job_id>")
    def status(job_id: str):
        state = runner.status(job_id)
        if state is None:
            return jsonify({"error": "No such job. It may have expired."}), 404
        return jsonify(state)

    @app.get("/api/result/<result_id>")
    def result(result_id: str):
        payload = cache.get(result_id)
        if payload is None:
            return jsonify({"error": "No such result."}), 404
        return _gzipped(payload)

    @app.get("/api/example/<name>")
    def example(name: str):
        """Preloaded examples, straight from the cache and never queued."""
        meta = EXAMPLES.get(name)
        if meta is None:
            return jsonify({"error": f"No example called {name!r}."}), 404
        payload = cache.get(meta["id"]) if "id" in meta else None
        if payload is None:
            stored = _example_file(name)
            if stored is None:
                return jsonify({
                    "error": f"The {meta['label']} example has not been pre-warmed "
                             "on this instance yet."
                }), 503
            payload = stored
        return _gzipped(payload, {"example": name})

    @app.get("/api/uniprot/<accession>")
    def uniprot(accession: str):
        if not settings.ENABLE_UNIPROT:
            return jsonify({"error": "UniProt lookup is disabled here."}), 503
        try:
            parsed = parse_input(
                accession, max_length=settings.MAX_SEQUENCE_LENGTH,
                min_length=settings.MIN_SEQUENCE_LENGTH,
            )
        except SequenceError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({
            "sequence": parsed.sequence, "length": len(parsed.sequence),
            "source": parsed.as_source(), "notes": parsed.notes,
            "cached": cache.has(sequence_id(parsed.sequence)),
        })

    @app.get("/api/variants/<accession>")
    def variants(accession: str):
        from alphabetti.variants import fetch_variants, VariantError
        if not settings.ENABLE_VARIANTS:
            return jsonify({"error": "Variant lookup is disabled here."}), 503
        try:
            return jsonify(fetch_variants(accession, settings.EBI_PROTEINS_URL))
        except VariantError as exc:
            return jsonify({"error": str(exc)}), 502

    @app.get("/api/download/<result_id>.<kind>")
    def download(result_id: str, kind: str):
        """The predicted PDB, the payload, or a per-residue CSV."""
        if kind == "pdb":
            pdb = cache.get_pdb(result_id)
            if pdb is None:
                return jsonify({"error": "No such result."}), 404
            return Response(pdb, mimetype="chemical/x-pdb", headers={
                "Content-Disposition": f'attachment; filename="alphabetti_{result_id[:8]}.pdb"'
            })

        payload = cache.get(result_id)
        if payload is None:
            return jsonify({"error": "No such result."}), 404

        if kind == "json":
            return Response(
                json.dumps(payload, indent=2), mimetype="application/json",
                headers={"Content-Disposition":
                         f'attachment; filename="alphabetti_{result_id[:8]}.json"'})

        if kind == "csv":
            lines = ["resnum,aa,plddt,rsa,sasa_A2,ss,bits,entropy,surprise"]
            for r in payload["residues"]:
                lines.append(
                    f"{r['resnum']},{r['aa']},{r['plddt']},{r['rsa']},{r['sasa']},"
                    f"{r['ss']},{r['bits']},{r['entropy']},{r['surprise']}"
                )
            return Response("\n".join(lines) + "\n", mimetype="text/csv", headers={
                "Content-Disposition":
                    f'attachment; filename="alphabetti_{result_id[:8]}.csv"'})

        return jsonify({"error": f"Unknown format {kind!r}. Use pdb, json or csv."}), 400

    @app.get("/healthz")
    def healthz():
        return jsonify({
            "ok": True,
            "version": __version__,
            "uptime_seconds": round(time.time() - STARTED_AT, 1),
            "queue_depth": cache.queue_depth(),
            "cache": cache.stats(),
            "fold": backend.health(),
            "examples_warm": [n for n in EXAMPLES if _example_warm(cache, n)],
        })

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return jsonify({"error": f"No route {request.path}."}), 404
        return render_template("404.html"), 404

    # ------------------------------------------------------------- helpers

    def _example_file(name: str) -> dict | None:
        path = EXAMPLES_DIR / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def _example_warm(cache_: Cache, name: str) -> bool:
        meta = EXAMPLES.get(name, {})
        return bool(meta.get("id")) and cache_.has(meta["id"])

    return app


def _gzipped(payload: dict, extra: dict | None = None) -> Response:
    """JSON response. nginx does the compression; this just avoids Flask's
    pretty-printing, which adds about 30 % to a payload nobody reads by eye."""
    if extra:
        payload = {**payload, **extra}
    return Response(
        json.dumps(payload, separators=(",", ":")),
        mimetype="application/json",
    )


def _load_examples(cache: Cache, settings) -> None:
    """Warm the cache from examples/*.json at start-up.

    Section 8 of the spec: the landing state must never be empty. These are
    committed to the repository as computed payloads precisely so that a fresh
    deployment shows a rotating protein before it has ever reached a GPU.
    """
    for name, meta in EXAMPLES.items():
        path = EXAMPLES_DIR / f"{name}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
            pdb_path = EXAMPLES_DIR / f"{name}.pdb"
            result_id = payload.get("id") or sequence_id(payload["sequence"])
            meta["id"] = result_id
            settings.PROTECTED_IDS.add(result_id)
            if not cache.has(result_id):
                cache.put(result_id, payload["sequence"], payload,
                          pdb_path.read_text() if pdb_path.exists() else "",
                          protected=True)
        except Exception:
            # A malformed example must not stop the app from starting: the
            # landing state degrades, everything else still works.
            continue


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=8006)
