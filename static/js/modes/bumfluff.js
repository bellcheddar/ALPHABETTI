/**
 * BUMFLUFF: solvent accessibility.
 *
 * Buried/Unburied Mapping of Fonts, Letters, Uncovered Faces and Folds.
 *
 * One letter a position, its height driven by relative solvent accessibility.
 * Buried residues shrink to nubs and exposed ones bristle outward, so the
 * protein ends up looking like a hairy tribble made of vowels. That is the
 * joke; the use is real, because an exposed hydrophobic patch is where a
 * protein aggregates, where a crystal contact refuses to form, and where an
 * epitope tends to sit.
 */

import * as THREE from 'three';
import { SCHEMES, KYTE_DOOLITTLE } from '../colours.js';

export const NAME = 'BUMFLUFF';
export const SUBTITLE = 'solvent accessibility';

// The conventional buried/exposed cut-off, and alanine's own hydropathy as the
// hydrophobic threshold. Both are labelled in the legend rather than hidden.
export const BURIED_CUTOFF = 0.25;
export const HYDROPHOBIC_CUTOFF = 1.8;

export const DEFAULTS = {
  heightScale: 4.5,       // Angstroms at RSA 1.0
  minHeight: 0.35,        // so a fully buried residue is still a visible nub
  highlightPatches: true, // flag exposed hydrophobics in orange
  scheme: 'rsa',
  globalScale: 1.0,
  letterWidth: 1.15,
};

const PATCH_COLOUR = new THREE.Color(0xff6900);

export class Bumfluff {
  constructor(context) {
    this.context = context;
    this.options = { ...DEFAULTS };
  }

  get legend() {
    return {
      kind: 'rsa',
      text: `height = relative SASA x ${this.options.heightScale.toFixed(1)} A`,
      detail: `buried/exposed cut-off at RSA ${BURIED_CUTOFF}; ` +
              'maxima from Tien et al. (2013)',
    };
  }

  build() {
    const { field, data } = this.context;
    const { heightScale, minHeight, highlightPatches, scheme, globalScale,
            letterWidth } = this.options;
    const colourOf = SCHEMES[scheme].colour;

    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const up = new THREE.Vector3();

    field.begin();
    let drawn = 0;
    let patches = 0;

    for (const residue of data.residues) {
      quaternion.fromArray(residue.q);
      up.set(0, 1, 0).applyQuaternion(quaternion);
      position.fromArray(residue.ca);

      const height = Math.max(minHeight, residue.rsa * heightScale * globalScale);
      // Constant width, as in GIBBERISH: accessibility is encoded by height
      // alone, and coupling width to it would say the same thing twice while
      // making short letters illegible.
      const width = letterWidth;

      const exposedHydrophobic = highlightPatches
        && residue.rsa > BURIED_CUTOFF
        && (KYTE_DOOLITTLE[residue.aa] ?? 0) > HYDROPHOBIC_CUTOFF;
      if (exposedHydrophobic) patches++;

      field.add(residue.aa, position, quaternion,
        new THREE.Vector3(width, height, width),
        exposedHydrophobic ? PATCH_COLOUR : colourOf(residue), residue.i);
      drawn++;
    }

    field.commit();
    return { drawn, patches, positions: data.residues.length };
  }

  rulerColour(residue) {
    if (this.options.highlightPatches
        && residue.rsa > BURIED_CUTOFF
        && (KYTE_DOOLITTLE[residue.aa] ?? 0) > HYDROPHOBIC_CUTOFF) {
      return PATCH_COLOUR;
    }
    return SCHEMES[this.options.scheme].colour(residue);
  }

  tooltip(residue) {
    const kd = KYTE_DOOLITTLE[residue.aa] ?? 0;
    return [
      { key: residue.aa, value: `${residue.resnum}` },
      { key: 'relative SASA', value: residue.rsa.toFixed(3) },
      { key: 'SASA', value: `${residue.sasa.toFixed(0)} A²` },
      { key: 'state', value: residue.rsa > BURIED_CUTOFF ? 'exposed' : 'buried' },
      { key: 'Kyte-Doolittle', value: kd.toFixed(1) },
    ];
  }
}
