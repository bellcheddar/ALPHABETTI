/**
 * HOGWASH: a real WebLogo, from a real alignment.
 *
 * Height-Ordered Glyphs Weighted Across Sequence Homologues.
 *
 * The odd one out, and deliberately. Every other tab derives from a protein
 * language model and uses no alignment at all, which is GIBBERISH's whole
 * point. This one counts what evolution actually did in a column of aligned
 * homologues, which is the older and better-established idea, and it is here so
 * the two can be put side by side.
 *
 * The numbers and the flat drawing both come from WebLogo 3 (Crooks, Hon,
 * Chandonia & Brenner, Genome Research 14:1188-1190, 2004), MIT licensed and
 * used unmodified. Nothing here reimplements any of it: the server hands back
 * WebLogo's own per-column counts and entropies, and the rendered logo is
 * WebLogo's own output. See THIRD-PARTY.md.
 */

import * as THREE from 'three';

export const NAME = 'HOGWASH';

// The ruler shows the structure's own sequence, which is a different coordinate
// system from the alignment's columns.
const NEUTRAL = new THREE.Color(0x6b7398);

export const DEFAULTS = {
  // What the alignment is measured against and how. These change the NUMBERS,
  // so touching one re-runs the maths on the server.
  unit_name: 'bits',
  composition: 'auto',
  small_sample_correction: true,
  alphabet: 'auto',
  input_format: 'auto',
  ignore_lower_case: false,

  // These change only the drawing.
  color_scheme: 'auto',
  show_errorbars: false,
  show_boxes: false,
  stacks_per_line: 40,
  logo_title: '',
  yaxis_scale: null,
  first_index: 1,
};

export class Hogwash {
  constructor(context) {
    this.context = context;
    this.options = { ...DEFAULTS };
    this.alignment = null;      // the raw text
    this.source = null;         // where it came from, for the caption
    this.data = null;           // WebLogo's per-column numbers
  }

  get legend() {
    const a = this.data?.alignment;
    return {
      kind: 'weblogo',
      text: a ? `${a.sequences} sequences x ${a.columns} columns`
              : 'no alignment loaded',
      detail: a
        ? `${a.alphabet} alphabet, ceiling ${this.data.max_bits} ${this.options.unit_name}`
        : 'paste one, upload a file, or load an example',
    };
  }

  /** HOGWASH draws no glyphs: it clears the field and shows a 2D sheet. */
  build() {
    const { field } = this.context;
    field.begin();
    field.commit();
    return { drawn: 0, columns: this.data?.columns?.length ?? 0 };
  }

  /**
   * The colour of a residue in the sequence ruler.
   *
   * The ruler shows the STRUCTURE's sequence, not the alignment's columns, and
   * the two are different things: an alignment has gaps and usually covers only
   * part of the chain. Until that mapping exists this stays neutral rather than
   * colouring the ruler with numbers that belong to different positions, which
   * would be a quietly wrong picture rather than an obviously missing one.
   */
  rulerColour() {
    return NEUTRAL;
  }

  tooltip(residue) {
    return [
      { key: residue.aa, value: `${residue.resnum}` },
      { key: 'from', value: 'the structure, not the alignment' },
    ];
  }
}
