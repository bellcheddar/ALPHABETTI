"""ClinVar and gnomAD annotations for BALDERDASH, via the EBI Proteins API.

Optional and non-blocking by design. A protein pasted as a bare sequence has no
accession and therefore no annotations, which is the common case, and BALDERDASH
is fully usable without them: the ghost glyphs and the hotspot glow come from
the language model alone.

These are reported clinical annotations. They are shown alongside language model
scores and are emphatically not the same kind of thing, which the UI states in
one line rather than a wall of disclaimer.
"""

from __future__ import annotations

import requests

# The EBI's significance vocabulary is longer and messier than three colours, so
# it is collapsed here rather than in the front end. Anything unrecognised
# becomes "uncertain", which is the honest default for a term we do not know.
SIGNIFICANCE = {
    "pathogenic": "pathogenic",
    "likely pathogenic": "pathogenic",
    "benign": "benign",
    "likely benign": "benign",
    "uncertain significance": "uncertain",
    "conflicting interpretations of pathogenicity": "uncertain",
    "not provided": "uncertain",
    "drug response": "uncertain",
}


class VariantError(RuntimeError):
    """A lookup that failed, phrased for a user."""


def fetch_variants(accession: str, url_template: str, timeout: float = 12.0) -> dict:
    """Return per-position variant annotations for one accession."""
    try:
        response = requests.get(
            url_template.format(accession=accession.upper()),
            headers={"Accept": "application/json",
                     "User-Agent": "ALPHABETTI/1.0 (marc@marcdeller.com)"},
            timeout=timeout,
        )
        if response.status_code == 404:
            return {"accession": accession, "positions": {}, "count": 0,
                    "note": "No variant record for this accession."}
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise VariantError(
            f"Could not reach the EBI Proteins API ({exc.__class__.__name__}). "
            "BALDERDASH still works; only the clinical overlay is missing."
        ) from exc

    positions: dict[str, list[dict]] = {}
    for feature in data.get("features", []):
        if feature.get("type") != "VARIANT":
            continue
        try:
            position = int(feature.get("begin"))
        except (TypeError, ValueError):
            continue
        mutated = (feature.get("alternativeSequence") or "").strip()
        # Only single-residue substitutions are renderable as a glyph. Deletions,
        # frameshifts and stop-gains are real but have no letter to draw.
        if len(mutated) != 1 or not mutated.isalpha():
            continue

        significance = "uncertain"
        for evidence in feature.get("clinicalSignificances", []) or []:
            raw = (evidence.get("type") or "").strip().lower()
            if raw in SIGNIFICANCE:
                significance = SIGNIFICANCE[raw]
                break

        entry = {
            "mut": mutated.upper(),
            "wt": (feature.get("wildType") or "").upper(),
            "significance": significance,
            "rsid": _rsid(feature),
        }
        positions.setdefault(str(position), []).append(entry)

    return {
        "accession": accession,
        "positions": positions,
        "count": sum(len(v) for v in positions.values()),
        "source": "EBI Proteins API (ClinVar, gnomAD and others)",
    }


def _rsid(feature: dict) -> str | None:
    for xref in feature.get("xrefs", []) or []:
        if (xref.get("name") or "").lower() == "dbsnp":
            return xref.get("id")
    return None
