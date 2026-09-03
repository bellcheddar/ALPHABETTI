"""Fetch a family alignment, so HOGWASH works without hunting for a file.

A sequence logo needs an alignment, and requiring one to be found, downloaded
and pasted before anything appears is the difference between a tool people try
and a tool people bounce off. Given a UniProt accession this asks InterPro which
Pfam families the protein belongs to and pulls the family's seed alignment.

The seed rather than the full alignment, deliberately. A full Pfam alignment can
run to hundreds of thousands of sequences, which is a large download, a slow
logo and a picture dominated by whichever clade happens to have been sequenced
most. The seed is curated, representative, and usually a few hundred sequences,
which is what a logo actually wants.
"""

from __future__ import annotations

import gzip
import io
import time

import requests

INTERPRO_ENTRIES = (
    "https://www.ebi.ac.uk/interpro/api/entry/pfam/protein/uniprot/{accession}/"
)
PFAM_ALIGNMENT = (
    "https://www.ebi.ac.uk/interpro/api/entry/pfam/{pfam_id}/?annotation=alignment:seed"
)

HEADERS = {"Accept": "application/json",
           "User-Agent": "ALPHABETTI/1.0 (marc@marcdeller.com)"}


class FamilyError(RuntimeError):
    """A lookup that failed, phrased for a user."""


def families_for(accession: str, timeout: float = 45.0,
                 retries: int = 2) -> list[dict]:
    """Which Pfam families this accession belongs to, and where they sit.

    InterPro is slow and erratically so: the same query can answer in a second
    or time out at twenty. Measured here, P00698 returned promptly while P0CG48
    and P60174 both timed out at 20 s and succeeded on a longer one, so the
    generous timeout and the retry are both load-bearing rather than defensive
    padding.
    """
    # Retry on server errors as well as on timeouts. InterPro returns a 500
    # with an HTML error page during its own bad spells -- observed answering
    # P00698 correctly and then 500ing on every accession two minutes later --
    # and a status check alone would report that as a permanent "no families"
    # rather than as a service that is temporarily unwell.
    response = None
    trouble = "no response"
    for attempt in range(retries + 1):
        try:
            candidate = requests.get(
                INTERPRO_ENTRIES.format(accession=accession.upper()),
                headers=HEADERS, timeout=timeout,
            )
            if candidate.status_code >= 500:
                trouble = f"HTTP {candidate.status_code} from InterPro"
                time.sleep(1.5 * (attempt + 1))
                continue
            response = candidate
            break
        except Exception as exc:
            trouble = exc.__class__.__name__
            time.sleep(1.5 * (attempt + 1))
    if response is None:
        raise FamilyError(
            f"InterPro is not answering ({trouble}). It is often slow and "
            "occasionally down; this is at their end, not yours. Paste or "
            "upload an alignment instead, or try the family fetch again later."
        )
    try:
        if response.status_code in (204, 404):
            # InterPro answers "no hits" with 204 and an empty body rather than
            # a 404 or an empty list, so a naive .json() raises here.
            return []
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise FamilyError(
            f"InterPro returned something unreadable ({exc.__class__.__name__}). "
            "You can still paste or upload an alignment."
        ) from exc

    families = []
    for result in data.get("results", []):
        meta = result.get("metadata", {})
        locations = []
        for protein in result.get("proteins", []):
            for entry in protein.get("entry_protein_locations", []) or []:
                for fragment in entry.get("fragments", []) or []:
                    locations.append((fragment.get("start"), fragment.get("end")))
        families.append({
            "id": meta.get("accession"),
            "name": meta.get("name"),
            "type": meta.get("type"),
            # Where the domain sits in THIS protein: the mapping the 3D view
            # needs to put an alignment column onto a residue.
            "locations": [{"start": s, "end": e} for s, e in locations if s and e],
        })
    return families


def seed_alignment(pfam_id: str, timeout: float = 60.0) -> str:
    """The seed alignment for a Pfam family, as Stockholm text.

    InterPro serves it gzipped. Content-Encoding handling differs between
    proxies, so the gzip magic number is checked rather than trusted from a
    header: an already-decompressed body would otherwise be run through gunzip
    and fail with something that says nothing about the real problem.
    """
    try:
        response = requests.get(
            PFAM_ALIGNMENT.format(pfam_id=pfam_id.upper()),
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=timeout,
        )
        if response.status_code == 404:
            raise FamilyError(f"Pfam has no family {pfam_id!r}.")
        response.raise_for_status()
        body = response.content
    except FamilyError:
        raise
    except Exception as exc:
        raise FamilyError(
            f"Could not download the {pfam_id} alignment ({exc.__class__.__name__})."
        ) from exc

    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception as exc:
            raise FamilyError(f"The {pfam_id} alignment did not decompress.") from exc

    text = body.decode("utf-8", errors="replace")
    if not text.strip():
        raise FamilyError(f"Pfam returned an empty alignment for {pfam_id}.")
    return text


def alignment_for(accession: str, pfam_id: str | None = None) -> dict:
    """Everything HOGWASH needs to draw a family logo for one accession."""
    families = families_for(accession)
    if not families:
        raise FamilyError(
            f"InterPro lists no Pfam family for {accession.upper()}. Not every "
            "protein has one. Paste or upload an alignment instead."
        )

    chosen = next((f for f in families if f["id"] == (pfam_id or "").upper()),
                  families[0])
    return {
        "accession": accession.upper(),
        "families": [{k: v for k, v in f.items() if k != "locations"}
                     for f in families],
        "chosen": chosen,
        "alignment": seed_alignment(chosen["id"]),
    }
