/**
 * Bootstrap, tab routing and application state.
 *
 * One payload serves all four tabs. Switching tab rebuilds the glyph field from
 * data already in memory and never touches the network, which is what makes the
 * tabs feel instant and is the reason the backend computes everything at once.
 */

import * as THREE from 'three';
import { GlyphSet, GlyphField } from './glyphs.js';
import { Renderer } from './renderer.js';
import { SCHEMES } from './colours.js';
import { Loading, Tooltip, Ruler, message, clearMessages,
         slider, toggle, select, stats } from './ui.js';
import { Gibberish } from './modes/gibberish.js';
import { Bumfluff } from './modes/bumfluff.js';
import { Balderdash } from './modes/balderdash.js';
import { Folderol } from './modes/folderol.js';
import { Hogwash, LAYOUTS } from './modes/hogwash.js';
import { exportGLB, exportSTL, exportPNG, exportGIF } from './exporters.js';

const state = {
  data: null,
  mode: null,
  modeKey: 'gibberish',
  renderer: null,
  field: null,
  glyphSet: null,
  shared: { backboneOpacity: 0.15, autoRotate: true, rotateSpeed: 0.85,
            globalScale: 1.0, scheme: null },
};

const dom = {};

/* ---------------------------------------------------------------- start-up */

async function boot() {
  cache(dom);
  dom.loading = new Loading(document.querySelector('[data-loading]'));
  dom.tooltip = new Tooltip(document.querySelector('[data-tooltip]'),
                            document.querySelector('[data-viewport]'));

  dom.loading.show('loading the typeface');
  try {
    state.glyphSet = await GlyphSet.load(
      document.body.dataset.fontUrl || '/static/fonts/baloo2-bold.typeface.json');
  } catch (error) {
    dom.loading.hide();
    message(dom.messages,
      '<b>The 3D typeface did not load.</b> Without it there are no letters to '
      + 'draw. Reloading usually fixes it; if it does not, the font file may be '
      + 'missing from this deployment.', 'error');
    return;
  }

  state.renderer = new Renderer(document.querySelector('[data-viewport]'));
  const material = state.renderer.makeMaterial();
  // Capacity covers the worst case the app allows: 400 residues at six letters
  // a position, plus BALDERDASH's ghosts and clinical ranks.
  state.field = new GlyphField(state.glyphSet, material, 4200);
  state.renderer.add(state.field.group);
  state.renderer.setPickTargets(state.field.objects, state.field);

  state.ruler = new Ruler(document.querySelector('[data-ruler]'), {
    onHover: (index) => hoverResidue(index, false),
    onPick: (index) => flyToResidue(index),
  });

  state.renderer.onHover = (index, at) => {
    state.ruler.highlight(index);
    if (index === null) return dom.tooltip.hide();
    dom.tooltip.show(state.mode.tooltip(state.data.residues[index]), at);
  };
  state.renderer.onPick = (index) => flyToResidue(index);
  state.renderer.onAutoRotateChange((on) => {
    state.shared.autoRotate = on;
    dom.autoRotateToggle?.set(on);
    // The on-canvas button reflects live state, including the transient pause
    // while someone is dragging, so it always says what is actually happening.
    if (dom.rotateButton) {
      dom.rotateButton.setAttribute('aria-pressed', String(on));
      dom.rotateButton.classList.toggle('off', !on);
      dom.rotateButton.querySelector('span').textContent =
        state.renderer._rotateOff ? 'paused' : (on ? 'rotating' : 'holding');
    }
  });

  state.modeKey = initialMode();
  // Test hook: lets an automated drag check whether the camera actually moved.
  window.__ALPHA_CAMPOS = () => state.renderer.camera.position.toArray().map(v => +v.toFixed(3));
  window.__ALPHA_CTRL = () => state.renderer.control;
  window.__ALPHA_R = () => state.renderer;
  // What this deployment can actually do with WebLogo, asked once.
  try {
    const capabilities = await (await fetch('/api/logo/capabilities')).json();
    state.logoFormats = capabilities.formats;
    state.logoUnits = capabilities.units;
    state.logoSchemes = capabilities.color_schemes;
    state.logoAlphabets = capabilities.alphabets;
    state.logoExamples = capabilities.examples;
    const stamp = document.querySelector('[data-weblogo-version]');
    if (stamp) stamp.textContent = capabilities.weblogo.version;
  } catch { /* HOGWASH degrades to a message; the other tabs are unaffected */ }

  wireInputs();
  wireTabs();

  // The landing state is never empty: the first example loads from the
  // pre-warmed cache before the user does anything at all.
  await loadExample(document.body.dataset.firstExample || 'ubiquitin');
}

function cache(target) {
  target.messages = document.querySelector('[data-messages]');
  target.controls = document.querySelector('[data-controls]');
  target.shared = document.querySelector('[data-shared-controls]');
  target.stats = document.querySelector('[data-stats]');
  target.legend = document.querySelector('[data-legend]');
  target.input = document.querySelector('[data-input]');
  target.submit = document.querySelector('[data-submit]');
  target.tabs = [...document.querySelectorAll('[data-tab]')];
  target.downloads = document.querySelector('[data-downloads]');
  target.rotateButton = document.querySelector('[data-rotate-toggle]');
  target.cite = document.querySelector('[data-cite]');
  target.expand = document.querySelector('[data-expand]');
  target.dock = document.querySelector('[data-residue-dock]');
  target.dockBody = document.querySelector('[data-dock-body]');
  target.dockClose = document.querySelector('[data-dock-close]');
  target.resetButton = document.querySelector('[data-reset-view]');
}

