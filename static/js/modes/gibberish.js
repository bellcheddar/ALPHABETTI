/**
 * GIBBERISH: the 3D sequence logo, and the headline feature.
 *
 * Glyph Interface for Bits, Entropy and Residue Information in Structural Homology.
 *
 * At each position the top N amino acids are stacked along the residue's `up`
 * vector, tallest at the bottom, exactly as a 2D logo stacks them along y. Each
 * letter's height is p_a * R_i bits converted to Angstroms, so the total height
 * of a stack IS that position's information content. That is the property that
 * makes the picture mean something rather than merely look like something, and
 * it is the one the test suite checks.
 *
 * The scientifically novel part, and the thing the UI says out loud: there is
 * no multiple sequence alignment anywhere in this pipeline. A conventional logo
 * measures conservation by counting residues in an alignment column. This asks
 * a protein language model what it expects at each position, having masked that
 * position so it cannot simply read the answer off its own input.
 */

import * as THREE from 'three';
import { SCHEMES } from '../colours.js';

export const NAME = 'GIBBERISH';
export const SUBTITLE = 'information content';

export const DEFAULTS = {
  stackDepth: 4,          // letters per position, 1-6
  bitsPerAngstrom: 0.9,   // 1 bit = 0.9 A, so a maximal stack is about 3.9 A
  minBits: 0.0,           // hides noise
  useProbability: false,  // heights by raw probability instead of bits
  scheme: 'chemistry',    // comparing residue identities is the point here
  globalScale: 1.0,
  // Constant across every letter. A logo encodes probability on the height
  // axis alone; these two only decide how legible the letters are.
  letterWidth: 1.15,
  letterDepth: 1.0,
};

export class Gibberish {
  constructor(context) {
    this.context = context;          // { field, glyphSet, renderer, data }
    this.options = { ...DEFAULTS };
  }

  get legend() {
    const { bitsPerAngstrom, useProbability } = this.options;
    if (useProbability) {
      return { kind: 'bits', text: 'height = probability (not bits)',
        detail: 'Stack totals no longer equal information content.' };
    }
    return {
      kind: 'bits',
      text: `1 bit = ${bitsPerAngstrom.toFixed(2)} A`,
      detail: `maximum 4.322 bits = log2(20), a stack of ${(4.3219 * bitsPerAngstrom).toFixed(1)} A`,
      bar: { bits: 1, angstroms: bitsPerAngstrom },
    };
  }

  build() {
    const { field, glyphSet, data } = this.context;
    const { stackDepth, bitsPerAngstrom, minBits, useProbability,
            scheme, globalScale, letterWidth, letterDepth } = this.options;
    const colourOf = SCHEMES[scheme].colour;

    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const up = new THREE.Vector3();
    const ca = new THREE.Vector3();

    field.begin();
    let drawn = 0;

    for (const residue of data.residues) {
      if (residue.bits < minBits) continue;

      ca.fromArray(residue.ca);
      quaternion.fromArray(residue.q);
      // The stack grows along the frame's own +y, which is the CB direction.
      up.set(0, 1, 0).applyQuaternion(quaternion);

      // Cumulative: each letter stands on the one below, so the total height of
      // the stack equals the position's information content. Resetting the
      // offset per letter instead would give N letters all starting at the
      // backbone, which looks similar at a glance and means nothing.
      let offset = 0;
      const count = Math.min(stackDepth, residue.top.length);

      for (let k = 0; k < count; k++) {
        const aa = residue.top[k];
        const heightBits = useProbability ? residue.p[k] * 4.3219 : residue.h[k];
        const height = heightBits * bitsPerAngstrom * globalScale;
        // Below about a tenth of an Angstrom a letter is a speck that costs an
        // instance and adds nothing. Skipping keeps a 400-residue scene light.
        if (height < 0.1) continue;

        // Glyph geometry is normalised to a cap height of 1 with its feet at
        // y = 0, so a y scale of `height` is literally the letter's height in
        // Angstroms.
        //
        // Width and depth are CONSTANT and deliberately not tied to height.
        // That is what a sequence logo is: probability is encoded on one axis
        // only, so the letters at a position form a column of equal width whose
        // total height is the information content. Scaling width with height
        // as well encodes the same number twice, and it squashes every
        // low-probability letter into an unreadable disc -- which is exactly
        // what the first render looked like.
        position.copy(ca).addScaledVector(up, offset);

        field.add(aa, position, quaternion,
          new THREE.Vector3(letterWidth, height, letterDepth),
          colourOf({ ...residue, aa }), residue.i);

        offset += height;
        drawn++;
      }
    }

    field.commit();
    return { drawn, positions: data.residues.length };
  }

  /** Colour a residue in the sequence ruler, so 3D and ruler always agree. */
  rulerColour(residue) {
    return SCHEMES[this.options.scheme].colour(residue);
  }

  tooltip(residue) {
    const letters = residue.top.slice(0, this.options.stackDepth)
      .map((aa, k) => `${aa} ${(residue.p[k] * 100).toFixed(0)}%`).join('  ');
    return [
      { key: residue.aa, value: `${residue.resnum}` },
      { key: 'information', value: `${residue.bits.toFixed(2)} bits` },
      { key: 'entropy', value: `${residue.entropy.toFixed(2)} bits` },
      { key: 'model expects', value: letters },
      { key: 'pLDDT', value: residue.plddt.toFixed(0) },
    ];
  }
}
