"""Per-residue orthonormal frames: where each letter sits and which way it faces.

This is the most important function in the backend. Everything the user sees is
a letter placed by a 4x4 matrix, and every one of those matrices comes from
here. Get the frame wrong and the app renders a plausible-looking pile of
nonsense, which is the worst kind of wrong because it still looks like a
protein.

The convention, from section 5.4 of the build spec:

    up      = normalise(CB - CA)          letter "up" runs along the side chain
    tangent = normalise(CA[i+1] - CA[i-1])   local backbone direction
    right   = normalise(cross(up, tangent))
    facing  = cross(right, up)            glyph plane normal, points outward

A glyph is authored in the XY plane facing +Z, so the rotation matrix has
`right` as its first column, `up` as its second and `facing` as its third. That
maps the letter's own x to `right`, its y to `up` and its extrusion depth to
`facing`, which is what puts the letter's face outward and its feet on the
backbone.

`static/js/orientation.js` mirrors this file function for function. If you
change the convention here, change it there in the same commit: FOLDEROL
recomputes frames client side during the morph, and a drift between the two
shows up as letters that rotate as the animation lands.
"""

from __future__ import annotations

import numpy as np

# Virtual CB construction constants. This is the standard tetrahedral placement
# used by trRosetta and AlphaFold's frame code: given the backbone N, CA and C,
# it reconstructs where CB would be if the residue had one. Glycine is the
# reason it exists -- roughly 7% of residues in a typical protein have no CB at
# all, and skipping them would punch holes in the display at exactly the
# positions where backbones turn.
_CB_A = -0.58273431
_CB_B = 0.56802827
_CB_C = -0.54067466

# Below this, two unit vectors count as parallel and the cross product that
# would build `right` is numerically worthless. sin(angle) < 0.05 is about 2.9
# degrees, which is generous: the failure is silent and cheap to guard.
_DEGENERATE = 0.05


def normalise(v: np.ndarray, axis: int = -1) -> np.ndarray:
    """Unit vectors, with a zero-length guard that returns +X rather than NaN."""
    v = np.asarray(v, dtype=np.float64)
    norm = np.linalg.norm(v, axis=axis, keepdims=True)
    safe = np.where(norm < 1e-9, 1.0, norm)
    out = v / safe
    # A zero-length input has no meaningful direction. +X is arbitrary but it is
    # finite, and a NaN here propagates into every matrix downstream.
    fallback = np.zeros_like(out)
    fallback[..., 0] = 1.0
    return np.where(norm < 1e-9, fallback, out)


