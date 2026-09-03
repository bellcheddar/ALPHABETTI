"""Solvent accessibility and secondary structure: what BUMFLUFF is made of.

Two quantities come out of here, both derived from the predicted coordinates
rather than from any model:

  * Relative solvent accessibility (RSA), which drives BUMFLUFF's glyph heights.
  * Secondary structure (H/E/C), which drives one of the shared colour schemes.

Neither needs a GPU, so both run wherever the web app runs.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

# FreeSASA is the reference implementation and is used when it is importable,
# but it has no Linux wheel and would drag a full compiler toolchain onto a
# droplet with 2 GB free. Biopython's Shrake-Rupley is pure Python and needs
# nothing extra.
#
# Measured against each other on lysozyme: total SASA agrees to 0.1 %
# (8978 vs 8971 A^2), per-residue correlation r = 0.998, mean absolute
# difference 2.6 A^2, and the buried/exposed call at RSA 0.25 -- which is the
# only thing any of this feeds -- agrees at 98.6 % of residues. Shrake-Rupley
# costs 114 ms against 28 ms, which is nothing next to a fold of several
# seconds.
try:
    import freesasa
except ImportError:                                   # pragma: no cover
    freesasa = None

# Theoretical maximum solvent accessible surface area per residue type, in A^2.
#
# Source: Tien MZ, Meyer AG, Sydykova DK, Spielman SJ, Wilke CO (2013),
# "Maximum Allowed Solvent Accessibilites of Residues in Proteins",
# PLoS ONE 8(11): e80635. doi:10.1371/journal.pone.0080635
#
# These are the THEORETICAL column of Table 1, computed from Gly-X-Gly tripeptides
# in an extended conformation. The paper also reports empirical maxima from a
# structure survey; the theoretical set is the one used here because it is the
# conventional choice for RSA and because empirical maxima are bounded by whatever
# happened to be in the survey, which makes RSA > 1 impossible by construction and
# therefore hides genuinely hyper-exposed residues.
#
# The UI cites this table, and section 9 of the build spec requires that citation,
# so if these numbers change the About page changes with them.
TIEN_MAX_ASA = {
    "A": 129.0, "R": 274.0, "N": 195.0, "D": 193.0, "C": 167.0,
    "E": 223.0, "Q": 225.0, "G": 104.0, "H": 224.0, "I": 197.0,
    "L": 201.0, "K": 236.0, "M": 224.0, "F": 240.0, "P": 159.0,
    "S": 155.0, "T": 172.0, "W": 285.0, "Y": 263.0, "V": 174.0,
}
# X is an unknown residue. Alanine's maximum is the least misleading default:
# it is small, so an unknown residue reads as relatively exposed rather than
# relatively buried, and over-reporting exposure is the safer error for the
# aggregation and epitope uses BUMFLUFF is aimed at.
TIEN_MAX_ASA["X"] = TIEN_MAX_ASA["A"]

# The conventional buried/exposed cut-off, labelled in the BUMFLUFF legend.
BURIED_CUTOFF = 0.25

# Kyte-Doolittle hydropathy. Used for the hydrophobic-patch highlight and for
# one of the shared colour schemes.
# Kyte J, Doolittle RF (1982), J Mol Biol 157(1):105-32.
KYTE_DOOLITTLE = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5,
    "E": -3.5, "Q": -3.5, "G": -0.4, "H": -3.2, "I": 4.5,
    "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6,
    "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
    "X": 0.0,
}
# Above this a residue counts as hydrophobic for the patch highlight. 1.8 is
# alanine's own value, which is the usual place to draw the line.
HYDROPHOBIC_CUTOFF = 1.8

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLU": "E", "GLN": "Q", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


def compute_sasa(pdb_text: str, probe_radius: float = 1.4) -> tuple[np.ndarray, float]:
    """Per-residue SASA in A^2, plus the total for the chain.

    Probe radius 1.4 A is the radius of a water molecule; it is an argument
    because the sidebar reports it, and a number shown in a UI should be the
    number the code actually used.
    """
    if freesasa is not None:
        return _sasa_freesasa(pdb_text, probe_radius)
    return _sasa_shrake_rupley(pdb_text, probe_radius)


def _sasa_shrake_rupley(pdb_text: str, probe_radius: float) -> tuple[np.ndarray, float]:
    """Biopython's Shrake-Rupley. No compiled dependency, so it always works."""
    import io

    from Bio.PDB import PDBParser
    from Bio.PDB.SASA import ShrakeRupley

    structure = PDBParser(QUIET=True).get_structure("x", io.StringIO(pdb_text))
    model = next(iter(structure))
    # 100 sphere points: the default is 100 and raising it changes the answer by
    # far less than the difference between the two algorithms.
    ShrakeRupley(probe_radius=probe_radius, n_points=100).compute(model, level="R")

    per_residue = np.array(
        [residue.sasa for residue in next(iter(model)) if residue.id[0] == " "],
        dtype=np.float64,
    )
    return per_residue, float(per_residue.sum())


