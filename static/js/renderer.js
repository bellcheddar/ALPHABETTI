/**
 * The three.js scene: camera, lighting, ghost backbone, picking and the bloom
 * that makes the Neon direction work.
 *
 * Everything here is mode-agnostic. A mode decides which letters exist, how
 * tall they are and what colour they take; this file decides how they are lit,
 * how the camera behaves and how a pointer turns into a residue index.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// Neon palette, kept in sync with static/css/alphabetti.css.
const VOID = 0x06060e;
const CYAN = 0x26f5e0;
const MAGENTA = 0xff2d9b;

export class Renderer {
  constructor(container) {
    this.container = container;
    this.residues = [];
    this.onHover = null;
    this.onPick = null;

    this.scene = new THREE.Scene();
    this.scene.background = this._gradientBackground();
    // Fog gives depth on long chains, where the far end of a 400-residue fold
    // would otherwise be as crisp as the near end and read as flat. Near and
    // far are set from the structure in frameStructure().
    this.scene.fog = new THREE.Fog(VOID, 60, 220);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.5, 3000);
    this.camera.position.set(0, 0, 90);

    this.webgl = new THREE.WebGLRenderer({ antialias: true, alpha: false,
      preserveDrawingBuffer: true });   // required for PNG export
    this.webgl.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.webgl.toneMapping = THREE.ACESFilmicToneMapping;
    this.webgl.toneMappingExposure = 1.05;
    container.appendChild(this.webgl.domElement);

    this.controls = new OrbitControls(this.camera, this.webgl.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 0.85;
    this.controls.minDistance = 5;
    this.controls.maxDistance = 900;
    // Auto-rotate stops on the first real interaction. It is an attract loop,
    // not a feature to fight with while trying to look at something.
    const stop = () => { this.controls.autoRotate = false; this._notifyRotate(); };
    this.webgl.domElement.addEventListener('pointerdown', stop, { once: true });
    this.webgl.domElement.addEventListener('wheel', stop, { once: true, passive: true });

    this._lights();
    this._composer();

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._hovered = null;
    this._lastPick = 0;
    this._bindPointer();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();

    this._clock = new THREE.Clock();
    this._frames = 0;
    this._fpsAt = performance.now();
    this.fps = 0;
    this._tick = this._tick.bind(this);
    this._running = true;
    requestAnimationFrame(this._tick);
  }

  /**
   * A soft vertical gradient rather than a flat fill. Flat black makes the
   * bloom look like it is floating in nothing; a gradient gives the letters a
   * horizon to sit against.
   */
  _gradientBackground() {
    const canvas = document.createElement('canvas');
    canvas.width = 2; canvas.height = 256;
    const context = canvas.getContext('2d');
    const gradient = context.createLinearGradient(0, 0, 0, 256);
    gradient.addColorStop(0, '#101426');
    gradient.addColorStop(0.55, '#080a16');
    gradient.addColorStop(1, '#04040a');
    context.fillStyle = gradient;
    context.fillRect(0, 0, 2, 256);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }

  _lights() {
    // Three-point, plus a hemisphere so the underside of a letter is never pure
    // black. Without the hemisphere, letters facing away from the key read as
    // holes rather than as surfaces.
    const key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(4, 7, 9);
    const fill = new THREE.DirectionalLight(CYAN, 0.75);
    fill.position.set(-7, -2, 5);
    const rim = new THREE.DirectionalLight(MAGENTA, 0.7);
    rim.position.set(-3, 4, -9);
    const hemisphere = new THREE.HemisphereLight(0x5566aa, 0x0a0a14, 0.55);
    this.scene.add(key, fill, rim, hemisphere);
    this.lights = { key, fill, rim, hemisphere };
  }

  _composer() {
    this.composer = new EffectComposer(this.webgl);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    // The bloom IS the Neon direction. Threshold above 0.6 so only genuinely
    // bright surfaces glow: with it lower, the whole scene hazes over and the
    // letters stop being legible, which defeats the point of an app made of
    // letters.
    // strength, radius, threshold. Threshold 0.78 rather than 0.68 because the
    // magenta rim light was pushing ordinary letters over the line and hazing
    // the middle of the scene; only genuinely bright surfaces should bloom.
    this.bloom = new UnrealBloomPass(
      new THREE.Vector2(1, 1), 0.48, 0.36, 0.78,
    );
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
  }

  /** Material shared by every glyph. Emissive so the letters read as tubes. */
  makeMaterial({ emissiveIntensity = 0.55 } = {}) {
    return new THREE.MeshStandardMaterial({
      roughness: 0.28,
      metalness: 0.35,
      // Vertex colours carry the per-instance colour set by GlyphField.
      vertexColors: false,
      emissiveIntensity,
      emissive: new THREE.Color(0x000000),
      toneMapped: true,
    });
  }

  add(object) { this.scene.add(object); }
  remove(object) { this.scene.remove(object); }

  /**
   * The ghost backbone: a thin tube through the CA positions so the fold reads
   * even when the glyphs are sparse (a low BUMFLUFF scale, or a high GIBBERISH
   * bit threshold, can leave very little on screen).
   */
  setBackbone(caPositions, { opacity = 0.15, radius = 0.34 } = {}) {
    if (this.backbone) {
      this.scene.remove(this.backbone);
      this.backbone.geometry.dispose();
      this.backbone.material.dispose();
      this.backbone = null;
    }
    if (!caPositions || caPositions.length < 2) return;

    const curve = new THREE.CatmullRomCurve3(caPositions, false, 'centripetal');
    // Segment count follows the chain length: a fixed count either wastes
    // geometry on a short peptide or corners visibly on a long one.
    const geometry = new THREE.TubeGeometry(
      curve, Math.min(1400, caPositions.length * 6), radius, 6, false,
    );
    const material = new THREE.MeshStandardMaterial({
      color: 0x8ab4d8, transparent: true, opacity,
      roughness: 0.55, metalness: 0.1, depthWrite: false,
    });
    this.backbone = new THREE.Mesh(geometry, material);
    this.scene.add(this.backbone);
  }

  setBackboneOpacity(opacity) {
    if (!this.backbone) return;
    this.backbone.material.opacity = opacity;
    this.backbone.visible = opacity > 0.001;
  }

  /**
   * Point the camera at a structure and set the fog to match its extent.
   * Called once per loaded protein, not per frame.
   */
  frameStructure(caPositions) {
    if (!caPositions?.length) return;
    const box = new THREE.Box3().setFromPoints(caPositions);
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();

    this.controls.target.copy(centre);
    // 1.05 rather than 1.35: the letters are the product and a loose frame
    // leaves them too small to read, which is the one thing this app cannot
    // afford. Orbit controls let anyone pull back who wants to.
    const distance = Math.max(14, size * 1.05);
    this.camera.position.copy(centre).add(new THREE.Vector3(0, size * 0.12, distance));
    this.camera.near = Math.max(0.4, distance / 900);
    this.camera.far = distance * 12;
    this.camera.updateProjectionMatrix();

    this.scene.fog.near = distance * 0.75;
    this.scene.fog.far = distance * 2.6;
    this.controls.update();
    this._home = { position: this.camera.position.clone(), target: centre.clone() };
  }

  /** Fly the camera to one residue. Used by the ruler and the BALDERDASH heatmap. */
  flyTo(position, { distance = 26, milliseconds = 620 } = {}) {
    const startTarget = this.controls.target.clone();
    const startPosition = this.camera.position.clone();
    // Approach from where the camera already is, so the move reads as a dolly
    // rather than a cut to an unrelated viewpoint.
    const direction = startPosition.clone().sub(startTarget).normalize();
    const endPosition = position.clone().add(direction.multiplyScalar(distance));
    const started = performance.now();
    this.controls.autoRotate = false;
    this._notifyRotate();

    const step = (now) => {
      const t = Math.min(1, (now - started) / milliseconds);
      // easeInOutCubic: a linear fly-to looks mechanical at both ends.
      const e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      this.controls.target.lerpVectors(startTarget, position, e);
      this.camera.position.lerpVectors(startPosition, endPosition, e);
      this.controls.update();
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  resetCamera() {
    if (!this._home) return;
    this.controls.target.copy(this._home.target);
    this.camera.position.copy(this._home.position);
    this.controls.update();
  }

  _bindPointer() {
    const element = this.webgl.domElement;
    element.addEventListener('pointermove', (event) => {
      const rect = element.getBoundingClientRect();
      this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      this._pointerScreen = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      this._pointerMoved = true;
    });
    element.addEventListener('pointerleave', () => {
      this._pointerScreen = null;
      if (this._hovered !== null) {
        this._hovered = null;
        this.onHover?.(null, null);
      }
    });
    element.addEventListener('click', () => {
      if (this._hovered !== null) this.onPick?.(this._hovered);
    });
  }

  /** Set the pickable objects. Called by whichever mode is active. */
  setPickTargets(objects, field) {
    this._pickTargets = objects;
    this._field = field;
  }

  _pick() {
    if (!this._pickTargets?.length || !this._pointerScreen) return;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this._pickTargets, false);
    const index = hits.length ? this._field?.residueFromHit(hits[0]) : null;
    if (index !== this._hovered) {
      this._hovered = index ?? null;
      this.onHover?.(this._hovered, this._pointerScreen);
    }
  }

  onAutoRotateChange(callback) { this._rotateCallback = callback; }
  _notifyRotate() { this._rotateCallback?.(this.controls.autoRotate); }

  setAutoRotate(on, speed) {
    this.controls.autoRotate = on;
    if (speed !== undefined) this.controls.autoRotateSpeed = speed;
  }

  resize() {
    const { clientWidth: width, clientHeight: height } = this.container;
    if (!width || !height) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.webgl.setSize(width, height, false);
    this.composer.setSize(width, height);
    this.bloom.resolution.set(width, height);
  }

  /** Per-frame hook a mode can set, for FOLDEROL's animation. */
  setUpdate(callback) { this._update = callback; }

  _tick(now) {
    if (!this._running) return;
    requestAnimationFrame(this._tick);

    const delta = this._clock.getDelta();
    this._update?.(delta, now);
    this.controls.update();

    // Raycasting every frame is wasteful and the tooltip does not need 120 Hz.
    // Throttled to roughly 30 Hz, as the build spec asks.
    if (this._pointerMoved && now - this._lastPick > 33) {
      this._lastPick = now;
      this._pointerMoved = false;
      this._pick();
    }

    this.composer.render();

    this._frames++;
    if (now - this._fpsAt >= 1000) {
      this.fps = Math.round((this._frames * 1000) / (now - this._fpsAt));
      this._frames = 0;
      this._fpsAt = now;
    }
  }

  /** PNG data URL. `transparent` renders once with no background. */
  snapshot({ scale = 1, transparent = false } = {}) {
    const { clientWidth: width, clientHeight: height } = this.container;
    const previousRatio = this.webgl.getPixelRatio();
    const previousBackground = this.scene.background;

    this.webgl.setPixelRatio(Math.min(4, previousRatio * scale));
    if (transparent) this.scene.background = null;
    this.composer.setSize(width, height);
    this.composer.render();
    const url = this.webgl.domElement.toDataURL('image/png');

    this.webgl.setPixelRatio(previousRatio);
    this.scene.background = previousBackground;
    this.composer.setSize(width, height);
    return url;
  }

  dispose() {
    this._running = false;
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.composer.dispose();
    this.webgl.dispose();
    this.webgl.domElement.remove();
  }
}