/* ------------------------------------------------------------------- data */

async function loadExample(name) {
  dom.loading.show('loading example');
  clearMessages(dom.messages);
  try {
    const response = await fetch(`/api/example/${name}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'That example is unavailable.');
    adopt(payload);
  } catch (error) {
    message(dom.messages, `<b>Could not load the example.</b> ${error.message}`, 'error');
  } finally {
    dom.loading.hide();
  }
}

async function submit(text, { truncate = false } = {}) {
  clearMessages(dom.messages);
  dom.loading.show('checking the cache');
  dom.submit.disabled = true;

  try {
    const response = await fetch('/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: text, truncate }),
    });
    const payload = await response.json();

    if (response.status === 400 && payload.can_truncate) {
      dom.loading.hide();
      message(dom.messages, `<b>${payload.error}</b>`, 'warn', {
        label: `Fold the first ${payload.max_length}`,
        onClick: () => submit(text, { truncate: true }),
      });
      return;
    }
    if (!response.ok) throw new Error(payload.error || 'That did not work.');

    if (payload.residues) {              // a cache hit came straight back
      adopt(payload);
      message(dom.messages, 'Served from cache, so no folding was needed.');
      return;
    }
    await poll(payload.job_id, payload);
  } catch (error) {
    message(dom.messages, `<b>${error.message}</b>`, 'error');
  } finally {
    dom.loading.hide();
    dom.submit.disabled = false;
  }
}

async function poll(jobId, submitted) {
  dom.loading.setStage('queued');
  if (submitted?.length) dom.loading.setMeta(`${submitted.length} residues`);

  // A fixed interval either hammers the server on a fast fold or feels sluggish
  // on a slow one, so it backs off from half a second to three.
  let interval = 500;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, interval));
    interval = Math.min(3000, interval * 1.25);

    const response = await fetch(`/api/status/${jobId}`);
    const status = await response.json();
    if (!response.ok) throw new Error(status.error || 'The job vanished.');

    dom.loading.setStage(status.stage_label || status.stage);
    if (status.queue_position > 0) {
      dom.loading.setMeta(`${status.queue_position} ahead in the queue`);
    }

    if (status.state === 'failed') throw new Error(status.error || 'The fold failed.');
    if (status.state === 'done') {
      const result = await fetch(`/api/result/${status.result_id}`);
      const payload = await result.json();
      if (!result.ok) throw new Error(payload.error || 'The result went missing.');
      adopt(payload);
      return;
    }
  }
}

function adopt(payload) {
  state.data = payload;
  state.renderer.frameStructure(
    payload.residues.map((r) => new THREE.Vector3().fromArray(r.ca)));
  state.renderer.setBackbone(
    payload.residues.map((r) => new THREE.Vector3().fromArray(r.ca)),
    { opacity: state.shared.backboneOpacity });

  // Only things that happened to the user's own input get a warning box.
  // How the result was computed lives in the info panel.
  for (const note of payload.notes || []) message(dom.messages, note, 'warn');
  if (payload.stats?.mean_plddt < 60) {
    message(dom.messages,
      `<b>Low confidence fold.</b> Mean pLDDT is ${payload.stats.mean_plddt}, which `
      + 'usually means the sequence has few close relatives in the training data. '
      + 'The structure and the logo should both be treated with suspicion.', 'warn');
  }

  setMode(state.modeKey, { force: true });
  loadVariants(payload);
  renderDownloads(payload);
}

async function loadVariants(payload) {
  const accession = payload.source?.accession;
  if (!accession) return;
  try {
    const response = await fetch(`/api/variants/${accession}`);
    if (!response.ok) return;                 // optional; never blocks BALDERDASH
    const variants = await response.json();
    state.variants = variants;
    if (state.mode instanceof Balderdash) { state.mode.variants = variants; rebuild(); }
  } catch { /* an overlay that fails to load is not an error worth showing */ }
}

/* ------------------------------------------------------------------ modes */

// The backronym for each tab, with the letters that spell the acronym marked.
// Shown in the viewport's top-right corner for whichever tab is active, rather
// than under all four tabs at once, which made the strip 90 px tall.
const MODES = {
  gibberish: { Class: Gibberish, label: 'GIBBERISH',
    expand: '<b>G</b>lyph <b>I</b>nterface for <b>B</b>its, <b>E</b>ntropy and '
          + '<b>R</b>esidue <b>I</b>nformation in <b>S</b>tructural <b>H</b>omology' },
  bumfluff: { Class: Bumfluff, label: 'BUMFLUFF',
    expand: '<b>B</b>uried/<b>U</b>nburied <b>M</b>apping of <b>F</b>onts, '
          + '<b>L</b>etters, <b>U</b>ncovered <b>F</b>aces and <b>F</b>olds' },
  balderdash: { Class: Balderdash, label: 'BALDERDASH',
    expand: '<b>B</b>ayesian <b>A</b>mino-acid <b>L</b>etter <b>D</b>isplay of '
          + '<b>E</b>stimated <b>R</b>esidue <b>D</b>eviations <b>A</b>nd '
          + '<b>S</b>ubstitution <b>H</b>otspots' },
  folderol: { Class: Folderol, label: 'FOLDEROL',
    expand: '<b>F</b>olding <b>O</b>f <b>L</b>etters <b>D</b>isplayed '
          + '<b>E</b>n <b>R</b>oute, <b>O</b>rdered <b>L</b>inearly' },
  hogwash: { Class: Hogwash, label: 'HOGWASH',
    expand: '<b>H</b>eight-<b>O</b>rdered <b>G</b>lyphs <b>W</b>eighted '
          + '<b>A</b>cross <b>S</b>equence <b>H</b>omologues' },
};

function setMode(key, { force = false } = {}) {
  if (!force && key === state.modeKey && state.mode) return;
  state.modeKey = key;
  const context = { field: state.field, glyphSet: state.glyphSet,
                    renderer: state.renderer, data: state.data };
  state.mode = new MODES[key].Class(context);

  // Shared settings persist across a tab change: a user who set the scale
  // should not have it reset by looking at another tab.
  //
  // The colour scheme is deliberately different. Each mode has a default that
  // suits what it is showing -- BUMFLUFF colours by accessibility because that
  // is the quantity it draws -- so the shared value only wins once the user
  // has actually chosen one. Applying it unconditionally meant BUMFLUFF opened
  // in GIBBERISH's chemistry palette, showing the wrong variable entirely.
  state.mode.options.globalScale = state.shared.globalScale;
  if (state.shared.scheme && SCHEMES[state.shared.scheme]) {
    state.mode.options.scheme = state.shared.scheme;
  }
  if (state.mode instanceof Balderdash) state.mode.variants = state.variants;

  state.renderer.setUpdate(
    state.mode.update ? (delta) => { if (state.mode.update(delta)) state.mode.build(); }
                      : null);

  for (const tab of dom.tabs) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === key));
  }
  if (dom.expand) {
    dom.expand.innerHTML = MODES[key].expand;
    // Restart the ignition sweep so the new backronym lights up on arrival
    // rather than joining the previous one's cycle midway through.
    dom.expand.style.animation = 'none';
    void dom.expand.offsetWidth;
    dom.expand.style.animation = '';
  }
  // HOGWASH used to cover the canvas with a flat WebLogo PNG. It no longer
  // does: the logo is drawn in 3D like everything else, and the flat one is an
  // output format rather than the way you look at it. A 231-column alignment as
  // a static image is six stacked rows of two-millimetre letters.
  // The WebLogo credit shows on the tab that uses it and nowhere else.
  if (dom.cite) dom.cite.hidden = key !== 'hogwash';
  hideDock();

  buildModeControls();
  buildSharedControls();
  rebuild();
}

function rebuild() {
  if (!state.mode) return;

  // HOGWASH's coordinate system is the alignment's columns, not the structure's
  // residues, and the two are different things: an alignment has gaps and
  // usually covers only part of a chain. So it supplies its own ruler rows --
  // the consensus residue per column -- rather than colouring the structure's
  // sequence with numbers belonging to different positions.
  if (state.modeKey === 'hogwash') {
    if (!state.mode.data) { state.mode.build(); renderLegend(); return; }
    const columns = state.mode.data.columns;
    const rows = {
      length: columns.length,
      source: { accession: state.mode.source, name: 'alignment consensus' },
      residues: columns.map((c) => ({
        i: c.i, resnum: c.number, aa: c.stack[0]?.aa || '-',
      })),
    };
    const result = state.mode.build();
    state.ruler.render(rows, (r) => state.mode.rulerColour(r));
    renderStats(result);
    renderLegend();
    frameLogo();
    return;
  }

  if (!state.data) return;
  const result = state.mode.build();
  state.ruler.render(state.data, (r) => state.mode.rulerColour(r));
  renderStats(result);
  renderLegend();
}

/** Frame whatever HOGWASH just laid out, whichever shape it chose. */
function frameLogo() {
  const points = (state.mode.data?.columns || [])
    .map((c) => state.mode.positionOf(c.i)).filter(Boolean);
  if (!points.length) return;
  state.renderer.frameStructure(points);
  state.renderer.setBackbone(null);
  // The ring and the helix ask to be framed against their near wall rather
  // than in full; a strip or rows return null and keep the fitted distance.
  const close = state.mode.preferredDistance?.();
  if (close) state.renderer.setPose(null, close);
  // Let the wheel bring you right up to a single column, whatever the layout.
  state.renderer.setZoomFloor(6);
}

/* --------------------------------------------------------------- controls */

function wireTabs() {
  for (const tab of dom.tabs) {
    tab.addEventListener('click', () => {
      setMode(tab.dataset.tab);
      // Reflect the tab in the URL so a particular view can be linked to.
      // replaceState rather than a hash assignment: setting location.hash
      // pushes a history entry per tab click, which turns the back button into
      // a tour of everything the user just looked at.
      history.replaceState(null, '', `#${tab.dataset.tab}`);
    });
  }
  window.addEventListener('hashchange', () => {
    const key = location.hash.replace('#', '');
    if (MODES[key]) setMode(key);
  });
}

