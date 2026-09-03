/**
 * Colour schemes. Every scheme is a function from a residue record to a hex
 * colour, plus the metadata the legend needs to describe itself honestly.
 *
 * Section 9 of the build spec: sequential scales for magnitude, diverging for
 * deviation. That is why pLDDT and RSA are sequential ramps while the variant
 * score is diverging about zero, and why every ramp states its endpoints.
 */

import * as THREE from 'three';

const c = (hex) => new THREE.Color(hex);

/** Taylor's amino acid colours, the convention most structural biologists read fluently. */
export const CHEMISTRY = {
  A: 0xc8c8c8, R: 0x145aff, N: 0x00dcdc, D: 0xe60a0a, C: 0xe6e600,
  E: 0xe60a0a, Q: 0x00dcdc, G: 0xebebeb, H: 0x8282d2, I: 0x0f820f,
  L: 0x0f820f, K: 0x145aff, M: 0xe6e6e6, F: 0x3232aa, P: 0xdc9682,
  S: 0xfa9600, T: 0xfa9600, W: 0xb45ab4, Y: 0x3232aa, V: 0x0f820f,
  X: 0x808080,
};

/** Clustal X, for people who read alignments more than structures. */
export const CLUSTAL = {
  A: 0x80a0f0, R: 0xf01505, N: 0x00ff00, D: 0xc048c0, C: 0xf08080,
  E: 0xc048c0, Q: 0x00ff00, G: 0xf09048, H: 0x15a4a4, I: 0x80a0f0,
  L: 0x80a0f0, K: 0xf01505, M: 0x80a0f0, F: 0x80a0f0, P: 0xffff00,
  S: 0x00ff00, T: 0x00ff00, W: 0x80a0f0, Y: 0x15a4a4, V: 0x80a0f0,
  X: 0x808080,
};

/** Secondary structure, matching the convention used by most viewers. */
export const SS_COLOURS = { H: 0xff4d6d, E: 0xffd166, C: 0x9aa5b1 };

// Kyte-Doolittle, duplicated from accessibility.py because the ruler colours
// residues client side. Range -4.5 (Arg) to +4.5 (Ile).
export const KYTE_DOOLITTLE = {
  A: 1.8, R: -4.5, N: -3.5, D: -3.5, C: 2.5, E: -3.5, Q: -3.5, G: -0.4,
  H: -3.2, I: 4.5, L: 3.8, K: -3.9, M: 1.9, F: 2.8, P: -1.6, S: -0.8,
  T: -0.7, W: -0.9, Y: -1.3, V: 4.2, X: 0.0,
};

const clamp01 = (x) => Math.min(1, Math.max(0, x));

/** Interpolate through an array of hex stops. */
function ramp(stops, t) {
  t = clamp01(t);
  const span = stops.length - 1;
  const i = Math.min(span - 1, Math.floor(t * span));
  const local = t * span - i;
  return c(stops[i]).lerp(c(stops[i + 1]), local);
}

// AlphaFold's own pLDDT ramp, so anyone who has looked at an AlphaFold model
// reads this without being told: orange is unreliable, dark blue is confident.
const PLDDT_STOPS = [0xff7d45, 0xffdb13, 0x65cbf3, 0x0053d6];
// Buried to exposed. Diverging is wrong here (there is no meaningful zero) but
// the 0.25 cut-off is a real threshold, so the legend marks it.
const RSA_STOPS = [0x08306b, 0x2171b5, 0x9ecae1, 0xfdae6b, 0xf16913];

export const SCHEMES = {
  chemistry: {
    label: 'Chemistry (Taylor)',
    kind: 'categorical',
    colour: (r) => c(CHEMISTRY[r.aa] ?? CHEMISTRY.X),
    legend: () => ({ type: 'swatches', entries: Object.entries(CHEMISTRY)
      .filter(([k]) => k !== 'X').map(([k, v]) => ({ label: k, colour: v })) }),
  },
  clustal: {
    label: 'Clustal',
    kind: 'categorical',
    colour: (r) => c(CLUSTAL[r.aa] ?? CLUSTAL.X),
    legend: () => ({ type: 'swatches', entries: Object.entries(CLUSTAL)
      .filter(([k]) => k !== 'X').map(([k, v]) => ({ label: k, colour: v })) }),
  },
  plddt: {
    label: 'pLDDT (confidence)',
    kind: 'sequential',
    colour: (r) => ramp(PLDDT_STOPS, r.plddt / 100),
    legend: () => ({ type: 'ramp', stops: PLDDT_STOPS, min: 0, max: 100,
      unit: 'pLDDT', ticks: [
        { at: 0.5, label: '50 · low' }, { at: 0.7, label: '70' },
        { at: 0.9, label: '90 · high' }] }),
  },
  ss: {
    label: 'Secondary structure',
    kind: 'categorical',
    colour: (r) => c(SS_COLOURS[r.ss] ?? SS_COLOURS.C),
    legend: () => ({ type: 'swatches', entries: [
      { label: 'Helix', colour: SS_COLOURS.H },
      { label: 'Strand', colour: SS_COLOURS.E },
      { label: 'Coil', colour: SS_COLOURS.C }] }),
  },
  rsa: {
    label: 'Solvent accessibility',
    kind: 'sequential',
    colour: (r) => ramp(RSA_STOPS, r.rsa),
    legend: () => ({ type: 'ramp', stops: RSA_STOPS, min: 0, max: 1,
      unit: 'relative SASA', ticks: [
        { at: 0.25, label: '0.25 · buried/exposed' }, { at: 1, label: '1.0' }] }),
  },
  hydrophobicity: {
    label: 'Hydrophobicity (Kyte-Doolittle)',
    kind: 'diverging',
    // Diverging about zero, because the sign is the meaningful thing: this is a
    // deviation scale, not a magnitude one.
    colour: (r) => {
      const kd = KYTE_DOOLITTLE[r.aa] ?? 0;
      const t = (kd + 4.5) / 9;
      return ramp([0x2166ac, 0xd1e5f0, 0xf7f7f7, 0xfddbc7, 0xb2182b], t);
    },
    legend: () => ({ type: 'ramp',
      stops: [0x2166ac, 0xd1e5f0, 0xf7f7f7, 0xfddbc7, 0xb2182b],
      min: -4.5, max: 4.5, unit: 'Kyte-Doolittle',
      ticks: [{ at: 0.5, label: '0' }, { at: 0.7, label: '+1.8 · hydrophobic' }] }),
  },
};

export const DEFAULT_SCHEME = 'chemistry';

/** Diverging ramp for BALDERDASH's substitution matrix: blue tolerated, red deleterious. */
export function substitutionColour(score, extent = 8) {
  const t = clamp01((score + extent) / (2 * extent));
  return ramp([0x67001f, 0xd6604d, 0xf7f7f7, 0x4393c3, 0x053061], t);
}

/** CSS hex string, for the sequence ruler and the DOM legend. */
export function cssColour(colour) {
  return `#${colour.getHexString()}`;
}
