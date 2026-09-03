"""HOGWASH: a real WebLogo, from a real alignment.

Height-Ordered Glyphs Weighted Across Sequence Homologues.

Every other tab in this app derives from a protein language model and uses no
alignment at all, which is GIBBERISH's whole point. This one is the opposite and
the older idea: count what evolution actually did, in a column of aligned
homologues. Having both in one app is the interesting part, because they can be
laid over the same structure and disagreed with each other.

The library doing the work is WebLogo 3 (Crooks GE, Hon G, Chandonia JM, Brenner
SE, Genome Research 14:1188-1190, 2004), MIT licensed, used unmodified. This
module is a wrapper and nothing more: it does not reimplement any of the
science, because the point of the tab is that the numbers are authentically
WebLogo's.

The one architectural thing worth knowing is that WebLogo separates its maths
from its rendering, and we use both halves separately:

    LogoData      counts and entropy per column, after composition priors,
                  pseudocounts and the small-sample correction. No renderer.
    eps           genuine WebLogo output, generated natively.
    pdf/png/jpeg  that EPS through ghostscript.
    svg           the PDF through pdf2svg.
    logodata/csv  the numbers, as WebLogo prints them.

So the flat logo a user downloads is the real thing, and the 3D one is the same
LogoData poured into this app's own glyph engine.

Note which format is the native one. WebLogo's git master has a
`native_pdf_formatter` that needs no external tool, but the released 3.9.0 does
not ship it: there, `pdf_formatter` builds an EPS and hands it to ghostscript,
so PDF, PNG and JPEG all depend on `gs` being installed and only EPS, logodata
and CSV work without it. `available_formats()` reports what this particular
machine can actually produce rather than what the library nominally offers.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field

import numpy as np

from weblogo import LogoData, LogoFormat, LogoOptions, parse_prior, seq_io
from weblogo import colorscheme as weblogo_colorscheme
from weblogo.logo import read_seq_data, std_alphabets, std_units
# The library's own mapping rather than a list of imported names: WebLogo
# renamed png_formatter to png_print_formatter between releases, and taking the
# dict means a rename cannot break this module silently.
from weblogo.logo_formatter import (GhostscriptAPI, _bitmap_formatter,
                                    eps_formatter)
from weblogo.logo_formatter import formatters as weblogo_formatters

# Ghostscript's FreeType path fails above 150 DPI on some builds. Measured on
# the droplet's 10.02.1: -r96 and -r150 render correctly, while -r300 and -r600
# both die with "Error: /unknownerror in --.FAPIBuildChar--" and produce zero
# bytes. It is purely the resolution -- the font named makes no difference
# (ArialMT, Helvetica and NimbusSans all fail identically), and neither do the
# anti-aliasing flags nor -dNOFAPI.
#
# This matters because WebLogo's "png" key is png_print_formatter, which sets
# resolution = 600 unconditionally. Every PNG therefore failed on the server
# while JPEG, which honours the requested resolution, worked. For anything that
# needs print quality the answer is EPS or PDF anyway: a logo is line art, and
# vector is the right format for a figure.
MAX_BITMAP_DPI = 150

# The vector devices need the OPPOSITE, and for an unrelated reason that happens
# to be a bug in WebLogo. GhostscriptAPI.convert adds the anti-aliasing flags
# whenever `resolution < 300`, guarded by `if device != "pdf"` -- but by that
# point `device` has been mapped from "pdf" to "pdfwrite", so the guard never
# fires and a PDF gets them too. Ghostscript then refuses outright:
#
#     ERROR: Can't set GraphicsAlphaBits or TextAlphaBits with a vector device.
#     Unrecoverable error: rangecheck in .putdeviceprops
#
# That leaves PDF squeezed between two errors: below 300 the alpha flags are
# added and the vector device refuses them, at 300 and above FAPIBuildChar kills
# anything but the smallest logo. The combination that works for every alignment
# tested is 150 DPI with NO alpha flags, and WebLogo cannot produce it, because
# the flags and the resolution are tied together by that one comparison.
#
# So the EPS is WebLogo's -- that is the drawing, and the part that matters --
# and the ghostscript invocation for PDF is made here instead. Verified across
# all five bundled alignments: globins, cap_hth, hth, cap_dna and lexa all
# convert cleanly this way and none of them did before.
VECTOR_DPI = 150
from weblogo.seq import Alphabet, SeqList

# WebLogo's own vocabularies, surfaced rather than redefined so the tab cannot
# drift from the library it is presenting.
UNITS = list(std_units.keys())
ALPHABETS = list(std_alphabets.keys())
FORMATS = [f.names[0] for f in seq_io.formats]

COLOR_SCHEMES = {
    "auto": None,          # WebLogo picks by alphabet
    "chemistry": weblogo_colorscheme.chemistry,
    "charge": weblogo_colorscheme.charge,
    "hydrophobicity": weblogo_colorscheme.hydrophobicity,
    "taylor": weblogo_colorscheme.taylor,
    "monochrome": weblogo_colorscheme.monochrome,
    "base_pairing": weblogo_colorscheme.base_pairing,
    "nucleotide": weblogo_colorscheme.nucleotide,
}

# Which output formats this deployment can actually produce. PDF and the text
# formats are native; the bitmap and vector ones shell out, and saying so up
# front is better than offering a button that fails.
#   name -> (WebLogo's key for it, the external binary it needs, if any)
#
# PDF needs ghostscript in the released 3.9.0. It is tempting to mark it native
# because git master has a `native_pdf_formatter`, and marking it so here made
# available_formats() report PDF as working on a machine with no `gs`, which
# would have put a download button on the page that could only ever fail.
_FORMATTERS = {
    "pdf": ("pdf", "gs"),
    "eps": ("eps", None),
    "txt": ("logodata", None),
    "csv": ("csv", None),
    "png": ("png", "gs"),
    "jpeg": ("jpeg", "gs"),
    "svg": ("svg", "pdf2svg"),
}


class LogoError(ValueError):
    """A problem with the alignment or the options, phrased for a user."""


def available_formats() -> dict[str, bool]:
    """Which download formats work here. Checked, not assumed."""
    return {
        name: (key in weblogo_formatters
               and (binary is None or shutil.which(binary) is not None))
        for name, (key, binary) in _FORMATTERS.items()
    }


@dataclass
class Alignment:
    """A parsed alignment, plus what we had to notice about it."""

    seqs: SeqList
    fmt: str
    count: int
    length: int
    alphabet_name: str
    names: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_alignment(text: str, fmt: str = "auto",
                    alphabet_name: str = "auto",
                    ignore_lower_case: bool = False) -> Alignment:
    """Read an alignment in any of the formats WebLogo understands.

    Goes through WebLogo's own `read_seq_data` rather than calling a parser
    directly. That function does three things beyond parsing, and reimplementing
    any of them would be a way for this tab to disagree with the library it is
    presenting: it assigns the alphabet AFTER reading (a SeqList parsed without
    one has `alphabet = None`, and LogoData then fails with "No alphabet"), it
    can auto-detect the alphabet from the residues actually present, and it
    honours lower-case masking.

    `fmt` "auto" uses WebLogo's own sniffing across eleven formats, which is
    better than ours. It is overridable because sniffing occasionally picks
    wrong on a file that is valid as two things.

    `alphabet_name` defaults to "auto" for a reason worth stating: it used to
    default to "protein", and a DNA alignment silently went through as protein
    because A, C, G and T are all valid amino acid letters too. Nothing errors.
    The logo just gets a 20-letter alphabet, a ceiling of 4.322 bits instead of
    2.0, and information content computed against the wrong background --
    a completely wrong answer that looks entirely reasonable.
    """
    if not text or not text.strip():
        raise LogoError("No alignment. Paste one, upload a file, or fetch a family.")

    alphabet = None if alphabet_name == "auto" else std_alphabets.get(alphabet_name)
    if alphabet_name != "auto" and alphabet is None:
        raise LogoError(f"Unknown alphabet {alphabet_name!r}. "
                        f"Choose from auto, {', '.join(ALPHABETS)}.")

    if fmt == "auto":
        parser = seq_io.read
    else:
        entry = next((f for f in seq_io.formats if fmt in f.names), None)
        if entry is None:
            raise LogoError(f"Unknown alignment format {fmt!r}. "
                            f"WebLogo reads {', '.join(FORMATS)}.")
        parser = entry.read

    try:
        seqs = read_seq_data(io.StringIO(text), parser, alphabet=alphabet,
                             ignore_lower_case=ignore_lower_case)
    except Exception as exc:
        raise LogoError(
            f"Could not read that as an alignment ({exc}). WebLogo understands "
            f"{', '.join(FORMATS)}. If the sequences are not all the same "
            "length, they are not aligned yet."
        ) from exc

    lengths = {len(s) for s in seqs}
    if len(lengths) > 1:
        raise LogoError(
            f"The sequences are not all the same length ({min(lengths)} to "
            f"{max(lengths)}), so this is a set of sequences rather than an "
            "alignment. Align them first, then come back."
        )

    notes = []
    if len(seqs) < 10:
        # Not an error: WebLogo's small-sample correction exists for exactly
        # this. But a logo over four sequences says more about the four than
        # about the family, and nothing on the picture admits that.
        notes.append(
            f"Only {len(seqs)} sequences. The small-sample correction is on by "
            "default, but a logo over this few is a weak estimate of a family."
        )

    detected = str(seqs.alphabet)
    resolved = alphabet_name
    if alphabet_name == "auto":
        resolved = next((n for n, a in std_alphabets.items()
                         if str(a) == detected), "protein")
        notes.append(f"Alphabet detected as {resolved}.")

    return Alignment(
        seqs=seqs, fmt=("auto-detected" if fmt == "auto" else fmt),
        count=len(seqs), length=len(seqs[0]), alphabet_name=resolved,
        names=[s.name or f"seq{i + 1}" for i, s in enumerate(seqs[:400])],
        notes=notes,
    )


def build_logo_data(alignment: Alignment, *, composition: str = "auto",
                    weight: float | None = None,
                    small_sample_correction: bool = True) -> LogoData:
    """Run WebLogo's own maths over the alignment.

    `composition` is the background the information content is measured
    AGAINST, and it is the option most often left at a default that does not
    suit the data. "auto" uses the alphabet's equiprobable distribution for
    protein; a genome's real amino acid or CG composition gives a different and
    usually more honest answer.
    """
    alphabet: Alphabet = alignment.seqs.alphabet
    try:
        prior = parse_prior(composition, alphabet, weight)
    except Exception as exc:
        raise LogoError(f"Could not use that composition ({exc}).") from exc

    if not small_sample_correction:
        # WebLogo applies the correction when a prior is present. Passing none
        # is how the library itself turns it off; there is no separate flag.
        prior = None

    try:
        return LogoData.from_seqs(alignment.seqs, prior)
    except Exception as exc:
        raise LogoError(f"WebLogo could not build a logo ({exc}).") from exc


def render(logo_data: LogoData, options: LogoOptions, fmt: str) -> bytes:
    """Render through WebLogo's own formatters. The output IS a WebLogo."""
    entry = _FORMATTERS.get(fmt)
    if entry is None:
        raise LogoError(f"Unknown format {fmt!r}. "
                        f"Choose from {', '.join(_FORMATTERS)}.")
    key, binary = entry
    formatter = weblogo_formatters.get(key)
    if formatter is None:
        raise LogoError(f"This build of WebLogo has no {fmt.upper()} formatter.")
    if binary and shutil.which(binary) is None:
        raise LogoError(
            f"{fmt.upper()} output needs {binary!r}, which is not installed on "
            "this server. PDF works and is the same drawing."
        )
    layout = LogoFormat(logo_data, options)
    try:
        if fmt in ("pdf", "svg"):
            # Also not weblogo_formatters["pdf"]: pdf_formatter calls
            # GhostscriptAPI.convert without a resolution, which defaults to
            # 300 and lands on the same cliff. Every PDF above the smallest
            # alignment failed. WebLogo's own API, asked for a survivable
            # resolution -- vector output, so the DPI only sets the raster
            # fallback for anything gs cannot express as curves.
            layout.resolution = VECTOR_DPI
            pdf = _eps_to_pdf(eps_formatter(logo_data, layout),
                              layout.logo_width, layout.logo_height)
            return pdf if fmt == "pdf" else _pdf_to_svg(pdf)

        if fmt in ("png", "jpeg"):
            # Not weblogo_formatters["png"], which is png_print_formatter and
            # pins 600 DPI. Go through _bitmap_formatter so the resolution is
            # the one in LogoOptions, capped at what ghostscript can survive.
            layout.resolution = min(int(layout.resolution or 96), MAX_BITMAP_DPI)
            return _bitmap_formatter(logo_data, layout,
                                     device=("png" if fmt == "png" else "jpeg"))
        return formatter(logo_data, layout)
    except Exception as exc:
        message = str(exc)
        if "FAPIBuildChar" in message or "Ghostscript conversion failed" in message:
            raise LogoError(
                "Ghostscript could not convert this logo. EPS is generated "
                "without it and always works, and for a figure it is the better "
                "format anyway: a logo is line art."
            ) from exc
        raise LogoError(f"WebLogo could not render that ({message}).") from exc