/** A tab named in the URL wins over the default, so links open where they point. */
function initialMode() {
  const key = location.hash.replace('#', '');
  return MODES[key] ? key : 'gibberish';
}

function wireInputs() {
  dom.submit.addEventListener('click', () => submit(dom.input.value));
  dom.input.addEventListener('keydown', (event) => {
    // Enter submits, shift-enter adds a newline: the box takes multi-line FASTA.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit(dom.input.value);
    }
  });
  for (const button of document.querySelectorAll('[data-example]')) {
    button.addEventListener('click', () => loadExample(button.dataset.example));
  }
  dom.rotateButton?.addEventListener('click', () => {
    const on = state.renderer._rotateOff;   // currently off -> turn it on
    state.renderer.setAutoRotate(on, state.shared.rotateSpeed);
    state.shared.autoRotate = on;
    dom.autoRotateToggle?.set(on);
    dom.rotateButton.classList.toggle('off', !on);
    dom.rotateButton.querySelector('span').textContent = on ? 'rotating' : 'paused';
    dom.rotateButton.setAttribute('aria-pressed', String(on));
  });
  dom.resetButton?.addEventListener('click', () => {
    state.renderer.resetCamera();
    hideDock();
  });
  dom.dockClose?.addEventListener('click', hideDock);

  // Keyboard, so the two things people reach for most are one key away.
  window.addEventListener('keydown', (event) => {
    if (event.target.matches('input, textarea, select')) return;
    if (event.key === 'r' || event.key === 'R') dom.rotateButton?.click();
    if (event.key === '0') { state.renderer.resetCamera(); hideDock(); }
    if (event.key === 'Escape') hideDock();
  });

  buildSharedControls();
}

