/**
 * Glyph geometry and instanced rendering.
 *
 * The performance shape of this app is decided here. A 400-residue protein in
 * GIBBERISH with four letters a position is 1,600 glyphs, and 1,600 meshes
 * would be 1,600 draw calls, which no phone will render at 30 fps. Instead
 * there is ONE InstancedMesh per amino acid type: twenty draw calls total,
 * regardless of how long the protein is or how deep the stacks go.
 *
 * The consequence to keep in mind when editing: an instance is a transform and
 * a colour, nothing else. Anything that has to vary per letter beyond position,
 * orientation, scale and colour needs either a second instanced mesh or a
 * shader, not a per-letter material.
 */

import * as THREE from 'three';
import { FontLoader } from 'three/addons/loaders/FontLoader.js';

const ALPHABET = 'ACDEFGHIKLMNPQRSTVWY';
const UNKNOWN = 'X';

// Extrusion depth in Angstroms. Deep enough to catch a rim light and read as an
// object rather than a decal, shallow enough that a dense stack does not become
// a solid brick.
const DEPTH = 0.35;
const BEVEL = 0.045;

// The font is authored at 1000 units per em. Glyph geometries are normalised to
// a nominal cap height of 1.0 so that a scale factor passed in from a mode is
// directly a height in Angstroms, and the maths in the modes stays readable.
const NOMINAL_CAP_HEIGHT = 1.0;

export class GlyphSet {
  constructor(font) {
    this.font = font;
    this.geometries = new Map();
    this._build();
  }

  static async load(url) {
    const font = await new FontLoader().loadAsync(url);
    return new GlyphSet(font);
  }

  _build() {
    // Cap height is measured once from a flat-topped letter rather than taken
    // from the font metrics: ascender includes room for diacritics this app
    // will never draw, and using it would make every letter mysteriously short.
    const probe = this._raw('H');
    probe.computeBoundingBox();
    const capHeight = probe.boundingBox.max.y - probe.boundingBox.min.y;
    probe.dispose();
    const scale = NOMINAL_CAP_HEIGHT / capHeight;

    for (const character of ALPHABET + UNKNOWN) {
      const geometry = this._raw(character);

      // Every glyph is normalised to the SAME box: one unit wide and one unit
      // tall. Height uses a shared cap height so the letters sit on a common
      // baseline and cap line; width is normalised per glyph, which stretches
      // I and L out to the width of W.
      //
      // That is not a liberty, it is what a sequence logo is. WebLogo sets
      // every letter to a uniform width so that a column reads as a column.
      // Normalising height alone leaves the narrow letters as 8:1 needles that
      // cannot be identified at all, which is exactly how the first render
      // looked: a fistful of coloured splinters.
      geometry.scale(scale, scale, scale);
      geometry.computeBoundingBox();
      const natural = geometry.boundingBox.max.x - geometry.boundingBox.min.x;
      if (natural > 1e-6) geometry.scale(NOMINAL_CAP_HEIGHT / natural, 1, 1);

      geometry.computeBoundingBox();
      const box = geometry.boundingBox;

      // Centre on x and z, but sit the letter's FEET on y = 0 rather than
      // centring it. A GIBBERISH stack is cumulative: each letter stands on the
      // one below, so the anchor has to be the baseline. Centring here would
      // make every stack overlap itself by half a letter.
      geometry.translate(
        -(box.min.x + box.max.x) / 2,
        -box.min.y,
        -(box.min.z + box.max.z) / 2,
      );
      geometry.computeBoundingBox();
      geometry.computeVertexNormals();
      this.geometries.set(character, geometry);
    }
  }

  _raw(character) {
    const shapes = this.font.generateShapes(character, 1);
    return new THREE.ExtrudeGeometry(shapes, {
      depth: DEPTH,
      bevelEnabled: true,
      bevelThickness: BEVEL,
      bevelSize: BEVEL,
      bevelSegments: 2,
      curveSegments: 5,
    });
  }

  get(character) {
    return this.geometries.get(character) ?? this.geometries.get(UNKNOWN);
  }