def _eps_to_pdf(eps: bytes, width: float, height: float) -> bytes:
    """WebLogo's EPS through ghostscript, with flags that actually work.

    Deliberately not GhostscriptAPI.convert: see VECTOR_DPI above for why that
    cannot be asked for the one combination the vector device accepts. The
    argument list is otherwise WebLogo's own, minus the three anti-aliasing
    flags that a vector device rejects outright.
    """
    import subprocess

    binary = shutil.which("gs") or shutil.which("gswin32c")
    if binary is None:
        raise LogoError("PDF output needs ghostscript, which is not installed here.")
    args = [
        binary, "-sDEVICE=pdfwrite", "-dPDFSETTINGS=/printer",
        "-sstdout=%stderr", "-dColorConversionStrategy=/LeaveColorUnchanged",
        "-sOutputFile=-", f"-dDEVICEWIDTHPOINTS={width}",
        f"-dDEVICEHEIGHTPOINTS={height}", "-dSAFER", "-dNOPAUSE",
        f"-r{VECTOR_DPI}", "-",
    ]
    result = subprocess.run(args, input=eps, capture_output=True, timeout=180)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        raise LogoError(
            "Ghostscript could not convert this logo to PDF"
            + (f": {detail[-1]}" if detail else ".")
        )
    return result.stdout