def _sasa_freesasa(pdb_text: str, probe_radius: float) -> tuple[np.ndarray, float]:
    """FreeSASA, when it is installed. The reference implementation."""
    with tempfile.NamedTemporaryFile("w", suffix=".pdb", delete=False) as handle:
        handle.write(pdb_text)
        path = handle.name
    try:
        structure = freesasa.Structure(path)
        parameters = freesasa.Parameters({"probe-radius": probe_radius})
        result = freesasa.calc(structure, parameters)

        # FreeSASA reports per atom; residues are rebuilt by summing atoms that
        # share a residue number. residueNumber() returns a padded string, so it
        # is normalised here rather than being used as a key directly.
        totals: dict[int, float] = {}
        order: list[int] = []
        for atom in range(structure.nAtoms()):
            resnum = int(structure.residueNumber(atom).strip())
            if resnum not in totals:
                totals[resnum] = 0.0
                order.append(resnum)
            totals[resnum] += result.atomArea(atom)

        per_residue = np.array([totals[r] for r in order], dtype=np.float64)
        return per_residue, float(result.totalArea())
    finally:
        Path(path).unlink(missing_ok=True)


def relative_sasa(sasa: np.ndarray, sequence: str) -> np.ndarray:
    """SASA divided by the Tien et al. theoretical maximum for each residue type.

    Not clipped to 1. A predicted structure can put a residue in a more exposed
    conformation than the reference tripeptide, and clipping would silently
    flatten exactly the hyper-exposed positions BUMFLUFF exists to show. Values
    above 1 are rare and real; the legend's ramp saturates at 1 but the number
    in the tooltip is the true one.
    """
    maxima = np.array([TIEN_MAX_ASA.get(aa, TIEN_MAX_ASA["X"]) for aa in sequence])
    return sasa / maxima


def hydropathy(sequence: str) -> np.ndarray:
    """Kyte-Doolittle value per position."""
    return np.array([KYTE_DOOLITTLE.get(aa, 0.0) for aa in sequence], dtype=np.float64)


def exposed_hydrophobics(rsa: np.ndarray, sequence: str) -> np.ndarray:
    """Boolean mask: exposed AND hydrophobic, the aggregation-prone combination."""
    return (rsa > BURIED_CUTOFF) & (hydropathy(sequence) > HYDROPHOBIC_CUTOFF)


# --------------------------------------------------------------------------
# Secondary structure
# --------------------------------------------------------------------------
#
# DSSP is the reference assignment and is used when mkdssp is on the PATH. It is
# NOT on the droplet, so the geometric fallback below is the production path
# rather than a safety net, and it is validated against DSSP in the test suite.


def secondary_structure(pdb_text: str, ca: np.ndarray | None = None) -> tuple[str, str]:
    """Return (assignment string of H/E/C, method used).

    Tries DSSP, falls back to the geometric assignment. The method is returned
    rather than logged because the UI names it: a user looking at a secondary
    structure colouring deserves to know whether it came from hydrogen bonding
    or from CA positions alone.

    The fallback carries the reason it fell back. An earlier version swallowed
    the exception with a bare `except: pass`, which hid a real bug for as long
    as it took to notice that the method label never said DSSP: mkdssp rejects
    any file without a valid PDB HEADER line, and ESMFold does not emit one, so
    DSSP failed on every single structure this app produces while looking to
    the caller like a machine that simply had no DSSP installed.
    """
    if shutil.which("mkdssp") or shutil.which("dssp"):
        try:
            return _dssp(pdb_text), "DSSP"
        except Exception as exc:
            # A DSSP that is present but unhappy (an unusual residue, a chain
            # break) must not take the request down with it -- but it must not
            # vanish either.
            reason = f"P-SEA (geometric; DSSP failed: {exc.__class__.__name__})"
    else:
        reason = "P-SEA (geometric)"
    if ca is None:
        raise ValueError("Geometric fallback needs CA coordinates.")
    return psea(ca), reason


