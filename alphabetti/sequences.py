"""Input handling: work out what the user pasted, then make it safe to fold.

The user gets one box. They may paste a bare sequence, a FASTA record, several
FASTA records, a UniProt accession or a UniProt entry name, and the app is
expected to work out which without asking. Section 5.7 of the build spec is
explicit that there is no dropdown, and that is the right call: a dropdown is a
question the computer can answer for itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

# The twenty canonical amino acids. X is tolerated on input (it appears in real
# UniProt entries) but is not a folding-friendly residue, so it is counted and
# reported rather than silently accepted.
CANONICAL = "ACDEFGHIKLMNPQRSTVWY"
ACCEPTED = CANONICAL + "X"

# Spec section 3: ESMFold is the bottleneck and these caps exist to keep it
# inside a sane wall clock. Both are configurable, but these are the defaults.
MIN_LENGTH = 10
MAX_LENGTH = 400

# A UniProt accession. The official pattern, which is stricter than the "six
# alphanumerics" approximation people usually reach for: it will not match a
# short peptide sequence by accident, which is the whole reason to be strict.
ACCESSION_RE = re.compile(
    r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$"
)

# A UniProt entry name, e.g. UBC_HUMAN. Always has the underscore, which is what
# makes it unambiguous against everything else the box accepts.
ENTRY_NAME_RE = re.compile(r"^[A-Z0-9]{1,11}_[A-Z0-9]{1,5}$")

UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/{key}.json"
UNIPROT_SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"


class SequenceError(ValueError):
    """A problem with user input that the user can act on.

    Every message this carries is shown verbatim in the UI, so it names the
    offending character and its position rather than saying "invalid input".
    """


@dataclass
class ParsedInput:
    """What the user actually gave us, once resolved to a sequence."""

    sequence: str
    kind: str                       # "sequence" | "fasta" | "accession" | "entry_name"
    accession: str | None = None
    name: str | None = None
    organism: str | None = None
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    original_length: int | None = None

    def as_source(self) -> dict:
        """The `source` block of the result payload."""
        return {
            "type": self.kind,
            "accession": self.accession,
            "name": self.name,
            "organism": self.organism,
            "notes": self.notes,
        }


def looks_like_accession(text: str) -> bool:
    return bool(ACCESSION_RE.match(text.strip().upper()))


def looks_like_entry_name(text: str) -> bool:
    return bool(ENTRY_NAME_RE.match(text.strip().upper()))


def clean_sequence(raw: str) -> tuple[str, list[str]]:
    """Uppercase, strip whitespace and digits, then validate.

    Digits are stripped rather than rejected because the single most common
    paste in this field is a numbered alignment block straight out of a
    sequence viewer, and rejecting that would be pedantry. Everything else that
    is not a letter is an error worth naming.
    """
    notes: list[str] = []
    stripped = re.sub(r"[\s\d]", "", raw).upper()
    if stripped != raw.strip().upper().replace(" ", ""):
        pass  # whitespace/digit removal is routine and not worth a note

    # Interior gap characters mean an alignment was pasted, which is a different
    # mistake from a typo and deserves its own message.
    if "-" in stripped or "." in stripped:
        gaps = stripped.count("-") + stripped.count(".")
        stripped = stripped.replace("-", "").replace(".", "")
        notes.append(
            f"Removed {gaps} gap character{'s' if gaps != 1 else ''}: "
            "that looked like an alignment, so the gaps were dropped."
        )

    if not stripped:
        raise SequenceError("That is empty once whitespace is removed.")

    for position, char in enumerate(stripped, start=1):
        if char not in ACCEPTED:
            raise SequenceError(
                f"'{char}' at position {position} is not an amino acid. "
                f"Accepted letters are {CANONICAL} (and X for unknown)."
            )

    unknown = stripped.count("X")
    if unknown:
        notes.append(
            f"{unknown} position{'s' if unknown != 1 else ''} marked X (unknown). "
            "ESMFold will fold them, but treat those regions with suspicion."
        )

    return stripped, notes


def parse_fasta(raw: str) -> tuple[str, str | None, list[str]]:
    """Return (sequence, header, notes) for the FIRST record in a FASTA blob.

    Multi-record input is accepted and the first record used, because refusing
    it helps nobody -- but the app says so out loud rather than quietly picking
    one, which would leave the user looking at the wrong protein.
    """
    records: list[tuple[str | None, list[str]]] = []
    header: str | None = None
    body: list[str] = []

    for line in raw.splitlines():
        if line.startswith(">"):
            if header is not None or body:
                records.append((header, body))
            header = line[1:].strip()
            body = []
        else:
            body.append(line)
    if header is not None or body:
        records.append((header, body))

    if not records:
        raise SequenceError("No FASTA records found.")

    notes: list[str] = []
    if len(records) > 1:
        notes.append(
            f"That FASTA held {len(records)} records. Using the first one: "
            f"{records[0][0] or 'unnamed'}."
        )

    first_header, first_body = records[0]
    return "".join(first_body), first_header, notes


def fetch_uniprot(key: str, timeout: float = 12.0, retries: int = 1) -> ParsedInput:
    """Resolve an accession or entry name to a sequence plus metadata.

    An entry name (UBC_HUMAN) is not a valid path segment for the UniProtKB
    entry endpoint, so it goes through search instead. Both paths converge on
    the same JSON shape.
    """
    key = key.strip().upper()
    is_entry_name = looks_like_entry_name(key)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if is_entry_name:
                response = requests.get(
                    UNIPROT_SEARCH_URL,
                    params={"query": f"id:{key}", "format": "json", "size": 1},
                    timeout=timeout,
                    headers={"User-Agent": "ALPHABETTI/1.0 (marc@marcdeller.com)"},
                )
                response.raise_for_status()
                results = response.json().get("results", [])
                if not results:
                    raise SequenceError(
                        f"UniProt has no entry named {key}. "
                        "Entry names look like UBC_HUMAN; accessions look like P0CG48."
                    )
                data = results[0]
            else:
                response = requests.get(
                    UNIPROT_URL.format(key=key),
                    timeout=timeout,
                    headers={"User-Agent": "ALPHABETTI/1.0 (marc@marcdeller.com)"},
                )
                if response.status_code == 404:
                    raise SequenceError(
                        f"UniProt has no entry {key}. Check the accession, "
                        "or paste the sequence directly."
                    )
                response.raise_for_status()
                data = response.json()
            break
        except SequenceError:
            raise
        except Exception as exc:  # network flake: worth one retry
            last_error = exc
            if attempt == retries:
                raise SequenceError(
                    f"Could not reach UniProt ({exc.__class__.__name__}). "
                    "Try again in a moment, or paste the sequence directly."
                ) from exc

    sequence = (data.get("sequence") or {}).get("value")
    if not sequence:
        raise SequenceError(f"UniProt entry {key} carries no sequence.")

    description = (
        (data.get("proteinDescription") or {}).get("recommendedName", {})
        .get("fullName", {})
        .get("value")
    )
    organism = (data.get("organism") or {}).get("scientificName")

    cleaned, notes = clean_sequence(sequence)
    return ParsedInput(
        sequence=cleaned,
        kind="entry_name" if is_entry_name else "accession",
        accession=data.get("primaryAccession", key),
        name=description or data.get("uniProtkbId", key),
        organism=organism,
        notes=notes,
    )


def parse_input(
    raw: str,
    *,
    residues: tuple[int, int] | None = None,
    allow_truncation: bool = False,
    max_length: int = MAX_LENGTH,
    min_length: int = MIN_LENGTH,
    fetch: bool = True,
) -> ParsedInput:
    """The single entry point: text in, a ready-to-fold ParsedInput out.

    `allow_truncation` is False by default so that the first response to an
    over-length sequence is a question, not a silent decision about which half
    of someone's protein to throw away.
    """
    if raw is None or not raw.strip():
        raise SequenceError("Nothing to fold. Paste a sequence or a UniProt accession.")

    text = raw.strip()

    if text.startswith(">"):
        body, header, notes = parse_fasta(text)
        cleaned, clean_notes = clean_sequence(body)
        parsed = ParsedInput(
            sequence=cleaned,
            kind="fasta",
            name=header,
            notes=notes + clean_notes,
        )
    elif looks_like_entry_name(text) or looks_like_accession(text):
        if not fetch:
            raise SequenceError("UniProt lookup is disabled in this configuration.")
        parsed = fetch_uniprot(text)
    else:
        cleaned, notes = clean_sequence(text)
        parsed = ParsedInput(sequence=cleaned, kind="sequence", notes=notes)

    if residues is not None:
        # An explicit one-based inclusive range. P0CG48 is polyubiquitin-C, a
        # 685-residue precursor of nine exact tandem copies of the 76-residue
        # monomer; folding the whole thing gives something nobody means by
        # "ubiquitin", and the repeats let a masked language model copy each
        # position from its neighbours, which drives information content to
        # near-maximal everywhere and makes the logo meaningless.
        first, last = residues
        full = len(parsed.sequence)
        parsed.sequence = parsed.sequence[first - 1:last]
        if full != len(parsed.sequence):
            parsed.notes.append(
                f"Using residues {first}-{last} of {full}."
            )
        parsed.name = f"{parsed.name} ({first}-{last})" if parsed.name else None

    length = len(parsed.sequence)

    if length < min_length:
        raise SequenceError(
            f"That is {length} residue{'s' if length != 1 else ''}. "
            f"ESMFold needs at least {min_length} to produce anything meaningful."
        )

    if length > max_length:
        if not allow_truncation:
            # The caller turns this into the "shall I truncate?" prompt. Naming
            # the exact range that would survive is the point: the user should
            # not have to count.
            raise SequenceError(
                f"That is {length} residues and the cap is {max_length}. "
                f"Residues 1-{max_length} would be kept and "
                f"{length - max_length} discarded from the C-terminus."
            )
        parsed.original_length = length
        parsed.truncated = True
        parsed.sequence = parsed.sequence[:max_length]
        parsed.notes.append(
            f"Truncated from {length} to {max_length} residues. "
            f"Residues {max_length + 1}-{length} are not shown."
        )

    return parsed