def _pdf_to_svg(pdf: bytes) -> bytes:
    """pdf2svg, the same tool and the same dance WebLogo's svg_formatter does.

    Reimplemented only because the PDF it would otherwise consume is made at a
    resolution ghostscript cannot manage here; the conversion itself is
    unchanged.
    """
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    binary = shutil.which("pdf2svg")
    if binary is None:
        raise LogoError("SVG output needs 'pdf2svg', which is not installed here.")
    with tempfile.TemporaryDirectory() as workdir:
        source = _Path(workdir) / "logo.pdf"
        target = _Path(workdir) / "logo.svg"
        source.write_bytes(pdf)
        subprocess.run([binary, str(source), str(target)],
                       check=True, capture_output=True, timeout=120)
        return target.read_bytes()


def make_options(settings: dict, alphabet_name: str = "protein") -> LogoOptions:
    """Turn the request's JSON into a LogoOptions, ignoring anything unknown.

    Deliberately permissive about extra keys and strict about types: the front
    end sends every control it has, and a new WebLogo option should be able to
    appear here without the client needing to know about it.
    """
    options = LogoOptions()
    options.alphabet = std_alphabets[alphabet_name]

    scheme = settings.get("color_scheme", "auto")
    if scheme != "auto" and scheme in COLOR_SCHEMES:
        options.color_scheme = COLOR_SCHEMES[scheme]

    simple = {
        "logo_title": str, "logo_label": str, "unit_name": str,
        "yaxis_label": str, "xaxis_label": str, "fineprint": str,
        "show_yaxis": bool, "show_xaxis": bool, "show_ends": bool,
        "show_fineprint": bool, "show_errorbars": bool, "show_boxes": bool,
        "rotate_numbers": bool, "scale_width": bool, "pad_right": bool,
        "stacks_per_line": int, "yaxis_tic_interval": float,
        "yaxis_scale": float, "number_interval": float,
        "first_index": int, "logo_start": int, "logo_end": int,
        "resolution": int, "stack_aspect_ratio": float,
        "errorbar_fraction": float, "errorbar_gray": float,
        "shrink_fraction": float, "stack_width": float,
        "title_fontsize": float, "fontsize": float, "small_fontsize": float,
        "number_fontsize": float,
    }
    for key, kind in simple.items():
        if key not in settings or settings[key] is None:
            continue
        try:
            setattr(options, key, kind(settings[key]))
        except (TypeError, ValueError):
            # A single bad control should not lose the whole logo.
            continue

    if options.unit_name not in std_units:
        raise LogoError(f"Unknown unit {options.unit_name!r}. "
                        f"Choose from {', '.join(UNITS)}.")
    return options