def virtual_cb(n: np.ndarray, ca: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Reconstruct a CB position from backbone N, CA and C.

    Works on single residues (3,) or whole chains (L, 3).

    Reference: the standard tetrahedral construction, as used in trRosetta
    (Yang et al., PNAS 2020) and AlphaFold's residue frames.
    """
    n = np.asarray(n, dtype=np.float64)
    ca = np.asarray(ca, dtype=np.float64)
    c = np.asarray(c, dtype=np.float64)

    b = ca - n
    cc = c - ca
    a = np.cross(b, cc)
    return _CB_A * a + _CB_B * b + _CB_C * cc + ca


def backbone_tangents(ca: np.ndarray) -> np.ndarray:
    """Local chain direction at every residue, as unit vectors.

    Interior residues use the central difference CA[i+1] - CA[i-1], which is
    smoother than either one-sided difference and is what makes helices read as
    a continuous staircase rather than a zigzag. The two termini have no such
    neighbour and fall back to the single available difference.

    A chain of one residue has no direction at all; +X is returned so the
    caller still gets a valid frame rather than a NaN.
    """
    ca = np.asarray(ca, dtype=np.float64)
    length = len(ca)
    if length == 1:
        return np.array([[1.0, 0.0, 0.0]])

    tangents = np.empty_like(ca)
    tangents[1:-1] = ca[2:] - ca[:-2]
    tangents[0] = ca[1] - ca[0]
    tangents[-1] = ca[-1] - ca[-2]
    return normalise(tangents)


def _orthogonal_to(v: np.ndarray) -> np.ndarray:
    """Any unit vector perpendicular to v, chosen stably.

    Crossing with whichever cardinal axis v is least aligned to avoids the
    classic bug where crossing with a fixed axis returns zero for the one input
    that happens to be that axis.
    """
    v = normalise(v)
    axis = np.zeros_like(v)
    least = np.argmin(np.abs(v), axis=-1)
    np.put_along_axis(axis, least[..., None], 1.0, axis=-1)
    return normalise(np.cross(v, axis))


def residue_frames(
    ca: np.ndarray, cb: np.ndarray, tangents: np.ndarray | None = None
) -> np.ndarray:
    """Rotation matrices, shape (L, 3, 3), columns [right, up, facing].

    `up` is the side-chain direction and is the axis a GIBBERISH stack grows
    along, so it is preserved exactly wherever possible. When `up` and the
    backbone tangent are near parallel -- which happens in tight turns, and
    always at a terminus of a short chain -- `right` is instead taken as any
    perpendicular to `up`. That keeps the letter standing on its residue and
    only gives up on aligning it with the chain, which is the less important of
    the two properties.
    """
    ca = np.asarray(ca, dtype=np.float64)
    cb = np.asarray(cb, dtype=np.float64)
    if tangents is None:
        tangents = backbone_tangents(ca)

    up = normalise(cb - ca)
    tangents = normalise(tangents)

    cross = np.cross(up, tangents)
    # |up x tangent| is sin(angle between them): small means near parallel.
    degenerate = np.linalg.norm(cross, axis=-1) < _DEGENERATE

    right = np.where(degenerate[..., None], _orthogonal_to(up), normalise(cross))
    # Re-orthogonalise: `right` came from a cross product with a vector that is
    # only approximately perpendicular, so a Gram-Schmidt pass keeps the matrix
    # genuinely orthonormal rather than nearly so.
    right = normalise(right - np.sum(right * up, axis=-1, keepdims=True) * up)
    facing = np.cross(right, up)

    return np.stack([right, up, facing], axis=-1)


def matrices_to_quaternions(matrices: np.ndarray) -> np.ndarray:
    """(L, 3, 3) rotation matrices to (L, 4) quaternions in three.js xyzw order.

    Shipping quaternions rather than matrices is not just about payload size:
    FOLDEROL slerps between the flat-strip orientation and the folded one, and
    a slerp needs quaternions. Doing the conversion here means the client never
    has to, and never has to agree with us about how.

    Uses the branchless-per-case Shepperd method: picking the largest diagonal
    term avoids the catastrophic cancellation the naive trace formula suffers
    near a 180 degree rotation.
    """
    matrices = np.asarray(matrices, dtype=np.float64)
    single = matrices.ndim == 2
    if single:
        matrices = matrices[None]

    m = matrices
    trace = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    quats = np.empty((len(m), 4), dtype=np.float64)

    for index in range(len(m)):
        a = m[index]
        t = trace[index]
        if t > 0.0:
            s = np.sqrt(t + 1.0) * 2.0
            w = 0.25 * s
            x = (a[2, 1] - a[1, 2]) / s
            y = (a[0, 2] - a[2, 0]) / s
            z = (a[1, 0] - a[0, 1]) / s
        elif a[0, 0] > a[1, 1] and a[0, 0] > a[2, 2]:
            s = np.sqrt(1.0 + a[0, 0] - a[1, 1] - a[2, 2]) * 2.0
            w = (a[2, 1] - a[1, 2]) / s
            x = 0.25 * s
            y = (a[0, 1] + a[1, 0]) / s
            z = (a[0, 2] + a[2, 0]) / s
        elif a[1, 1] > a[2, 2]:
            s = np.sqrt(1.0 + a[1, 1] - a[0, 0] - a[2, 2]) * 2.0
            w = (a[0, 2] - a[2, 0]) / s
            x = (a[0, 1] + a[1, 0]) / s
            y = 0.25 * s
            z = (a[1, 2] + a[2, 1]) / s
        else:
            s = np.sqrt(1.0 + a[2, 2] - a[0, 0] - a[1, 1]) * 2.0
            w = (a[1, 0] - a[0, 1]) / s
            x = (a[0, 2] + a[2, 0]) / s
            y = (a[1, 2] + a[2, 1]) / s
            z = 0.25 * s
        quats[index] = (x, y, z, w)

    quats = normalise(quats)
    return quats[0] if single else quats


def centroid(coords: np.ndarray) -> np.ndarray:
    """Geometric centre, used to park the camera and centre the scene."""
    return np.asarray(coords, dtype=np.float64).mean(axis=0)


def radius_of_gyration(coords: np.ndarray) -> float:
    """Rg in Angstroms: how far to pull the camera back so the fold fits."""
    coords = np.asarray(coords, dtype=np.float64)
    return float(np.sqrt(((coords - coords.mean(axis=0)) ** 2).sum(axis=1).mean()))