function buildSharedControls() {
  const host = dom.shared;
  host.replaceChildren();

  select(host, {
    label: 'Colour by',
    options: Object.entries(SCHEMES).map(([k, v]) => [k, v.label]),
    value: state.shared.scheme ?? state.mode?.options?.scheme ?? 'chemistry',
    onChange: (value) => {
      state.shared.scheme = value;
      if (state.mode) state.mode.options.scheme = value;
      rebuild();
    },
  });

  slider(host, {
    label: 'Glyph scale', min: 0.3, max: 3, step: 0.05,
    value: state.shared.globalScale, format: (v) => `${v.toFixed(2)}x`,
    onInput: (value) => {
      state.shared.globalScale = value;
      if (state.mode) state.mode.options.globalScale = value;
      state.mode?.invalidate?.();
      rebuild();
    },
  });

  slider(host, {
    label: 'Ghost backbone', min: 0, max: 0.8, step: 0.01,
    value: state.shared.backboneOpacity, format: (v) => `${(v * 100).toFixed(0)}%`,
    onInput: (value) => {
      state.shared.backboneOpacity = value;
      state.renderer.setBackboneOpacity(value);
    },
  });

  dom.autoRotateToggle = toggle(host, {
    label: 'Auto-rotate', checked: state.shared.autoRotate,
    onChange: (on) => {
      state.shared.autoRotate = on;
      state.renderer.setAutoRotate(on, state.shared.rotateSpeed);
    },
  });

  slider(host, {
    label: 'Rotate speed', min: 0.1, max: 4, step: 0.05,
    value: state.shared.rotateSpeed, format: (v) => `${v.toFixed(2)}`,
    onInput: (value) => {
      state.shared.rotateSpeed = value;
      state.renderer.setAutoRotate(state.shared.autoRotate, value);
    },
  });

  const row = document.createElement('div');
  row.className = 'button-row';
  row.innerHTML = '<button class="button" data-reset>Reset view</button>';
  const png = document.createElement('button');
  png.className = 'button';
  png.textContent = 'PNG 2x';
  png.addEventListener('click', () => exportPNG(state.renderer,
    { scale: 2, name: exportName() }));
  row.appendChild(png);
  row.querySelector('[data-reset]').addEventListener('click',
    () => state.renderer.resetCamera());
  host.appendChild(row);
}

