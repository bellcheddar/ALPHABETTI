/**
 * HOGWASH: a real WebLogo, made immersive.
 *
 * Height-Ordered Glyphs Weighted Across Sequence Homologues.
 *
 * The one tab that uses an alignment. Every other one asks a protein language
 * model what it expects and uses no alignment anywhere, which is GIBBERISH's
 * whole point; this one counts what evolution actually did in a column of
 * aligned homologues. Both are here so they can be compared.
 *
 * The numbers are WebLogo 3's, unmodified (Crooks, Hon, Chandonia & Brenner,
 * Genome Research 14:1188-1190, 2004; MIT). What this file adds is the thing a
 * flat PNG cannot be: a logo you can walk around.
 *
 * The flat WebLogo is still exactly what it always was, and still downloadable
 * in all seven formats. It is simply no longer the way you LOOK at the thing.
 * A 231-column alignment as a static image is six stacked rows of two-millimetre
 * letters; the same alignment as a lit ring you can turn is readable.
 */

import * as THREE from 'three';
import { SCHEMES } from '../colours.js';

export const NAME = 'HOGWASH';

// WebLogo's own colour schemes, so the 3D logo and the downloaded one agree.
// Taken from weblogo/colorscheme.py rather than invented.
const WEBLOGO_CHEMISTRY = {
  G: 0x00c000, S: 0x00c000, T: 0x00c000, Y: 0x00c000, C: 0x00c000,
  N: 0xc000c0, Q: 0xc000c0,
  K: 0x0000c0, R: 0x0000c0, H: 0x0000c0,
  D: 0xc00000, E: 0xc00000,
  P: 0x000000, A: 0x000000, W: 0x000000, F: 0x000000, L: 0x000000,
  I: 0x000000, M: 0x000000, V: 0x000000,
};
const WEBLOGO_NUCLEOTIDE = { A: 0x00c000, C: 0x0000c0, G: 0xffb300, T: 0xc00000, U: 0xc00000 };
const WEBLOGO_CHARGE = {
  K: 0x0000c0, R: 0x0000c0, H: 0x0000c0, D: 0xc00000, E: 0xc00000,
};

// Chemistry's blacks are invisible on this app's near-black ground. WebLogo
// draws on white and that palette is right there; here the same GROUPS are kept
// and only the black one is lifted to a pale neutral, so the grouping a reader
// knows still holds and the letters can still be seen.
const NEUTRAL_ON_DARK = 0xd8dcea;

export const LAYOUTS = {
  ring: 'Ring',
  helix: 'Helix',
  strip: 'Strip',
  rows: 'Rows',
};

export const DEFAULTS = {
  // ---- what the numbers mean (server side; changing these re-runs WebLogo)
  unit_name: 'bits',
  composition: 'auto',
  small_sample_correction: true,
  alphabet: 'auto',
  input_format: 'auto',
  ignore_lower_case: false,

  // ---- the 3D arrangement
  // Rows on arrival: the shape a reader already knows, wrapped the way WebLogo
  // wraps past 40 stacks. The helix and the ring are the interesting views and
  // are one click away, but landing on a spiral asks someone to learn the
  // display before they can read the data.
  layout: 'rows',
  // How many times the coil goes round, and how far it climbs per turn. Rise
  // has to clear the tallest stack or consecutive turns grow through each other.
  helixTurns: 3,
  helixRise: 15,
  stackDepth: 5,
  bitsPerAngstrom: 2.2,
  minBits: 0.0,
  letterWidth: 1.15,
  columnSpacing: 2.4,
  rowsPerWrap: 40,
  scheme: 'weblogo',
  showErrorCaps: false,
  globalScale: 1.0,

  // ---- the downloadable flat logo only
  color_scheme: 'auto',
  show_errorbars: false,
  show_boxes: false,
  stacks_per_line: 40,
  logo_title: '',
  first_index: 1,
};

export class Hogwash {
  constructor(context) {
    this.context = context;
    this.options = { ...DEFAULTS };
    this.alignment = null;      // raw text, if pasted or uploaded
    this.example = null;        // or the name of a bundled one
    this.source = null;         // for captions and filenames
    this.data = null;           // WebLogo's per-column numbers
    this._placed = [];          // column index -> world position, for flying to
  }

  get legend() {
    const a = this.data?.alignment;
    if (!a) {
      return { kind: 'weblogo', text: 'no alignment loaded',
               detail: 'load an example, paste one, or fetch a family' };
    }
    return {
      kind: 'weblogo',
      text: `${a.sequences} sequences x ${a.columns} columns`,
      detail: `${a.alphabet}, ceiling ${this.data.max_bits} ${this.options.unit_name}`
            + ` · ${LAYOUTS[this.options.layout].toLowerCase()} layout`,
    };
  }

