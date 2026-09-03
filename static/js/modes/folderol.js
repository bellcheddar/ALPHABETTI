/**
 * FOLDEROL: the morph from a flat 2D logo strip into the folded structure.
 *
 * Folding Of Letters Displayed En Route, Ordered Linearly.
 *
 * Frame 0 is a conventional WebLogo-style strip: letters in a line along x,
 * upright, stacked by information content. Frame 1 is the full GIBBERISH
 * arrangement. Between them, positions lerp and orientations slerp.
 *
 * The stagger is what makes it look deliberate. Every residue starting at once
 * gives a single snap that reads as a state change; delaying each residue by a
 * small multiple of its index makes the fold ripple along the chain, which is
 * both prettier and a better description of what a chain does.
 */

import * as THREE from 'three';
import { SCHEMES } from '../colours.js';

export const NAME = 'FOLDEROL';
export const SUBTITLE = 'flat logo to fold';

export const DEFAULTS = {
  stackDepth: 4,
  bitsPerAngstrom: 0.9,
  spacing: 1.5,           // Angstroms between positions in the flat strip
  staggerPerResidue: 0.0035,  // fraction of the timeline per residue index
  duration: 4.2,          // seconds for one play-through
  loop: true,
  scheme: 'chemistry',
  globalScale: 1.0,
  letterWidth: 1.15,
  letterDepth: 1.0,
};

const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export class Folderol {
  constructor(context) {
    this.context = context;
    this.options = { ...DEFAULTS };
    this.progress = 0;       // 0 flat, 1 folded
    this.playing = true;
    this._letters = null;
  }

  get legend() {
    return {
      kind: 'bits',
      text: `1 bit = ${this.options.bitsPerAngstrom.toFixed(2)} A`,
      detail: 'frame 0 is the same logo a 2D tool would draw',
    };
  }

  /**
   * Precompute both endpoints for every letter once. Rebuilding the letter list
   * every frame would allocate thousands of objects sixty times a second; this
   * way the per-frame work is a lerp and a slerp per letter and nothing else.
   */
  prepare() {
    const { data } = this.context;
    const { stackDepth, bitsPerAngstrom, spacing, globalScale, scheme } = this.options;
    const colourOf = SCHEMES[scheme].colour;

    const flatWidth = data.residues.length * spacing;
    const centroid = new THREE.Vector3().fromArray(data.centroid);
    const upright = new THREE.Quaternion();          // identity: letters face +Z

    const letters = [];
    const quaternion = new THREE.Quaternion();
    const up = new THREE.Vector3();

    for (const residue of data.residues) {
      quaternion.fromArray(residue.q);
      up.set(0, 1, 0).applyQuaternion(quaternion);
      const ca = new THREE.Vector3().fromArray(residue.ca);

      let foldedOffset = 0;
      let flatOffset = 0;
      const count = Math.min(stackDepth, residue.top.length);

      for (let k = 0; k < count; k++) {
        const height = residue.h[k] * bitsPerAngstrom * globalScale;
        if (height < 0.1) continue;
        const width = this.options.letterWidth;

        letters.push({
          aa: residue.top[k],
          residueIndex: residue.i,
          // Flat: a line along x through the structure's centroid, so the strip
          // and the fold occupy the same region of space and the camera does
          // not have to travel between them.
          flat: new THREE.Vector3(
            centroid.x - flatWidth / 2 + residue.i * spacing,
            centroid.y + flatOffset,
            centroid.z,
          ),
          folded: ca.clone().addScaledVector(up, foldedOffset),
          flatQuaternion: upright,
          foldedQuaternion: quaternion.clone(),
          scale: new THREE.Vector3(width, height, this.options.letterDepth),
          colour: colourOf({ ...residue, aa: residue.top[k] }),
          // Stagger by residue index, so the fold travels N to C.
          delay: residue.i * this.options.staggerPerResidue,
        });
        foldedOffset += height;
        flatOffset += height;
      }
    }
    this._letters = letters;
    return letters.length;
  }

  build() {
    if (!this._letters) this.prepare();
    const { field } = this.context;
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();

    field.begin();
    for (const letter of this._letters) {
      // Each letter runs its own clipped, eased sub-timeline. Clamping rather
      // than skipping means a letter whose delay has not arrived sits at the
      // flat pose instead of vanishing.
      const local = Math.min(1, Math.max(0,
        (this.progress - letter.delay) / Math.max(0.05, 1 - letter.delay)));
      const t = easeInOut(local);

      position.lerpVectors(letter.flat, letter.folded, t);
      quaternion.slerpQuaternions(letter.flatQuaternion, letter.foldedQuaternion, t);
      field.add(letter.aa, position, quaternion, letter.scale,
        letter.colour, letter.residueIndex);
    }
    field.commit();
    return { drawn: this._letters.length };
  }

  /** Called every frame by the renderer while this mode is active. */
  update(delta) {
    if (!this.playing) return false;
    this.progress += delta / this.options.duration;
    if (this.progress >= 1) {
      if (this.options.loop) this.progress = 0;
      else { this.progress = 1; this.playing = false; }
    }
    return true;
  }

  invalidate() { this._letters = null; }

  rulerColour(residue) { return SCHEMES[this.options.scheme].colour(residue); }

  tooltip(residue) {
    return [
      { key: residue.aa, value: `${residue.resnum}` },
      { key: 'information', value: `${residue.bits.toFixed(2)} bits` },
      { key: 'timeline', value: `${(this.progress * 100).toFixed(0)}%` },
    ];
  }
}