function buildModeControls() {
  const host = dom.controls;
  host.replaceChildren();
  const options = state.mode.options;
  const refresh = () => { state.mode.invalidate?.(); rebuild(); };

  if (state.modeKey === 'gibberish') {
    slider(host, { label: 'Stack depth', min: 1, max: 6, step: 1,
      value: options.stackDepth, format: (v) => `${v} letters`,
      onInput: (v) => { options.stackDepth = v; refresh(); } });
    slider(host, { label: 'Bits per Angstrom', min: 0.2, max: 3, step: 0.05,
      value: options.bitsPerAngstrom, format: (v) => `1 bit = ${v.toFixed(2)} A`,
      onInput: (v) => { options.bitsPerAngstrom = v; refresh(); } });
    slider(host, { label: 'Minimum bits', min: 0, max: 4.32, step: 0.05,
      value: options.minBits, format: (v) => `${v.toFixed(2)} bits`,
      onInput: (v) => { options.minBits = v; refresh(); } });
    slider(host, { label: 'Letter width', min: 0.4, max: 2.5, step: 0.05,
      value: options.letterWidth, format: (v) => `${v.toFixed(2)}x`,
      onInput: (v) => { options.letterWidth = v; refresh(); } });
    toggle(host, { label: 'Height by probability, not bits',
      checked: options.useProbability,
      onChange: (v) => { options.useProbability = v; refresh(); } });
  }

  if (state.modeKey === 'bumfluff') {
    slider(host, { label: 'Height scale', min: 1, max: 12, step: 0.1,
      value: options.heightScale, format: (v) => `${v.toFixed(1)} A at RSA 1.0`,
      onInput: (v) => { options.heightScale = v; refresh(); } });
    toggle(host, { label: 'Highlight exposed hydrophobics',
      checked: options.highlightPatches,
      onChange: (v) => { options.highlightPatches = v; refresh(); } });
  }

  if (state.modeKey === 'balderdash') {
    slider(host, { label: 'Ghost letters', min: 0, max: 4, step: 1,
      value: options.ghostDepth, format: (v) => `${v}`,
      onInput: (v) => { options.ghostDepth = v; refresh(); } });
    slider(host, { label: 'Glow threshold', min: 0.5, max: 6, step: 0.1,
      value: options.glowThreshold, format: (v) => `${v.toFixed(1)} -log p`,
      onInput: (v) => { options.glowThreshold = v; refresh(); } });
    toggle(host, { label: 'Reported clinical variants',
      checked: options.showClinical,
      onChange: (v) => { options.showClinical = v; refresh(); } });
    const note = document.createElement('p');
    note.className = 'message';
    note.innerHTML = 'These are language model scores, not clinical predictions.';
    host.appendChild(note);
  }

  if (state.modeKey === 'hogwash') {
    buildHogwashControls(host, options, refresh);
    return;
  }

  if (state.modeKey === 'folderol') {
    const timeline = slider(host, { label: 'Timeline', min: 0, max: 1, step: 0.005,
      value: state.mode.progress, format: (v) => `${(v * 100).toFixed(0)}%`,
      onInput: (v) => { state.mode.playing = false; state.mode.progress = v;
                        state.mode.build(); } });
    slider(host, { label: 'Duration', min: 1, max: 12, step: 0.1,
      value: options.duration, format: (v) => `${v.toFixed(1)} s`,
      onInput: (v) => { options.duration = v; } });
    slider(host, { label: 'Stagger', min: 0, max: 0.02, step: 0.0005,
      value: options.staggerPerResidue,
      format: (v) => (v === 0 ? 'none' : `${(v * 1000).toFixed(1)} ms/residue`),
      onInput: (v) => { options.staggerPerResidue = v; refresh(); } });
    toggle(host, { label: 'Loop', checked: options.loop,
      onChange: (v) => { options.loop = v; } });

    const row = document.createElement('div');
    row.className = 'button-row';
    const play = document.createElement('button');
    play.className = 'button primary';
    play.textContent = state.mode.playing ? 'Pause' : 'Play';
    play.addEventListener('click', () => {
      state.mode.playing = !state.mode.playing;
      play.textContent = state.mode.playing ? 'Pause' : 'Play';
    });
    row.appendChild(play);
    host.appendChild(row);

    // Keep the timeline slider in step with playback rather than letting it sit
    // at whatever value it was last dragged to.
    setInterval(() => {
      if (state.modeKey === 'folderol' && state.mode.playing) {
        timeline.set(state.mode.progress.toFixed(3));
      }
    }, 120);

    buildExportRow(host);
  }
}

/**
 * HOGWASH's controls: WebLogo's own options, grouped by what they affect.
 *
 * The split matters. The first group changes the NUMBERS -- the composition the
 * information is measured against, the small-sample correction, the units --
 * and getting one of those wrong gives a different and wrong answer that looks
 * exactly as convincing. The second only changes the drawing.
 */