  /** Height of a letter at scale 1, so a mode can work out where the next one sits. */
  heightOf(character) {
    const box = this.get(character).boundingBox;
    return box.max.y - box.min.y;
  }

  dispose() {
    for (const geometry of this.geometries.values()) geometry.dispose();
    this.geometries.clear();
  }
}

/**
 * One InstancedMesh per amino acid, filled a letter at a time.
 *
 * Usage each frame that the display changes:
 *     field.begin();
 *     for (...) field.add(aa, position, quaternion, scale, colour, residueIndex);
 *     field.commit();
 *
 * `begin` resets the per-letter counters, `add` appends, and `commit` uploads
 * the buffers and sets each mesh's count. Nothing is allocated in between,
 * which is what keeps FOLDEROL's per-frame rebuild from generating garbage.
 */
export class GlyphField {
  constructor(glyphSet, material, capacity = 4096) {
    this.glyphSet = glyphSet;
    this.group = new THREE.Group();
    this.meshes = new Map();
    this.counts = new Map();
    // Maps a flat instance id back to a residue index, so a raycast hit can say
    // which residue was clicked. Without it, picking returns an instanceId that
    // means nothing outside this class.
    this.residueOf = new Map();

    this._matrix = new THREE.Matrix4();
    this._scale = new THREE.Vector3();
    this._colour = new THREE.Color();

    for (const character of ALPHABET + UNKNOWN) {
      const mesh = new THREE.InstancedMesh(
        glyphSet.get(character), material, capacity,
      );
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.count = 0;
      mesh.frustumCulled = false;   // instances move; the mesh bounds do not follow
      mesh.userData.aa = character;
      this.meshes.set(character, mesh);
      this.counts.set(character, 0);
      this.residueOf.set(character, []);
      this.group.add(mesh);
    }
  }

  begin() {
    for (const character of this.counts.keys()) {
      this.counts.set(character, 0);
      this.residueOf.get(character).length = 0;
    }
  }

  /**
   * @param {string} aa            single-letter residue code
   * @param {THREE.Vector3} position
   * @param {THREE.Quaternion} quaternion
   * @param {number|THREE.Vector3} scale  uniform, or per-axis
   * @param {THREE.Color} colour
   * @param {number} residueIndex  what a raycast hit should report
   */
  add(aa, position, quaternion, scale, colour, residueIndex) {
    const character = this.meshes.has(aa) ? aa : UNKNOWN;
    const mesh = this.meshes.get(character);
    const index = this.counts.get(character);
    if (index >= mesh.instanceMatrix.count) return false;   // capacity reached

    if (typeof scale === 'number') this._scale.set(scale, scale, scale);
    else this._scale.copy(scale);

    this._matrix.compose(position, quaternion, this._scale);
    mesh.setMatrixAt(index, this._matrix);
    mesh.setColorAt(index, colour);
    this.residueOf.get(character)[index] = residueIndex;
    this.counts.set(character, index + 1);
    return true;
  }

  commit() {
    for (const [character, mesh] of this.meshes) {
      mesh.count = this.counts.get(character);
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.computeBoundingSphere();
    }
  }

  /** Total letters currently drawn, for the diagnostics readout. */
  total() {
    let sum = 0;
    for (const count of this.counts.values()) sum += count;
    return sum;
  }

  /** Turn a raycast intersection into a residue index, or null. */
  residueFromHit(hit) {
    const aa = hit.object?.userData?.aa;
    if (!aa || hit.instanceId === undefined) return null;
    const index = this.residueOf.get(aa)?.[hit.instanceId];
    return index === undefined ? null : index;
  }

  get objects() {
    return [...this.meshes.values()];
  }

  setMaterial(material) {
    for (const mesh of this.meshes.values()) mesh.material = material;
  }

  dispose() {
    for (const mesh of this.meshes.values()) {
      mesh.dispose();
      this.group.remove(mesh);
    }
    this.meshes.clear();
  }
}

export { ALPHABET, DEPTH };
