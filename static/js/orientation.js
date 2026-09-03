/**
 * Per-residue frame maths, mirroring alphabetti/geometry.py function for function.
 *
 * The payload already carries a quaternion per residue, so nothing here is
 * needed to draw a static structure. It exists for FOLDEROL, which interpolates
 * between a flat 2D logo strip and the folded arrangement and therefore has to
 * build frames the server never computed.
 *
 * If the convention in geometry.py changes, change it here in the same commit.
 * A drift between the two shows up as letters that rotate as the morph lands,
 * which reads as a bug in the animation rather than a disagreement about maths.
 *
 * Convention, identical to the Python:
 *     up      = normalise(CB - CA)
 *     tangent = normalise(CA[i+1] - CA[i-1])
 *     right   = normalise(cross(up, tangent))
 *     facing  = cross(right, up)
 * with the rotation matrix columns [right, up, facing].
 */

import * as THREE from 'three';

// sin(angle) below this counts as parallel: the cross product that builds
// `right` is numerically worthless there. 0.05 is about 2.9 degrees.
const DEGENERATE = 0.05;

// Virtual CB constants, the standard tetrahedral construction used by trRosetta
// and AlphaFold. Validated against 1UBQ: 0.13 A mean deviation from the real CB
// across all 70 non-glycine residues.
const CB_A = -0.58273431;
const CB_B = 0.56802827;
const CB_C = -0.54067466;

const _a = new THREE.Vector3();
const _b = new THREE.Vector3();
const _c = new THREE.Vector3();

/** Reconstruct a CB from backbone N, CA and C. Glycine needs this; nothing is skipped. */
export function virtualCB(n, ca, c, out = new THREE.Vector3()) {
  _b.subVectors(ca, n);
  _c.subVectors(c, ca);
  _a.crossVectors(_b, _c);
  return out.set(0, 0, 0)
    .addScaledVector(_a, CB_A)
    .addScaledVector(_b, CB_B)
    .addScaledVector(_c, CB_C)
    .add(ca);
}

/** Any unit vector perpendicular to v, chosen so it never degenerates. */
function orthogonalTo(v, out = new THREE.Vector3()) {
  // Cross with whichever cardinal axis v is least aligned to. Crossing with a
  // fixed axis returns zero for the one input that happens to be that axis.
  const ax = Math.abs(v.x), ay = Math.abs(v.y), az = Math.abs(v.z);
  if (ax <= ay && ax <= az) out.set(1, 0, 0);
  else if (ay <= az) out.set(0, 1, 0);
  else out.set(0, 0, 1);
  return out.crossVectors(v, out).normalize();
}

const _up = new THREE.Vector3();
const _tan = new THREE.Vector3();
const _right = new THREE.Vector3();
const _facing = new THREE.Vector3();
const _matrix = new THREE.Matrix4();

/**
 * Build the quaternion for one residue.
 * @param {THREE.Vector3} ca
 * @param {THREE.Vector3} cb
 * @param {THREE.Vector3} tangent  local backbone direction, already a difference
 */
export function residueQuaternion(ca, cb, tangent, out = new THREE.Quaternion()) {
  _up.subVectors(cb, ca);
  if (_up.lengthSq() < 1e-12) _up.set(0, 1, 0);
  _up.normalize();

  _tan.copy(tangent);
  if (_tan.lengthSq() < 1e-12) _tan.set(1, 0, 0);
  _tan.normalize();

  _right.crossVectors(_up, _tan);
  if (_right.length() < DEGENERATE) {
    // Near-parallel. Keep `up` exactly (a GIBBERISH stack grows along it, so it
    // is the property worth preserving) and give up only on aligning the letter
    // with the chain.
    orthogonalTo(_up, _right);
  } else {
    _right.normalize();
  }
  // Gram-Schmidt: `right` came from a cross with a vector only approximately
  // perpendicular, so re-orthogonalise rather than trusting it.
  _right.addScaledVector(_up, -_right.dot(_up)).normalize();
  _facing.crossVectors(_right, _up);

  _matrix.makeBasis(_right, _up, _facing);
  return out.setFromRotationMatrix(_matrix);
}

/** Central-difference tangents for a whole chain, as the Python does. */
export function backboneTangents(caPositions) {
  const n = caPositions.length;
  const out = new Array(n);
  if (n === 1) { out[0] = new THREE.Vector3(1, 0, 0); return out; }
  for (let i = 0; i < n; i++) {
    const a = caPositions[Math.max(0, i - 1)];
    const b = caPositions[Math.min(n - 1, i + 1)];
    out[i] = new THREE.Vector3().subVectors(b, a).normalize();
  }
  return out;
}

/** Frames for a whole chain from scratch. Used by FOLDEROL, not by the static view. */
export function chainQuaternions(caPositions, cbPositions) {
  const tangents = backboneTangents(caPositions);
  return caPositions.map((ca, i) =>
    residueQuaternion(ca, cbPositions[i], tangents[i]));
}
