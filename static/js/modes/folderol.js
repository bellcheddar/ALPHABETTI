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
  // Off by default. A logo that folds itself once and then holds the finished
  // structure is a demonstration; one that restarts every four seconds is a
  // screensaver you have to fight to read. The toggle is still there.
  loop: false,
  scheme: 'chemistry',
  globalScale: 1.0,
  letterWidth: 1.15,
  letterDepth: 1.0,
};

const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

// The attitude the rest of the app rests at, matching StageCamera's own.
const RESTING_ATTITUDE = [Math.sin(0.09), 0, 0, Math.cos(0.09)];

/** Shortest-arc quaternion interpolation, in the [x, y, z, w] order used here. */
function slerp(a, b, t) {
  let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  let target = b;
  if (dot < 0) { target = b.map((v) => -v); dot = -dot; }
  if (dot > 0.9995) {
    const out = a.map((v, i) => v + (target[i] - v) * t);
    const length = Math.hypot(...out);
    return out.map((v) => v / length);
  }
  const theta = Math.acos(dot);
  const sin = Math.sin(theta);
  const wa = Math.sin((1 - t) * theta) / sin;
  const wb = Math.sin(t * theta) / sin;
  return a.map((v, i) => v * wa + target[i] * wb);
}

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
    // The subject group is already centred on the structure's centroid, so the
    // strip is built about the LOCAL origin. Using the centroid here as well
    // would offset it by the centroid twice over.
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
    if (this.playing) {
      this.progress += delta / this.options.duration;
      if (this.progress >= 1) {
        if (this.options.loop) this.progress = 0;
        else { this.progress = 1; this.playing = false; }
      }
    }
    // Drive the camera only while there is a fold in progress. Once it has
    // landed, hand the camera back: this used to keep writing the folded pose
    // every frame forever, which pinned the attitude and made Fold the one mode
    // that never resumed its idle rotation. Scrubbing back below 1 takes it
    // over again.
    if (this.playing || this.progress < 1) this.pose();
    return this.playing;
  }

  /**
   * Drive the camera from the timeline, so frame 0 really is a 2D logo.
   *
   * Two things were wrong without this. The subject carries whatever attitude
   * the orbit left it at, so the "flat strip" was seen in perspective, receding
   * to one side -- it read as a diagonal smear rather than as a line of text.
   * And the strip is far wider than the protein it folds into (76 residues at
   * 1.5 A is 114 A against a fold about 30 A across), so at the structure's own
   * framing distance both ends ran off the edges of the viewport.
   *
   * So the pose is interpolated along with the letters: square-on and pulled
   * back at frame 0, easing to the structure's own framing by frame 1. The
   * camera coming IN as the chain collapses is also the right way round -- the
   * subject is getting smaller, so holding the distance would shrink it.
   */
  pose() {
    const { renderer, data } = this.context;
    if (!renderer?.setPose) return;
    const t = easeInOut(Math.min(1, Math.max(0, this.progress)));

    // Identity attitude = the strip square-on to the camera, so the text is
    // horizontal and readable. Slerped toward the structure's resting pose.
    const flat = [0, 0, 0, 1];
    const folded = RESTING_ATTITUDE;
    renderer.setPose(slerp(flat, folded, t), this._flatDistance(t));
  }

  _flatDistance(t) {
    const { renderer, data } = this.context;
    const framed = renderer.control.defaultDistance;
    // Far enough back that the whole strip fits the WIDTH of the viewport.
    const width = (data.residues.length * this.options.spacing) / 2;
    const vertical = (renderer.camera.fov * Math.PI) / 180;
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * renderer.camera.aspect);
    const flat = (width * 1.12) / Math.tan(horizontal / 2);
    return flat + (framed - flat) * t;
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