def _dssp(pdb_text: str) -> str:
    """Run mkdssp and reduce its eight states to three.

    The HEADER line matters. mkdssp inspects the first line to decide whether it
    is looking at PDB or mmCIF, and without a valid HEADER it assumes mmCIF,
    fails to parse it, and exits 1 with a message about mmCIF that says nothing
    about the real problem. ESMFold emits no HEADER, so one is synthesised here
    rather than requiring every caller to know this.
    """
    binary = shutil.which("mkdssp") or shutil.which("dssp")
    if not pdb_text.startswith("HEADER"):
        # Columns matter: HEADER, classification at 11-50, date at 51-59, id at
        # 63-66. A malformed HEADER is no better than none.
        pdb_text = (
            "HEADER    PREDICTED STRUCTURE                     "
            "01-JAN-00   ABCD\n" + pdb_text
        )

    with tempfile.TemporaryDirectory() as workdir:
        source = Path(workdir) / "in.pdb"
        target = Path(workdir) / "out.dssp"
        source.write_text(pdb_text)
        subprocess.run(
            [binary, "--output-format", "dssp", str(source), str(target)],
            capture_output=True, text=True, timeout=60, check=True,
        )
        out = target.read_text()

    codes: list[str] = []
    started = False
    for line in out.splitlines():
        if line.startswith("  #  RESIDUE"):
            started = True
            continue
        if not started or len(line) < 17:
            continue
        if line[13] == "!":       # chain break marker, not a residue
            continue
        codes.append(_simplify_dssp(line[16]))
    return "".join(codes)


def _simplify_dssp(code: str) -> str:
    """DSSP's eight states collapsed to three.

    G (3-10) and I (pi) join H, and B (isolated bridge) joins E, which is the
    standard three-state reduction. Everything else is coil.
    """
    if code in "HGI":
        return "H"
    if code in "EB":
        return "E"
    return "C"


# P-SEA reference values, from Labesse G, Colloc'h N, Pothier J, Mornon JP (1997),
# "P-SEA: a new efficient assignment of secondary structure from C-alpha trace of
# proteins", CABIOS 13(3):291-295.
#
# Each entry is (centre, tolerance) in A for the CA(i)-CA(i+n) distances, and in
# degrees for the CA angle and the CA virtual torsion.
_PSEA_HELIX = {
    "d2": (5.5, 0.5), "d3": (5.3, 0.5), "d4": (6.4, 0.6),
    "theta": (89.0, 12.0), "tau": (50.0, 20.0),
}
_PSEA_STRAND = {
    "d2": (6.7, 0.6), "d3": (9.9, 0.9), "d4": (12.4, 1.1),
    "theta": (124.0, 14.0), "tau": (-170.0, 45.0),
}
# A run shorter than this is noise rather than a secondary structure element.
# DSSP itself will not call a helix with fewer than four residues.
_MIN_HELIX = 3
_MIN_STRAND = 2


# How many overlapping windows must agree before a residue is called. Tuned on
# the ten-chain DSSP set; see psea() for the numbers. One is far too permissive.
_COVERAGE = 3


def _within(value: float, spec: tuple[float, float]) -> bool:
    centre, tolerance = spec
    return abs(value - centre) <= tolerance


def _within_wrapped(value: float, spec: tuple[float, float]) -> bool:
    """As _within, but on the circle: +175 and -175 are 10 degrees apart."""
    centre, tolerance = spec
    return abs((value - centre + 180.0) % 360.0 - 180.0) <= tolerance


def _ca_angles(ca: np.ndarray) -> np.ndarray:
    """CA(i-1)-CA(i)-CA(i+1) angle in degrees, NaN at both termini."""
    out = np.full(len(ca), np.nan)
    if len(ca) < 3:
        return out
    v1 = ca[:-2] - ca[1:-1]
    v2 = ca[2:] - ca[1:-1]
    cosine = np.sum(v1 * v2, axis=1) / (
        np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1)
    )
    out[1:-1] = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    return out