function buildHogwashControls(host, options, refresh) {
  const reload = () => loadAlignment(
    { alignment: state.mode.alignment, example: state.mode.example },
    state.mode.source);

  // ---- where the alignment comes from
  const lab = document.createElement('div');
  lab.className = 'label';
  lab.innerHTML = '<span>Alignment</span>';
  host.appendChild(lab);

  const box = document.createElement('textarea');
  box.className = 'align-input';
  box.rows = 4;
  box.placeholder = 'Paste an alignment: FASTA, CLUSTAL, Stockholm, PHYLIP, MSF ...';
  host.appendChild(box);

  const row = document.createElement('div');
  row.className = 'button-row';
  const use = document.createElement('button');
  use.className = 'button primary';
  use.textContent = 'Make the logo';
  use.addEventListener('click', () =>
    loadAlignment({ alignment: box.value }, 'pasted alignment'));
  row.appendChild(use);

  const upload = document.createElement('button');
  upload.className = 'button';
  upload.textContent = 'Upload';
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = '.fa,.fasta,.aln,.sto,.stk,.msf,.phy,.txt,.seq';
  picker.addEventListener('change', async () => {
    const file = picker.files?.[0];
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) {
      message(dom.messages, '<b>That file is over 8 MB.</b> A logo over that '
        + 'many sequences is dominated by whichever clade was sequenced most; '
        + 'a curated seed alignment gives a better picture.', 'warn');
      return;
    }
    const text = await file.text();
    box.value = text.slice(0, 200000);
    loadAlignment({ alignment: text }, file.name);
  });
  upload.addEventListener('click', () => picker.click());
  row.appendChild(upload);
  row.appendChild(picker);

  const family = document.createElement('button');
  family.className = 'button';
  family.textContent = 'Fetch family';
  family.title = 'Fetch this protein\u2019s Pfam seed alignment from InterPro';
  family.addEventListener('click', () => fetchFamilyAlignment(box));
  row.appendChild(family);
  host.appendChild(row);

  const examples = document.createElement('div');
  examples.className = 'button-row';
  for (const [key, meta] of Object.entries(state.logoExamples || {})) {
    const button = document.createElement('button');
    button.className = 'button';
    button.textContent = meta.label;
    button.title = meta.note;
    button.addEventListener('click', () => {
      box.value = '';
      loadAlignment({ example: key }, meta.label);
    });
    examples.appendChild(button);
  }
  host.appendChild(examples);

  // ---- the 3D arrangement
  const shape = document.createElement('div');
  shape.className = 'label';
  shape.style.marginTop = '14px';
  shape.innerHTML = '<span>Arrangement</span>';
  host.appendChild(shape);

  select(host, {
    label: 'Layout', value: options.layout,
    options: Object.entries(LAYOUTS),
    onChange: (v) => { options.layout = v; refresh(); },
  });
  slider(host, {
    label: 'Stack depth', min: 1, max: 8, step: 1,
    value: options.stackDepth, format: (v) => `${v} letters`,
    onInput: (v) => { options.stackDepth = v; refresh(); },
  });
  slider(host, {
    label: 'Height scale', min: 0.4, max: 6, step: 0.1,
    value: options.bitsPerAngstrom,
    format: (v) => `1 ${options.unit_name} = ${v.toFixed(1)} A`,
    onInput: (v) => { options.bitsPerAngstrom = v; refresh(); },
  });
  slider(host, {
    label: 'Column spacing', min: 1.2, max: 6, step: 0.1,
    value: options.columnSpacing, format: (v) => `${v.toFixed(1)} A`,
    onInput: (v) => { options.columnSpacing = v; refresh(); },
  });
  slider(host, {
    label: `Minimum ${options.unit_name}`, min: 0, max: 4.32, step: 0.05,
    value: options.minBits, format: (v) => v.toFixed(2),
    onInput: (v) => { options.minBits = v; refresh(); },
  });
  select(host, {
    label: 'Colour by', value: options.scheme,
    options: [
      ['weblogo', "WebLogo's own scheme"],
      ['conservation', 'Conservation'],
      ['charge', 'Charge'],
      ['chemistry', 'Chemistry (Taylor)'],
      ['hydrophobicity', 'Hydrophobicity'],
    ],
    onChange: (v) => { options.scheme = v; refresh(); },
  });

  // ---- options that change the NUMBERS
  const science = document.createElement('div');
  science.className = 'label';
  science.style.marginTop = '14px';
  science.innerHTML = '<span>Measurement</span>';
  host.appendChild(science);

  select(host, {
    label: 'Units', value: options.unit_name,
    options: (state.logoUnits || ['bits']).map((u) => [u, u]),
    onChange: (v) => { options.unit_name = v; reload(); },
  });
  select(host, {
    label: 'Background composition', value: options.composition,
    options: [
      ['auto', 'Equiprobable (auto)'],
      ['H. sapiens', 'H. sapiens'], ['E. coli', 'E. coli'],
      ['S. cerevisiae', 'S. cerevisiae'], ['D. melanogaster', 'D. melanogaster'],
      ['M. musculus', 'M. musculus'], ['C. elegans', 'C. elegans'],
      ['none', 'None (no prior)'],
    ],
    onChange: (v) => { options.composition = v; reload(); },
  });
  toggle(host, {
    label: 'Small-sample correction', checked: options.small_sample_correction,
    onChange: (v) => { options.small_sample_correction = v; reload(); },
  });
  select(host, {
    label: 'Alphabet', value: options.alphabet,
    options: (state.logoAlphabets || ['auto']).map((a) => [a, a]),
    onChange: (v) => { options.alphabet = v; reload(); },
  });

  // ---- the flat WebLogo, which is now an OUTPUT rather than the view
  const dl = document.createElement('div');
  dl.className = 'label';
  dl.style.marginTop = '14px';
  dl.innerHTML = '<span>Export a real WebLogo</span>';
  host.appendChild(dl);

  const blurb = document.createElement('p');
  blurb.className = 'message';
  blurb.innerHTML = 'These are generated by WebLogo itself and are the '
    + 'publication figure. EPS and PDF are vector.';
  host.appendChild(blurb);

  select(host, {
    label: 'Flat logo colours', value: options.color_scheme,
    options: (state.logoSchemes || ['auto']).map((c) => [c, c]),
    onChange: (v) => { options.color_scheme = v; },
  });
  slider(host, {
    label: 'Stacks per line', min: 10, max: 120, step: 1,
    value: options.stacks_per_line, format: (v) => `${v}`,
    onInput: (v) => { options.stacks_per_line = v; },
  });
  toggle(host, {
    label: 'Error bars', checked: options.show_errorbars,
    onChange: (v) => { options.show_errorbars = v; },
  });
  toggle(host, {
    label: 'Boxes around letters', checked: options.show_boxes,
    onChange: (v) => { options.show_boxes = v; },
  });
  const dlRow = document.createElement('div');
  dlRow.className = 'button-row';
  for (const [fmt, ok] of Object.entries(state.logoFormats || {})) {
    if (!ok) continue;
    const button = document.createElement('button');
    button.className = 'button';
    button.textContent = fmt.toUpperCase();
    button.addEventListener('click', () => downloadLogo(fmt));
    dlRow.appendChild(button);
  }
  host.appendChild(dlRow);
}

