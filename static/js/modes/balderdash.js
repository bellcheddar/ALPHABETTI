/**
 * BALDERDASH: variant effects and the residues the model would have preferred.
 *
 * Bayesian Amino-acid Letter Display of Estimated Residue Deviations And
 * Substitution Hotspots.
 *
 * The wild-type letter at full opacity, with ghost letters beneath it showing
 * what the model expected instead. Positions where the wild type sits low in
 * the model's distribution glow, driven by -log p(wt), so hotspots genuinely
 * light up rather than merely changing hue.
 *
 * These are language model scores. They are not clinical predictions, and the
 * panel says so in one line rather than a wall of disclaimer.
 */

import * as THREE from 'three';
import { SCHEMES, substitutionColour } from '../colours.js';

export const NAME = 'BALDERDASH';
export const SUBTITLE = 'variant hotspots';

export const DEFAULTS = {
  ghostDepth: 2,          // how many preferred residues to show beneath
  ghostOpacity: 0.35,
  heightScale: 2.6,       // Angstroms for the wild-type letter
  glowThreshold: 2.0,     // -log p(wt) above which a position starts to glow
  showClinical: true,
  scheme: 'chemistry',
  globalScale: 1.0,
  letterWidth: 1.15,
};

// Clinical significance colours. Deliberately not the accent palette: these
// mean something fixed and must not be confused with the app's own hues.
export const CLINICAL = {
  pathogenic: new THREE.Color(0xff2d2d),
  benign: new THREE.Color(0x3ddc97),
  uncertain: new THREE.Color(0x9aa5b1),
};

export class Balderdash {
  constructor(context) {
    this.context = context;
    this.options = { ...DEFAULTS };
    this.variants = null;              // filled in by main.js if an accession exists
  }

  get legend() {
    return {
      kind: 'variant',
      text: 'glow = -log p(wild type)',
      detail: `ghosts show the ${this.options.ghostDepth} residues the model ` +
              'preferred; scores are log-ratios, negative means deleterious',
    };
  }

  build() {
    const { field, data } = this.context;
    const { ghostDepth, ghostOpacity, heightScale, glowThreshold,
            scheme, globalScale, showClinical, letterWidth } = this.options;
    const colourOf = SCHEMES[scheme].colour;

    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const up = new THREE.Vector3();
    const right = new THREE.Vector3();
    const colour = new THREE.Color();

    field.begin();
    let hotspots = 0;
    let clinicalDrawn = 0;

    for (const residue of data.residues) {
      quaternion.fromArray(residue.q);
      up.set(0, 1, 0).applyQuaternion(quaternion);
      right.set(1, 0, 0).applyQuaternion(quaternion);

      const surprise = residue.surprise ?? 0;
      const hot = surprise > glowThreshold;
      if (hot) hotspots++;

      // The wild type, at full size, sitting on the backbone.
      const height = heightScale * globalScale;
      position.fromArray(residue.ca);
      colour.copy(colourOf(residue));
      if (hot) {
        // Push the colour toward magenta in proportion to the surprise. The
        // bloom pass then does the rest: a brighter, more saturated instance
        // blooms harder, so hotspots light up without needing a second
        // material or a per-letter emissive.
        const t = Math.min(1, (surprise - glowThreshold) / 3);
        colour.lerp(new THREE.Color(0xff2d9b), 0.55 * t)
              .multiplyScalar(1 + 1.4 * t);
      }
      field.add(residue.aa, position, quaternion,
        new THREE.Vector3(letterWidth, height, letterWidth * 0.85), colour, residue.i);

      // Ghosts: what the model would have preferred, stacked BENEATH the
      // backbone so they read as an alternative rather than as part of the
      // same stack GIBBERISH builds above it.
      let offset = 0;
      let shown = 0;
      for (let k = 0; k < residue.top.length && shown < ghostDepth; k++) {
        const aa = residue.top[k];
        if (aa === residue.aa) continue;          // not a ghost of itself
        const ghostHeight = height * 0.62 * Math.max(0.35, residue.p[k]);
        offset += ghostHeight + 0.12;
        position.copy(new THREE.Vector3().fromArray(residue.ca))
          .addScaledVector(up, -offset);
        colour.copy(colourOf({ ...residue, aa })).multiplyScalar(ghostOpacity);
        field.add(aa, position, quaternion,
          new THREE.Vector3(letterWidth * 0.7, ghostHeight, letterWidth * 0.6), colour, residue.i);
        shown++;
      }

      // A second rank, offset sideways, for reported clinical variants.
      const reported = showClinical && this.variants?.positions?.[String(residue.resnum)];
      if (reported?.length) {
        for (let k = 0; k < Math.min(2, reported.length); k++) {
          const record = reported[k];
          position.fromArray(residue.ca);
          position.addScaledVector(right, 1.5 + k * 1.1)
                  .addScaledVector(up, height * 0.25);
          field.add(record.mut, position, quaternion,
            new THREE.Vector3(letterWidth * 0.6, height * 0.6, letterWidth * 0.5),
            CLINICAL[record.significance] ?? CLINICAL.uncertain, residue.i);
          clinicalDrawn++;
        }
      }
    }

    field.commit();
    return { hotspots, clinicalDrawn, positions: data.residues.length };
  }

  rulerColour(residue) {
    const surprise = residue.surprise ?? 0;
    if (surprise > this.options.glowThreshold) return new THREE.Color(0xff2d9b);
    return SCHEMES[this.options.scheme].colour(residue);
  }

  tooltip(residue) {
    const worst = residue.sub
      ? residue.sub.map((v, k) => [v, k]).sort((a, b) => a[0] - b[0])[0] : null;
    const rows = [
      { key: residue.aa, value: `${residue.resnum}` },
      { key: 'model surprise', value: `${(residue.surprise ?? 0).toFixed(2)} (-log p)` },
      { key: 'model prefers', value: residue.top.slice(0, 3).join(' ') },
    ];
    const reported = this.variants?.positions?.[String(residue.resnum)];
    if (reported?.length) {
      rows.push({ key: 'reported', value: reported.slice(0, 2)
        .map((r) => `${residue.aa}${residue.resnum}${r.mut} ${r.significance}` +
                    (r.rsid ? ` (${r.rsid})` : '')).join('; ') });
    }
    return rows;
  }

  /** Data for the 20 x L substitution heatmap panel. */
  heatmap() {
    const { data } = this.context;
    return {
      alphabet: data.alphabet,
      rows: data.residues.map((r) => ({
        resnum: r.resnum, aa: r.aa, scores: r.sub,
      })),
      colourFor: substitutionColour,
    };
  }
}
