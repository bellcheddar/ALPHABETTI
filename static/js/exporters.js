/**
 * Exports: GLB, STL, PNG and animated GIF.
 *
 * The instanced meshes that make the app fast are the problem here. Every
 * exporter except PNG needs real geometry, and an InstancedMesh carries one
 * geometry plus a matrix buffer. So the export path bakes instances into merged
 * BufferGeometry, which is slow and memory-hungry and therefore happens on
 * demand rather than being kept around.
 */

import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';

/** Bake a GlyphField's instances into ordinary meshes, ready to export. */
function bake(field, { withColour = true } = {}) {
  const group = new THREE.Group();
  group.name = 'ALPHABETTI';
  const matrix = new THREE.Matrix4();
  const colour = new THREE.Color();

  for (const mesh of field.objects) {
    if (!mesh.count) continue;
    const source = mesh.geometry;

    for (let i = 0; i < mesh.count; i++) {
      mesh.getMatrixAt(i, matrix);
      const geometry = source.clone().applyMatrix4(matrix);

      if (withColour && mesh.instanceColor) {
        colour.fromBufferAttribute(mesh.instanceColor, i);
        // Bake the instance colour into vertex colours: glTF has no concept of
        // an instance colour, so without this every letter exports grey.
        const count = geometry.attributes.position.count;
        const colours = new Float32Array(count * 3);
        for (let v = 0; v < count; v++) {
          colours[v * 3] = colour.r;
          colours[v * 3 + 1] = colour.g;
          colours[v * 3 + 2] = colour.b;
        }
        geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
      }

      group.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
        vertexColors: withColour, roughness: 0.3, metalness: 0.3,
      })));
    }
  }
  return group;
}

function disposeGroup(group) {
  group.traverse((object) => {
    object.geometry?.dispose();
    object.material?.dispose();
  });
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoke on the next tick: revoking synchronously can cancel the download in
  // Safari before it has started reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export async function exportGLB(field, name = 'alphabetti') {
  const group = bake(field, { withColour: true });
  try {
    const result = await new GLTFExporter().parseAsync(group, { binary: true });
    download(new Blob([result], { type: 'model/gltf-binary' }), `${name}.glb`);
    return { meshes: group.children.length };
  } finally {
    disposeGroup(group);
  }
}

export function exportSTL(field, name = 'alphabetti') {
  // STL has no colour, so vertex colours are skipped: they would double the
  // baked geometry's memory for data the format cannot carry.
  const group = bake(field, { withColour: false });
  try {
    const text = new STLExporter().parse(group, { binary: false });
    download(new Blob([text], { type: 'model/stl' }), `${name}.stl`);

    // Manifold-ness matters for printing and this geometry is not guaranteed to
    // be: separate letters float free wherever a stack has a gap, and extruded
    // glyph shells are not solid. Warn rather than pretend.
    return {
      meshes: group.children.length,
      warning: 'Letters are separate shells and are not joined to the backbone. '
             + 'Run it through a mesh repair (or print with supports) before '
             + 'sending it to a printer.',
    };
  } finally {
    disposeGroup(group);
  }
}

export function exportPNG(renderer, { scale = 2, transparent = false,
                                      name = 'alphabetti' } = {}) {
  const url = renderer.snapshot({ scale, transparent });
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${name}${scale > 1 ? `@${scale}x` : ''}.png`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  return { scale, transparent };
}

/**
 * Animated GIF over one FOLDEROL loop.
 *
 * gif.js runs its encoder in a web worker, and the worker script has to come
 * from the same CDN as the library or the browser refuses it. Frames are
 * captured at a reduced size because a full-resolution GIF of a 4-second loop
 * is tens of megabytes, which is not a file anybody wants.
 */
export async function exportGIF(renderer, mode, {
  width = 640, frames = 48, delay = 60, name = 'alphabetti', onProgress,
} = {}) {
  if (typeof window.GIF === 'undefined') {
    throw new Error('The GIF encoder has not loaded. Check the network tab.');
  }

  const source = renderer.webgl.domElement;
  const height = Math.round(width * (source.clientHeight / source.clientWidth));
  const gif = new window.GIF({
    workers: 2, quality: 12, width, height,
    workerScript: 'https://cdnjs.cloudflare.com/ajax/libs/gif.js/0.2.0/gif.worker.js',
  });

  const scratch = document.createElement('canvas');
  scratch.width = width; scratch.height = height;
  const context = scratch.getContext('2d');

  const wasPlaying = mode.playing;
  mode.playing = false;

  for (let frame = 0; frame < frames; frame++) {
    mode.progress = frame / frames;
    mode.build();
    renderer.composer.render();
    context.drawImage(renderer.webgl.domElement, 0, 0, width, height);
    gif.addFrame(context, { copy: true, delay });
    onProgress?.((frame + 1) / frames);
    // Yield so the page stays responsive and the progress readout can paint.
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  mode.playing = wasPlaying;

  return new Promise((resolve, reject) => {
    gif.on('finished', (blob) => {
      download(blob, `${name}.gif`);
      resolve({ frames, width, height, bytes: blob.size });
    });
    gif.on('abort', () => reject(new Error('GIF encoding was aborted.')));
    gif.render();
  });
}

export function downloadText(text, filename, type = 'text/plain') {
  download(new Blob([text], { type }), filename);
}
