/**
 * DOM: controls, tooltip, sequence ruler, loading states and messages.
 *
 * Kept apart from the renderer so that the 3D code never touches the document
 * and the DOM code never touches three.js. The one thing they share is a
 * residue index, passed both ways.
 */

import { cssColour } from './colours.js';

/* -------------------------------------------------------------------------
   Loading. ESMFold takes real time and a bare spinner would be a lie about how
   long. These lines are honest about which part is slow, and rotate so that a
   long wait does not look like a frozen one.
   ------------------------------------------------------------------------- */

const QUIPS = [
  'folding, this is the slow bit',
  'asking the language model what it would have preferred',
  'masking one residue at a time, which is the honest way to ask',
  'measuring what the solvent can reach',
  'working out which way is up for every residue',
  'building a virtual beta carbon for every glycine',
  'arranging the letters',
  'no alignment was harmed in the making of this logo',
];

export class Loading {
  constructor(root) {
    this.root = root;
    this.stageEl = root.querySelector('[data-loading-stage]');
    this.metaEl = root.querySelector('[data-loading-meta]');
    this._quip = 0;
    this._timer = null;
    this._started = 0;
  }

  show(stage = 'starting') {
    this.root.hidden = false;
    this._started = performance.now();
    this.setStage(stage);
    clearInterval(this._timer);
    this._timer = setInterval(() => this._tick(), 1000);
    this._tick();
  }

  setStage(text) {
    if (this.stageEl) this.stageEl.textContent = text;
    this._stage = text;
  }

  setMeta(text) { this._meta = text; }

  _tick() {
    const seconds = (performance.now() - this._started) / 1000;
    // After ten seconds the stage label alone stops being reassuring, so the
    // quips start rotating underneath it. Before that they would just be noise.
    let line = this._meta ?? '';
    if (seconds > 10 && Math.floor(seconds / 6) !== this._quipAt) {
      this._quipAt = Math.floor(seconds / 6);
      this._quip = (this._quip + 1) % QUIPS.length;
    }
    const quip = seconds > 10 ? ` · ${QUIPS[this._quip]}` : '';
    if (this.metaEl) {
      this.metaEl.textContent = `${seconds.toFixed(0)} s${line ? ` · ${line}` : ''}${quip}`;
    }
  }

  hide() {
    clearInterval(this._timer);
    this._timer = null;
    this.root.hidden = true;
  }
}

/* ------------------------------------------------------------------------- */

export class Tooltip {
  constructor(element, container) {
    this.element = element;
    this.container = container;
  }

