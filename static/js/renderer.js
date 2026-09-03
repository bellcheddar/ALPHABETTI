/**
 * The three.js scene: camera, lighting, ghost backbone, picking and the bloom
 * that makes the Neon direction work.
 *
 * Everything here is mode-agnostic. A mode decides which letters exist, how
 * tall they are and what colour they take; this file decides how they are lit,
 * how the camera behaves and how a pointer turns into a residue index.
 */

import * as THREE from 'three';
import { StageCamera } from './StageCamera.js';
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

    // The subject turns; the camera does not.
    //
    // This is the whole fix for a vertical drag that died halfway across the
    // viewport. A camera orbiting in spherical coordinates HAS a pole, its up
    // vector is degenerate there, and the polar clamp that protects it is what
    // the drag runs into. Rotating the protein against a fixed camera has no
    // pole to protect, so it tumbles freely, like the object in the hand the
    // gesture is meant to be.
    //
    //   subject   carries the attitude quaternion, sits at the world origin
    //     centring  shifts the structure so its centroid IS that origin, so it
    //               spins about its own middle rather than swinging around it
    this.subject = new THREE.Group();
    this.centring = new THREE.Group();
    this.subject.add(this.centring);
    this.scene.add(this.subject);

    this.control = new StageCamera(90);
    // The explicit toggle. StageCamera resumes its own orbit after a delay;
    // this is the only thing that stops it for good.
    this._rotateOff = false;

    this.webgl = new THREE.WebGLRenderer({ antialias: true, alpha: false,
      preserveDrawingBuffer: true });   // required for PNG export
    this.webgl.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.webgl.toneMapping = THREE.ACESFilmicToneMapping;
    this.webgl.toneMappingExposure = 1.05;
    container.appendChild(this.webgl.domElement);

    // Pointer state the gesture handlers and the raycaster both write to, so it
    // has to exist before the listeners are attached.
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._hovered = null;
    this._lastPick = 0;

    this._bindGestures();

    this._lights();
    this._composer();

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
   * Pointer, wheel and touch translated into StageCamera calls and nothing else.
   *
   * Every gesture is a delta since the last event, which is what lets the
   * camera compose rotations about the SCREEN axes and keep "drag right turns
   * right" true even when the protein is upside down.
   */
  _bindGestures() {
    const element = this.webgl.domElement;
    // The browser must not claim the gesture for scrolling or pinch-zoom, or a
    // touch drag scrolls the page instead of turning the protein.
    element.style.touchAction = 'none';

    let lastX = 0, lastY = 0;
    const pointers = new Map();

    element.addEventListener('pointerdown', (event) => {
      element.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      lastX = event.clientX; lastY = event.clientY;

      // Work out what was clicked HERE, not in the click handler.
      //
      // `_dragging` goes true on the next line, and the very next frame clears
      // `_hovered` so that a drag does not pay for a raycast it cannot use. A
      // real click holds the button for about 100 ms, which is several frames,
      // so by the time `click` fires the hover is long gone and nothing is
      // selected. A synthetic click with no delay slips through before any
      // frame runs, which is how this passed its first test.
      //
      // Raycasting rather than reading `_hovered` also makes touch work, where
      // there is no hover before the tap at all.
      this._pressedResidue = this.residueAt(event.clientX, event.clientY);
      this._pressOrigin = { x: event.clientX, y: event.clientY };
      this._dragging = true;
    });

    element.addEventListener('pointermove', (event) => {
      const rect = element.getBoundingClientRect();
      this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      this._pointerScreen = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      this._pointerMoved = true;

      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

      if (pointers.size >= 2) {
        // Pinch: the distance between the first two contacts against its value
        // at the start of the gesture.
        const [a, b] = [...pointers.values()];
        const span = Math.hypot(a.x - b.x, a.y - b.y);
        this._pinchStart = this._pinchStart || span;
        this.control.magnify(span / this._pinchStart);
        return;
      }
      this.control.drag(event.clientX - lastX, event.clientY - lastY);
      lastX = event.clientX; lastY = event.clientY;
      this._notifyRotate();
    });

    const release = (event) => {
      pointers.delete(event.pointerId);
      if (pointers.size < 2) this._pinchStart = null;
      if (pointers.size === 0) {
        this._dragging = false;
        this.control.endInteraction();
        this._notifyRotate();
      }
    };
    for (const type of ['pointerup', 'pointercancel', 'pointerleave']) {
      element.addEventListener(type, release);
    }

    element.addEventListener('wheel', (event) => {
      event.preventDefault();
      // Normalised so a trackpad and a notched wheel feel the same.
      this.control.zoom(-event.deltaY * 0.0016);
      this._notifyRotate();
    }, { passive: false });

    element.addEventListener('dblclick', () => this.resetCamera());

    element.addEventListener('pointerleave', () => {
      this._pointerScreen = null;
      if (this._hovered !== null) { this._hovered = null; this.onHover?.(null, null); }
    });
    element.addEventListener('click', (event) => {
      // Only a press that stayed put counts as a click. Without this, letting
      // go at the end of a drag would select whatever happened to be under the
      // pointer, which is never what the drag was for.
      const origin = this._pressOrigin;
      const travelled = origin
        ? Math.hypot(event.clientX - origin.x, event.clientY - origin.y) : 0;
      this._pressOrigin = null;
      if (travelled > 5) return;
      if (this._pressedResidue !== null && this._pressedResidue !== undefined) {
        this.onPick?.(this._pressedResidue);
      }
    });
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

  /** Renderable content goes INSIDE the subject so it turns with everything else. */
  add(object) { this.centring.add(object); }
  remove(object) { this.centring.remove(object); }

  /**
   * The ghost backbone: a thin tube through the CA positions so the fold reads
   * even when the glyphs are sparse (a low BUMFLUFF scale, or a high GIBBERISH
   * bit threshold, can leave very little on screen).
   */
  setBackbone(caPositions, { opacity = 0.15, radius = 0.34 } = {}) {
    if (this.backbone) {
      this.centring.remove(this.backbone);
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
    this.centring.add(this.backbone);
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

    // Shift the structure so its centroid sits on the subject's origin. Without
    // this the protein would swing around a point off to one side instead of
    // turning about its own middle.
    this.centring.position.copy(centre).multiplyScalar(-1);
    this.subject.quaternion.set(0, 0, 0, 1);

    // The BOUNDING SPHERE, not the box diagonal.
    //
    // Two reasons. A tumbling subject presents a different silhouette every
    // frame, and the only extent that does not change as it turns is the radius
    // about its own centre, so framing on it means the protein never clips and
    // never needs re-framing mid-rotation. And a box diagonal badly
    // overestimates a globular protein -- roughly 1.7x its real diameter --
    // which pushed the camera far enough back that the letters were too small
    // to read, which is the one thing this app cannot afford.
    let radius = 0;
    for (const position of caPositions) {
      radius = Math.max(radius, position.distanceTo(centre));
    }
    // Glyph stacks grow outward along the side chains, so allow for the tallest
    // of them plus a little air rather than framing the backbone alone.
    radius += 4.5;
    this._radius = radius;

    // Fit that sphere to whichever field-of-view axis is narrower.
    const vertical = THREE.MathUtils.degToRad(this.camera.fov);
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * this.camera.aspect);
    const distance = Math.max(14, radius / Math.sin(Math.min(vertical, horizontal) / 2));

    this.control.setDefaultDistance(distance);
    this.control.reframe();
    this._applyCamera();
  }

  /** Push the control's state onto the three.js objects. Called every frame. */
  _applyCamera() {
    const [x, y, z, w] = this.control.attitude;
    this.subject.quaternion.set(x, y, z, w);
    this.camera.position.set(0, 0, this.control.distance);
    this.camera.lookAt(0, 0, 0);

    const distance = this.control.distance;
    this.camera.near = Math.max(0.4, distance / 900);
    this.camera.far = distance * 12;
    this.camera.updateProjectionMatrix();

    // Fog follows the zoom, so pulling back never fades the whole protein out.
    this.scene.fog.near = Math.max(1, distance - (this._radius || 20) * 1.7);
    this.scene.fog.far = distance + (this._radius || 20) * 3.4;
  }

  /**
   * Turn a residue to face the viewer. Used by the ruler and the heatmap.
   *
   * With a fixed camera this is a rotation of the subject rather than a move of
   * the camera: find the shortest rotation that carries the residue's direction
   * from the centre round to +Z, and slerp the attitude onto it.
   */
  flyTo(position, { milliseconds = 620 } = {}) {
    if (!this.control) return;
    // Where the residue sits in the subject's own frame, before any attitude.
    const local = position.clone().add(this.centring.position).normalize();
    if (!Number.isFinite(local.x) || local.lengthSq() === 0) return;

    const target = new THREE.Quaternion()
      .setFromUnitVectors(local, new THREE.Vector3(0, 0, 1));
    const start = new THREE.Quaternion(...this.control.attitude);
    const started = performance.now();

    // A drag mid-flight should win, so the animation checks who is in charge.
    this._flying = true;
    const step = (now) => {
      if (!this._flying) return;
      const t = Math.min(1, (now - started) / milliseconds);
      const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      const q = start.clone().slerp(target, eased);
      this.control.attitude = [q.x, q.y, q.z, q.w];
      this.control.idleTime = 0;          // do not resume orbiting mid-flight
      if (t < 1) requestAnimationFrame(step);
      else this._flying = false;
    };
    requestAnimationFrame(step);
  }

  /**
   * Turn a residue to the front AND come in close. What a click should do.
   *
   * flyTo only rotates; on a 250-residue protein that leaves the residue you
   * asked about still a speck among a thousand others. This dollies to a
   * distance set by the glyph scale rather than the structure's, so the letters
   * are legible whatever size the protein is.
   */
  focusResidue(position, { milliseconds = 620 } = {}) {
    const local = position.clone().add(this.centring.position);
    const radius = local.length();
    this.flyTo(position, { milliseconds });

    // Close enough to read the letter, far enough to keep its neighbours around
    // it. `radius` is how far the residue sits from the structure's centre, and
    // the camera looks at that centre, so the gap has to clear it before any
    // framing is added -- otherwise the camera ends up inside the protein, which
    // is what +12 did: it put ubiquitin at 22 A and filled the screen with two
    // letters and no context.
    //
    // +30 shows roughly 30 A across at this field of view: the residue, its
    // neighbours along the chain, and enough of the fold to place it.
    const target = Math.max(radius + 30, 32);
    const start = this.control.distance;
    const started = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - started) / milliseconds);
      const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      this.control.distance = start + (target - start) * eased;
      this.control.idleTime = 0;
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /**
   * Ring the residue the dock is showing.
   *
   * Centring the camera on a residue is not the same as showing which one it
   * is: at a distance that keeps any context there are a hundred other letters
   * in view and nothing distinguishes the one you clicked. The marker is a thin
   * ring that always faces the camera, so it reads as a target rather than as
   * another piece of geometry.
   */
  setFocusMarker(position) {
    if (!this.focusMarker) {
      const geometry = new THREE.TorusGeometry(2.6, 0.13, 8, 48);
      const material = new THREE.MeshBasicMaterial({
        color: 0x26f5e0, transparent: true, opacity: 0.95,
        depthTest: false,          // never hidden behind the letter it marks
      });
      this.focusMarker = new THREE.Mesh(geometry, material);
      this.focusMarker.renderOrder = 999;
      this.centring.add(this.focusMarker);
    }
    if (!position) { this.focusMarker.visible = false; return; }
    this.focusMarker.visible = true;
    this.focusMarker.position.copy(position);
  }

  /** Set the attitude and distance directly. FOLDEROL drives its own framing. */
  setPose(attitude, distance) {
    if (attitude) this.control.attitude = attitude;
    if (distance) {
      this.control.distance = Math.min(
        Math.max(distance, this.control.minimumDistance), this.control.maximumDistance);
    }
    this.control.idleTime = 0;
  }

  /** Double-click, the reset button, or the 0 key: frame the whole thing again. */
  resetCamera() {
    this._flying = false;
    this.control.reframe();
    this._notifyRotate();
  }

  /** Set the pickable objects. Called by whichever mode is active. */
  setPickTargets(objects, field) {
    this._pickTargets = objects;
    this._field = field;
  }

  /** Which residue is under these client coordinates, or null. No side effects. */
  residueAt(clientX, clientY) {
    if (!this._pickTargets?.length) return null;
    const rect = this.webgl.domElement.getBoundingClientRect();
    this.pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this._pickTargets, false);
    return hits.length ? this._field?.residueFromHit(hits[0]) : null;
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
  _notifyRotate() { this._rotateCallback?.(this.isOrbiting); }

  /** True when the stage is actually turning on its own right now. */
  get isOrbiting() { return !this._rotateOff && this.control.isOrbiting; }

  /** The explicit toggle. This is the only thing that stops the orbit for good. */
  setAutoRotate(on, speed) {
    this._rotateOff = !on;
    if (on) {
      // Start turning now rather than after the resume delay: the user just
      // asked for it, so waiting eight seconds reads as a broken button.
      this.control.endInteraction();
      this.control.idleTime = this.control.resumeDelay;
    }
    if (speed !== undefined) this.control.autoOrbitRate = speed * 0.14;
    this._notifyRotate();
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
    this.__ticks = (this.__ticks || 0) + 1;
    if (!this._running) return;
    requestAnimationFrame(this._tick);

    const delta = this._clock.getDelta();
    this._update?.(delta, now);

    // The stage advances itself: it resumes its own slow orbit once the visitor
    // has been still for long enough, and does nothing while they are dragging.
    if (!this._rotateOff && !this._flying) this.control.advance(delta);
    const wasOrbiting = this._orbitState;
    this._orbitState = this.isOrbiting;
    if (wasOrbiting !== this._orbitState) this._notifyRotate();
    this._applyCamera();

    // The ring is a child of the tumbling subject, so cancel that rotation to
    // keep it face-on; a torus seen edge-on is a line and marks nothing.
    if (this.focusMarker?.visible) {
      this.focusMarker.quaternion.copy(this.subject.quaternion).invert();
    }

    // Raycasting every frame is wasteful and the tooltip does not need 120 Hz.
    // Throttled to roughly 30 Hz, as the build spec asks.
    //
    // Skipped entirely while a button is held: a raycast against twenty
    // instanced meshes carrying up to 4,200 letters is the single most
    // expensive thing per frame, and during a drag it buys nothing because
    // nobody is reading a tooltip while swinging the camera around. It was
    // costing more than half the frame rate exactly when smoothness matters
    // most (13 fps mid-drag under software rendering).
    if (this._dragging && this._hovered !== null) {
      this._hovered = null;
      this.onHover?.(null, null);
    }
    if (!this._dragging && this._pointerMoved && now - this._lastPick > 33) {
      this._lastPick = now;
      this._pointerMoved = false;
      this._pick();
    }

    try {
      this.composer.render();
    } catch (error) {
      this.__lastError = error;
      throw error;
    }

    this.__frames = (this.__frames || 0) + 1;   // reached only if render() did not throw
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
    try {
      this.composer.render();
    } catch (error) {
      this.__lastError = error;
      throw error;
    }
    const url = this.webgl.domElement.toDataURL('image/png');

    this.webgl.setPixelRatio(previousRatio);
    this.scene.background = previousBackground;
    this.composer.setSize(width, height);
    return url;
  }

  dispose() {
    this._running = false;
    this.resizeObserver.disconnect();
    this.composer.dispose();
    this.webgl.dispose();
    this.webgl.domElement.remove();
  }
}