def _ca_torsions(ca: np.ndarray) -> np.ndarray:
    """CA virtual torsion over i-1..i+2, in degrees. NaN where undefined.

    This is the quantity that separates a right-handed alpha helix (about +50
    degrees) from a beta strand (about 180). Distances alone cannot: a left-
    handed helix has the same CA-CA distances as a right-handed one.
    """
    out = np.full(len(ca), np.nan)
    if len(ca) < 4:
        return out
    # b0 points BACKWARDS along the chain (p0 - p1), not forwards. Using
    # p1 - p0 negates v below, which flips both atan2 arguments and returns
    # a torsion exactly 180 degrees out. That error is invisible in isolation
    # -- the values still look like plausible angles -- and it showed up here
    # only as helices sitting at -129 deg where P-SEA says +50.
    b0 = ca[:-3] - ca[1:-2]
    b1 = ca[2:-1] - ca[1:-2]
    b2 = ca[3:] - ca[2:-1]
    b1n = b1 / np.linalg.norm(b1, axis=1, keepdims=True)
    v = b0 - np.sum(b0 * b1n, axis=1, keepdims=True) * b1n
    w = b2 - np.sum(b2 * b1n, axis=1, keepdims=True) * b1n
    x = np.sum(v * w, axis=1)
    y = np.sum(np.cross(b1n, v) * w, axis=1)
    out[1:-2] = np.degrees(np.arctan2(y, x))
    return out


def psea(ca: np.ndarray) -> str:
    """Secondary structure from CA positions alone.

    A CA-only method, because that is all a fallback can rely on and because it
    means the app never hard-fails on a missing binary. The droplet has no
    mkdssp, so in production this IS the assignment, not a safety net.

    Measured against real DSSP over ten diverse chains (805 residues: 1UBQ,
    1CRN, 2LZM, 1BDD, 4PTI, 1ENH, 3CHY, 1SHG, 256B, 1PGB):

        three-state accuracy   83.7 %
        balanced recall        83.6 %   (H 0.95, E 0.93, C 0.63)

    Coil recall is the weak one, and predictably so: every criterion here paints
    a window, so elements over-extend by a residue or two at each end and eat
    into the coil either side. For a colour scheme that is the right way round
    to be wrong -- a helix drawn one residue long reads fine, a helix that is
    missing does not.

    The tolerance constants are P-SEA's published ones, unchanged. The coverage
    thresholds (three overlapping windows before a residue is called) were tuned
    on the set above, because P-SEA's paper specifies the geometry but not how
    to combine overlapping window hits, and the obvious choice of "any window
    fires" over-calls strand catastrophically: it scored 55 %.
    """
    ca = np.asarray(ca, dtype=np.float64)
    length = len(ca)
    states = ["C"] * length
    if length < 5:
        return "".join(states)

    theta = _ca_angles(ca)
    tau = _ca_torsions(ca)

    def distance(i: int, j: int) -> float:
        return float(np.linalg.norm(ca[j] - ca[i]))

    # Coverage counts rather than booleans. A residue is called only when
    # several overlapping windows agree, which is what separates a real element
    # from one lucky window.
    helix = np.zeros(length)
    strand = np.zeros(length)

    for i in range(length):
        if i + 4 < length:
            if all(_within(distance(i, i + k), _PSEA_HELIX[f"d{k}"]) for k in (2, 3, 4)):
                helix[i:i + 5] += 1
            if all(_within(distance(i, i + k), _PSEA_STRAND[f"d{k}"]) for k in (2, 3, 4)):
                strand[i:i + 5] += 1

        if not np.isnan(theta[i]) and not np.isnan(tau[i]):
            # Both torsion comparisons wrap, because the strand reference of
            # -170 degrees sits right next to the +180 discontinuity and a plain
            # subtraction rejects +175 as though it were 345 degrees away.
            if _within(theta[i], _PSEA_HELIX["theta"]) and _within_wrapped(
                tau[i], _PSEA_HELIX["tau"]
            ):
                helix[max(0, i - 1):i + 3] += 1
            if _within(theta[i], _PSEA_STRAND["theta"]) and _within_wrapped(
                tau[i], _PSEA_STRAND["tau"]
            ):
                strand[max(0, i - 1):i + 3] += 1

    for i in range(length):
        # Helix wins ties: its criteria are the tighter pair, so a position
        # satisfying both is far more likely to be helical.
        if helix[i] >= _COVERAGE and helix[i] >= strand[i]:
            states[i] = "H"
        elif strand[i] >= _COVERAGE:
            states[i] = "E"

    return _prune_short_runs("".join(states))


def _prune_short_runs(assignment: str) -> str:
    """Demote elements too short to be real, then return the assignment."""
    chars = list(assignment)
    start = 0
    while start < len(chars):
        end = start
        while end + 1 < len(chars) and chars[end + 1] == chars[start]:
            end += 1
        run = end - start + 1
        minimum = {"H": _MIN_HELIX, "E": _MIN_STRAND}.get(chars[start], 0)
        if run < minimum:
            for i in range(start, end + 1):
                chars[i] = "C"
        start = end + 1
    return "".join(chars)