  show(rows, at) {
    if (!rows?.length || !at) return this.hide();
    const [first, ...rest] = rows;
    this.element.innerHTML =
      `<span class="aa">${first.key}</span> <span class="k">${first.value}</span><br>`
      + rest.map((r) => `<span class="k">${r.key}</span> ${r.value}`).join('<br>');

    // Flip the tooltip to the other side of the pointer near an edge, rather
    // than letting it run outside the viewport where it cannot be read.
    const bounds = this.container.getBoundingClientRect();
    const box = this.element.getBoundingClientRect();
    const x = at.x + 16 + box.width > bounds.width ? at.x - box.width - 14 : at.x + 16;
    const y = at.y + 12 + box.height > bounds.height ? at.y - box.height - 10 : at.y + 12;
    this.element.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, y)}px)`;
    this.element.classList.add('on');
  }

  hide() { this.element.classList.remove('on'); }
}

/* ------------------------------------------------------------------------- */

export class Ruler {
  constructor(root, { onHover, onPick }) {
    this.root = root;
    this.seqEl = root.querySelector('[data-ruler-seq]');
    this.tickEl = root.querySelector('[data-ruler-ticks]');
    this.titleEl = root.querySelector('[data-ruler-title]');
    this.onHover = onHover;
    this.onPick = onPick;
    this.cells = [];
    this._active = null;

    // One delegated listener rather than one per residue: a 400-residue chain
    // would otherwise attach 800 listeners.
    this.seqEl.addEventListener('mouseover', (event) => {
      const index = event.target?.dataset?.i;
      if (index !== undefined) this.onHover?.(Number(index));
    });
    this.seqEl.addEventListener('mouseleave', () => this.onHover?.(null));
    this.seqEl.addEventListener('click', (event) => {
      const index = event.target?.dataset?.i;
      if (index !== undefined) this.onPick?.(Number(index));
    });
  }

  render(data, colourFor) {
    this.titleEl.textContent = [
      'Sequence',
      data.source?.accession,
      data.source?.name,
      `${data.length} residues`,
    ].filter(Boolean).join('  ·  ');

    const seq = document.createDocumentFragment();
    const ticks = document.createDocumentFragment();
    this.cells = [];

    for (const residue of data.residues) {
      const cell = document.createElement('span');
      cell.className = 'ruler-res';
      cell.textContent = residue.aa;
      cell.dataset.i = residue.i;
      cell.style.color = cssColour(colourFor(residue));
      cell.title = `${residue.aa}${residue.resnum}`;
      seq.appendChild(cell);
      this.cells.push(cell);

      const tick = document.createElement('span');
      tick.style.width = '11px';
      tick.style.flex = 'none';
      tick.style.textAlign = 'center';
      // Every tenth residue, and always the first: an unlabelled ruler is a
      // decoration.
      tick.textContent = (residue.resnum === 1 || residue.resnum % 10 === 0)
        ? String(residue.resnum) : '';
      if (tick.textContent) { tick.style.width = 'auto'; tick.style.minWidth = '11px'; }
      ticks.appendChild(tick);
    }

    this.seqEl.replaceChildren(seq);
    this.tickEl.replaceChildren(ticks);
  }

  recolour(data, colourFor) {
    data.residues.forEach((residue, i) => {
      if (this.cells[i]) this.cells[i].style.color = cssColour(colourFor(residue));
    });
  }

  highlight(index) {
    if (this._active !== null) this.cells[this._active]?.classList.remove('active');
    this._active = index;
    if (index === null || index === undefined) return;
    const cell = this.cells[index];
    if (!cell) return;
    cell.classList.add('active');
    // Only scroll if the residue is actually out of view: scrolling on every
    // hover makes the ruler jitter under the pointer.
    const scroller = this.root.querySelector('.ruler-scroll');
    const cellBox = cell.getBoundingClientRect();
    const box = scroller.getBoundingClientRect();
    if (cellBox.left < box.left || cellBox.right > box.right) {
      scroller.scrollLeft += cellBox.left - box.left - box.width / 2;
    }
  }
}

/* ------------------------------------------------------------------------- */

export function message(container, text, kind = 'info', action = null) {
  const element = document.createElement('div');
  element.className = `message${kind === 'info' ? '' : ` ${kind}`}`;
  element.innerHTML = text;
  if (action) {
    const button = document.createElement('button');
    button.className = 'button';
    button.textContent = action.label;
    button.addEventListener('click', () => { action.onClick(); element.remove(); });
    element.appendChild(button);
  }
  container.appendChild(element);
  return element;
}

export function clearMessages(container) { container.replaceChildren(); }

/** A labelled slider that keeps its readout in step with its value. */
export function slider(host, { label, min, max, step, value, unit = '', format, onInput }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  const render = (v) => (format ? format(v) : `${v}${unit}`);
  wrapper.innerHTML =
    `<div class="label"><span>${label}</span><span class="value"></span></div>`;
  const input = document.createElement('input');
  input.type = 'range';
  Object.assign(input, { min, max, step, value });
  wrapper.appendChild(input);
  const readout = wrapper.querySelector('.value');
  readout.textContent = render(Number(value));
  input.addEventListener('input', () => {
    const v = Number(input.value);
    readout.textContent = render(v);
    onInput(v);
  });
  host.appendChild(wrapper);
  return { wrapper, input, set: (v) => { input.value = v; readout.textContent = render(Number(v)); } };
}

export function toggle(host, { label, checked, onChange }) {
  const wrapper = document.createElement('label');
  wrapper.className = 'switch';
  wrapper.innerHTML = `<input type="checkbox" ${checked ? 'checked' : ''}>`
    + '<span class="track"></span>' + `<span>${label}</span>`;
  const input = wrapper.querySelector('input');
  input.addEventListener('change', () => onChange(input.checked));
  host.appendChild(wrapper);
  return { wrapper, input, set: (v) => { input.checked = v; } };
}

export function select(host, { label, options, value, onChange }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'field';
  wrapper.innerHTML = `<div class="label"><span>${label}</span></div>`;
  const element = document.createElement('select');
  for (const [key, text] of options) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = text;
    if (key === value) option.selected = true;
    element.appendChild(option);
  }
  element.addEventListener('change', () => onChange(element.value));
  wrapper.appendChild(element);
  host.appendChild(wrapper);
  return { wrapper, element };
}

export function stats(host, entries) {
  host.innerHTML = entries.map(({ label, value, wide }) =>
    `<div class="stat${wide ? ' wide' : ''}"><b>${value}</b><span>${label}</span></div>`
  ).join('');
}