def to_payload(logo_data: LogoData, alignment: Alignment,
               options: LogoOptions) -> dict:
    """The column-by-column numbers, for this app's own renderer.

    This is what makes the 3D half possible: WebLogo's counts and entropies,
    handed over as data rather than as a picture. Heights are computed the same
    way a logo stacks them, p_a * R_i, so a column here and a column in the PDF
    are the same column.
    """
    counts = np.asarray(logo_data.counts, dtype=np.float64)
    entropy = np.asarray(logo_data.entropy, dtype=np.float64)
    # entropy_interval is (L, 2): the low and high bounds WebLogo draws as
    # error bars, not a single number. Both are carried, because the 3D view
    # can show an uncertainty cap on a stack and a scalar could not say how.
    interval = (np.asarray(logo_data.entropy_interval, dtype=np.float64)
                if logo_data.entropy_interval is not None
                else np.zeros((len(entropy), 2)))
    weights = np.asarray(
        logo_data.weight if logo_data.weight is not None
        else np.ones(len(counts)), dtype=np.float64)

    # Alphabet is not subscriptable; its string form is the letter order, and
    # that order is exactly the column order of LogoData.counts.
    letters = str(options.alphabet)
    totals = counts.sum(axis=1, keepdims=True)
    probs = np.divide(counts, np.where(totals == 0, 1, totals))

    columns = []
    for i in range(len(counts)):
        order = np.argsort(-probs[i])
        stack = [
            {"aa": letters[int(j)],
             "p": round(float(probs[i, int(j)]), 5),
             "n": round(float(counts[i, int(j)]), 2),
             # p * R, the height a logo would give this letter, in the
             # position's own units.
             "h": round(float(probs[i, int(j)] * entropy[i]), 5)}
            for j in order[:8] if counts[i, int(j)] > 0
        ]
        columns.append({
            "i": i,
            "number": int(options.first_index) + i,
            "bits": round(float(entropy[i]), 4),
            "lo": round(float(interval[i][0]), 4),
            "hi": round(float(interval[i][1]), 4),
            "weight": round(float(weights[i]), 4),
            "observed": int(round(float(totals[i, 0]))),
            "stack": stack,
        })

    return {
        "columns": columns,
        "alphabet": letters,
        "unit": options.unit_name,
        # The ceiling a column can reach: log2 of the alphabet size, which is
        # 4.322 for the 20 amino acids and 2.0 for the four bases.
        "max_bits": round(float(np.log2(max(2, len(letters)))), 3),
        "alignment": {
            "sequences": alignment.count,
            "columns": alignment.length,
            "format": alignment.fmt,
            "alphabet": alignment.alphabet_name,
            "names": alignment.names[:60],
            "notes": alignment.notes,
        },
    }