  /** The consensus residue per column, for the ruler along the bottom. */
  get consensus() {
    return (this.data?.columns || []).map((c) => c.stack[0]?.aa || '-').join('');
  }

  colourFor(letter, column) {
    const { scheme } = this.options;
    if (scheme === 'weblogo') {
      const dna = this.data?.alignment?.alphabet !== 'protein';
      const table = dna ? WEBLOGO_NUCLEOTIDE : WEBLOGO_CHEMISTRY;
      const hex = table[letter];
      return new THREE.Color(hex === 0x000000 || hex === undefined
        ? NEUTRAL_ON_DARK : hex);
    }
    if (scheme === 'charge') {
      return new THREE.Color(WEBLOGO_CHARGE[letter] ?? NEUTRAL_ON_DARK);
    }
    if (scheme === 'conservation') {
      // One hue, lightness by how conserved the column is. Makes the shape of
      // the conservation read at a glance without the letters competing.
      const t = Math.min(1, (column?.bits ?? 0) / (this.data?.max_bits || 4.322));
      return new THREE.Color(0x0d2b3e).lerp(new THREE.Color(0x26f5e0), t);
    }
    const app = SCHEMES[scheme];
    if (app) return app.colour({ aa: letter, plddt: 50, rsa: 0.5, ss: 'C', bits: 0 });
    return new THREE.Color(NEUTRAL_ON_DARK);
  }