/** Fetch the current protein's Pfam seed alignment. */
async function fetchFamilyAlignment(box) {
  const accession = state.data?.source?.accession;
  if (!accession) {
    message(dom.messages, '<b>No accession for the current protein.</b> The '
      + 'family fetch needs one, so load an example or submit a UniProt '
      + 'accession first.', 'warn');
    return;
  }
  dom.loading.show('asking InterPro for a family');
  try {
    const list = await fetch(`/api/families/${accession}`);
    const found = await list.json();
    if (!list.ok) throw new Error(found.error);
    if (!found.families?.length) {
      throw new Error(`InterPro lists no Pfam family for ${accession}.`);
    }
    const first = found.families[0];
    dom.loading.setStage(`downloading the ${first.id} seed alignment`);
    const response = await fetch(`/api/families/${accession}/${first.id}/alignment`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error);
    box.value = payload.alignment.slice(0, 200000);
    await loadAlignment({ alignment: payload.alignment },
      `${first.id} ${first.name}`);
  } catch (error) {
    message(dom.messages, `<b>${error.message}</b>`, 'warn');
  } finally {
    dom.loading.hide();
  }
}

function buildExportRow(host) {
  const row = document.createElement('div');
  row.className = 'button-row';
  const add = (label, handler) => {
    const button = document.createElement('button');
    button.className = 'button';
    button.textContent = label;
    button.addEventListener('click', async () => {
      button.disabled = true;
      const original = button.textContent;
      try {
        const result = await handler(button);
        if (result?.warning) message(dom.messages, result.warning, 'warn');
      } catch (error) {
        message(dom.messages, `<b>Export failed.</b> ${error.message}`, 'error');
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
    });
    row.appendChild(button);
  };

  add('GLB', () => exportGLB(state.field, exportName()));
  add('STL', () => exportSTL(state.field, exportName()));
  add('PNG 4x', () => exportPNG(state.renderer, { scale: 4, name: exportName() }));
  add('GIF', (button) => exportGIF(state.renderer, state.mode, {
    name: exportName(),
    onProgress: (fraction) => { button.textContent = `GIF ${(fraction * 100) | 0}%`; },
  }));
  host.appendChild(row);
}

/* ------------------------------------------------------------- read-outs */

function renderStats(result) {
  if (state.modeKey === 'hogwash') {
    const d = state.mode.data;
    if (!d) { stats(dom.stats, []); return; }
    const bits = d.columns.map((c) => c.bits);
    const mean = bits.reduce((a, b) => a + b, 0) / (bits.length || 1);
    stats(dom.stats, [
      { label: 'sequences', value: d.alignment.sequences },
      { label: 'columns', value: d.alignment.columns },
      { label: `mean ${d.unit}`, value: mean.toFixed(2) },
      { label: `most conserved`, value: Math.max(...bits).toFixed(2) },
      { label: 'letters drawn', value: state.field.total(), wide: true },
      { label: 'alignment', value: state.mode.source || 'pasted', wide: true },
    ]);
    return;
  }
  const s = state.data.stats;
  stats(dom.stats, [
    { label: 'mean pLDDT', value: s.mean_plddt },
    { label: 'residues', value: state.data.length },
    { label: 'mean bits', value: s.mean_bits.toFixed(2) },
    { label: 'letters drawn', value: state.field.total() },
    { label: 'source', value: state.data.source?.accession || state.data.source?.type,
      wide: true },
    { label: s.cached ? 'from cache' : `folded on ${s.device}`,
      value: s.cached ? 'instant' : `${(s.fold_seconds + s.lm_seconds).toFixed(1)} s`,
      wide: true },
  ]);
}

function renderLegend() {
  const legend = state.mode.legend;
  const s = state.data.stats;
  // Provenance ("masked marginals ...", "folded on ...") deliberately not here.
  // It is a fact about how the numbers were made, not about what is on screen,
  // and it was three lines of small type competing with the legend it sat under.
  // The About page carries it in full.
  dom.legend.innerHTML =
    `<b>${legend.text}</b><br>${legend.detail}`
    + `<br>secondary structure via ${s.ss_method}`;
}

function renderDownloads(payload) {
  if (!dom.downloads) return;
  const id = payload.id;
  dom.downloads.innerHTML = [
    ['pdb', 'PDB'], ['json', 'JSON'], ['csv', 'CSV'],
  ].map(([kind, label]) =>
    `<a class="button" href="/api/download/${id}.${kind}">${label}</a>`).join('');
}

function exportName() {
  const source = state.data?.source;
  const base = (source?.accession || source?.name || 'alphabetti')
    .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  return `alphabetti_${base}_${state.modeKey}`;
}

/* --------------------------------------------------------------- HOGWASH */

/**
 * The WebLogo half.
 *
 * Two round trips on purpose. /api/logo returns WebLogo's per-column numbers,
 * and /api/logo/render.<fmt> returns WebLogo's own drawing. Splitting them
 * means a knob that only changes the picture does not re-run the maths, and a
 * knob that changes the maths does not wait on ghostscript.
 */
async function loadAlignment(body, sourceLabel) {
  const mode = state.mode;
  if (!(mode instanceof Hogwash)) return;
  clearMessages(dom.messages);
  dom.loading.show('counting the alignment');
  try {
    const response = await fetch('/api/logo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, ...logoRequest() }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'That did not work.');

    mode.data = payload;
    mode.alignment = body.alignment ?? null;
    mode.example = body.example ?? null;
    mode.source = sourceLabel;
    for (const note of payload.alignment?.notes || []) {
      message(dom.messages, note, 'warn');
    }
    rebuild();
  } catch (error) {
    message(dom.messages, `<b>${error.message}</b>`, 'error');
  } finally {
    dom.loading.hide();
  }
}

/** Everything the server needs to reproduce this logo. */
function logoRequest() {
  const o = state.mode.options;
  return {
    alphabet: o.alphabet,
    input_format: o.input_format,
    ignore_lower_case: o.ignore_lower_case,
    composition: o.composition,
    small_sample_correction: o.small_sample_correction,
    options: {
      unit_name: o.unit_name,
      color_scheme: o.color_scheme,
      show_errorbars: o.show_errorbars,
      show_boxes: o.show_boxes,
      stacks_per_line: o.stacks_per_line,
      logo_title: o.logo_title,
      first_index: o.first_index,
      ...(o.yaxis_scale ? { yaxis_scale: o.yaxis_scale } : {}),
    },
  };
}

async function downloadLogo(format) {
  const mode = state.mode;
  const body = JSON.stringify({
    alignment: mode.alignment, example: mode.example, ...logoRequest(),
  });
  const response = await fetch(`/api/logo/render.${format}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    message(dom.messages, `<b>${error.error || 'That format failed.'}</b>`, 'error');
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `hogwash_${(mode.source || 'logo').replace(/\W+/g, '_')}.${format}`;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* -------------------------------------------------------------- residues */

function hoverResidue(index, fromCanvas) {
  state.ruler.highlight(index);
  if (index === null) dom.tooltip.hide();
}

/**
 * Clicking a residue docks its card and zooms to it.
 *
 * The hover tooltip still follows the pointer and still disappears; this is the
 * other half of the gesture. A tooltip you have to keep the mouse still to read
 * is no use once you want to compare it with something, so a click pins it in
 * the corner and takes the camera there.
 */
function flyToResidue(index) {
  if (state.modeKey === 'hogwash') {
    const at = state.mode.positionOf(index);
    const column = state.mode.data?.columns?.[index];
    if (!at || !column) return;
    state.renderer.focusResidue(at);
    state.renderer.setFocusMarker(at);
    state.ruler.highlight(index);
    showDock({
      i: index, resnum: column.number, aa: column.stack[0]?.aa || '-',
      plddt: 0, rsa: 0, ss: 'C',
    });
    return;
  }
  const residue = state.data?.residues[index];
  if (!residue) return;
  const at = new THREE.Vector3().fromArray(residue.ca);
  state.renderer.focusResidue(at);
  state.renderer.setFocusMarker(at);
  state.ruler.highlight(index);
  showDock(residue);
}

function showDock(residue) {
  if (!dom.dock) return;
  state.dockedResidue = residue.i;
  const [first, ...rest] = state.mode.tooltip(residue);

  // The docked card shows the active tab's rows plus the handful that are worth
  // seeing whatever tab you are on. Deduplicated by label, because each mode's
  // tooltip already carries some of them -- GIBBERISH's includes pLDDT, and the
  // card listed it twice.
  const rows = [...rest];
  const seen = new Set(rows.map((r) => r.key));
  // An alignment column has no pLDDT, no accessibility and no secondary
  // structure: those belong to a predicted structure, which is a different
  // object. Showing them as zeros would be worse than showing nothing.
  const always = state.modeKey === 'hogwash' ? [] : [
    { key: 'pLDDT', value: residue.plddt.toFixed(0) },
    { key: 'relative SASA', value: residue.rsa.toFixed(3) },
    { key: 'structure', value: { H: 'helix', E: 'strand', C: 'coil' }[residue.ss] || residue.ss },
  ];
  for (const row of always) if (!seen.has(row.key)) rows.push(row);

  dom.dockBody.innerHTML =
    `<div class="dock-head"><span class="dock-aa">${first.key}</span>`
    + `<span class="dock-num">${first.value}</span></div>`
    + rows.map((r) => `<div class="dock-row"><span>${r.key}</span><b>${r.value}</b></div>`).join('');
  dom.dock.hidden = false;
}

function hideDock() {
  if (!dom.dock) return;
  dom.dock.hidden = true;
  state.dockedResidue = null;
  state.renderer.setFocusMarker(null);
  state.ruler.highlight(null);
}

/* -------------------------------------------------------------------------
   Nothing should ever render a blank canvas with no explanation. An unhandled
   rejection anywhere in start-up used to leave the loading overlay showing
   "loading the typeface" forever, which is indistinguishable from a slow
   network and tells the user nothing.
   ------------------------------------------------------------------------- */

function fatal(error) {
  console.error('[ALPHABETTI]', error);
  document.querySelector('[data-loading]')?.setAttribute('hidden', '');
  const host = document.querySelector('[data-messages]');
  if (!host) return;
  message(host,
    `<b>Something broke while starting up.</b> ${error?.message || error}. `
    + 'This is a bug rather than anything you did. The details are in the '
    + 'browser console.', 'error');
}

window.addEventListener('unhandledrejection', (event) => fatal(event.reason));
window.addEventListener('error', (event) => fatal(event.error || event.message));

document.addEventListener('DOMContentLoaded', () => { boot().catch(fatal); });