  /**
   * Where each column sits, and which way it faces.
   *
   * Four arrangements, because one shape does not suit every alignment. A
   * 20-column motif wants a strip; a 231-column family wrapped into a strip is
   * 550 Angstroms wide and unreadable at any distance that fits it on screen.
   *
   *   ring   columns around a circle, stacks growing outward, letters facing
   *          out. You can orbit it or drop into the middle of it.
   *   helix  the same, rising as it goes: a tower. Long alignments stay dense
   *          without any column being far from the eye.
   *   strip  what WebLogo draws, in three dimensions.
   *   rows   the strip wrapped, as WebLogo wraps it past 40 stacks.
   */
  _layout(index, total) {
    const { layout, columnSpacing, rowsPerWrap } = this.options;
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const up = new THREE.Vector3(0, 1, 0);

    if (layout === 'strip' || layout === 'rows') {
      const perRow = layout === 'rows' ? Math.max(4, rowsPerWrap) : total;
      const row = Math.floor(index / perRow);
      const column = index % perRow;
      const rows = Math.ceil(total / perRow);
      position.set(
        (column - Math.min(perRow, total) / 2) * columnSpacing,
        (rows - 1) / 2 * 14 - row * 14,
        0,
      );
      return { position, quaternion, up };
    }

    // Ring and helix: put the columns on a circle big enough that neighbouring
    // stacks do not overlap, rather than a fixed radius that crowds a long
    // alignment into itself.
    const wrap = layout === 'helix'
      ? Math.min(total, Math.max(24, Math.ceil(total / this.options.helixTurns)))
      : total;
    const radius = Math.max(12, (wrap * columnSpacing) / (2 * Math.PI));

    // Turns as a CONTINUOUS fraction, not a floor.
    //
    // This used to be `angle = (index % wrap)` with `y = floor(index / wrap) *
    // rise`, which is not a helix: it is a stack of flat rings that jump a whole
    // turn's height at the seam and then sit level all the way round. Letting
    // both the angle and the height run off the same unrounded value gives one
    // continuous coil, and the seam disappears because there is no longer a
    // seam to disappear.
    const turned = index / wrap;
    const totalTurns = total / wrap;
    const angle = turned * Math.PI * 2;

    position.set(
      Math.sin(angle) * radius,
      layout === 'helix'
        ? (turned - totalTurns / 2) * this.options.helixRise
        : 0,
      Math.cos(angle) * radius,
    );
    // Face outward, so the letters read from outside the coil.
    quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), angle);
    return { position, quaternion, up };
  }

  build() {
    const { field } = this.context;
    field.begin();
    this._placed = [];
    if (!this.data) { field.commit(); return { drawn: 0, columns: 0 }; }

    const { stackDepth, bitsPerAngstrom, minBits, letterWidth, globalScale } = this.options;
    const columns = this.data.columns;
    const position = new THREE.Vector3();
    const offset = new THREE.Vector3();
    let drawn = 0;

    for (const column of columns) {
      const place = this._layout(column.i, columns.length);
      this._placed[column.i] = place.position.clone();
      if (column.bits < minBits) continue;

      // Cumulative, tallest first, exactly as a logo stacks: the total height
      // of a column IS its information content.
      let raised = 0;
      const count = Math.min(stackDepth, column.stack.length);
      for (let k = 0; k < count; k++) {
        const entry = column.stack[k];
        const height = entry.h * bitsPerAngstrom * globalScale;
        if (height < 0.08) continue;
        position.copy(place.position)
          .add(offset.copy(place.up).multiplyScalar(raised));
        field.add(entry.aa, position, place.quaternion,
          new THREE.Vector3(letterWidth, height, letterWidth),
          this.colourFor(entry.aa, column), column.i);
        raised += height;
        drawn++;
      }
    }

    field.commit();
    return { drawn, columns: columns.length };
  }

  /** True when this layout is a flat sheet meant to be read square-on. */
  get isFlat() {
    return this.options.layout === 'rows' || this.options.layout === 'strip';
  }

  /** Where a column ended up, so the camera can be sent there. */
  positionOf(index) {
    return this._placed[index] || null;
  }

  /**
   * How far back the camera should sit. NOT far enough to fit the whole thing.
   *
   * Framing a ring of 231 columns so that all of it is on screen puts the
   * camera 120 Angstroms away from letters that are four Angstroms tall, and
   * they come out two pixels high: technically the whole logo, legibly nothing.
   * A logo you can walk around is only worth having if you are close enough to
   * read it, so the ring and the helix are framed against the near wall and the
   * far side is left to fall away behind it.
   */
  preferredDistance() {
    const columns = this.data?.columns?.length || 0;
    const camera = this.context.renderer?.camera;
    if (!columns || !camera) return null;

    const { layout, columnSpacing } = this.options;
    const vertical = (camera.fov * Math.PI) / 180;
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * camera.aspect);
    // Tallest stack, so the framing accounts for the letters rather than only
    // for where their feet are.
    const tall = Math.max(...this.data.columns.map((c) => c.bits))
               * this.options.bitsPerAngstrom * this.options.globalScale;

    if (layout === 'strip' || layout === 'rows') {
      // Fit the BOX, one axis at a time. frameStructure fits a bounding sphere,
      // which is right for a globular protein and wrong for a wide flat strip:
      // it reserves as much vertical room as horizontal, so a 40-column logo
      // 96 Angstroms wide got framed from 143 away and came out unreadable.
      const perRow = layout === 'rows'
        ? Math.min(columns, Math.max(4, this.options.rowsPerWrap)) : columns;
      const rows = Math.ceil(columns / perRow);
      const width = perRow * columnSpacing;
      const height = rows === 1 ? tall : (rows - 1) * 14 + tall;
      // 1.35, not a snug fit: the mode buttons, the view controls and the
      // legend all sit over the canvas, and a logo framed edge to edge runs
      // underneath them.
      return 1.35 * Math.max(
        (width / 2) / Math.tan(horizontal / 2),
        (height / 2) / Math.tan(vertical / 2),
      );
    }

    // Ring and helix are framed against their NEAR WALL, not in full.
    //
    // Fitting a 231-column ring entirely on screen puts the camera 120
    // Angstroms from letters four Angstroms tall, and they render two pixels
    // high: technically the whole logo, legibly nothing. A logo worth walking
    // around has to be close enough to read, so the far side is allowed to fall
    // away behind the near one.
    const wrap = layout === 'helix'
      ? Math.min(columns, Math.max(24, Math.ceil(columns / this.options.helixTurns)))
      : columns;
    const radius = Math.max(12, (wrap * columnSpacing) / (2 * Math.PI));
    // Close enough that about a fifth of the circumference fills the view, but
    // far enough that the whole climb of the coil is still in frame.
    const arc = Math.max(18, (wrap / 5) * columnSpacing);
    const comfortable = (arc / 2) / Math.tan(horizontal / 2);
    const climb = layout === 'helix'
      ? ((columns / wrap) * this.options.helixRise + tall) / 2 / Math.tan(vertical / 2)
      : 0;
    return Math.max(radius + Math.max(tall * 2.2, comfortable), climb);
  }

  rulerColour(residue) {
    const column = this.data?.columns?.[residue.i];
    if (!column) return new THREE.Color(0x6b7398);
    return this.colourFor(column.stack[0]?.aa || '-', column);
  }

  tooltip(residue) {
    const column = this.data?.columns?.[residue.i];
    if (!column) return [{ key: '?', value: '' }];
    const observed = column.stack.slice(0, 4)
      .map((s) => `${s.aa} ${s.n}`).join('  ');
    return [
      { key: column.stack[0]?.aa || '-', value: `column ${column.number}` },
      { key: this.options.unit_name, value: column.bits.toFixed(2) },
      { key: '95% interval', value: `${column.lo.toFixed(2)} to ${column.hi.toFixed(2)}` },
      { key: 'counts', value: observed },
      { key: 'sequences', value: `${column.observed} of ${this.data.alignment.sequences}` },
    ];
  }
}
