/* nodes.js — meshes and shaders. The small-node population is ONE
 * InstancedMesh per region (plus a billboarded aura quad layer that shares
 * the same instanceMatrix/instanceColor buffers, so scale animations move
 * both in lockstep). The core is a hand-built layered sun. Sub-agents are
 * individual glow spheres. Everything breathes on a per-node phase hashed
 * from the id — never Math.random(), so a reload doesn't reshuffle.
 */

import * as THREE from "three";
import { hash01 } from "./regions.js";

export const NODE_GEO = new THREE.SphereGeometry(1, 24, 16);
export const PLANE_GEO = new THREE.PlaneGeometry(1, 1);

/* ------------------------------------------------------------ node glow */
const GLOW_VERT = /* glsl */ `
  uniform float uTime;
  attribute float aPhase;
  attribute float aFreshness;
  varying vec3 vNormal;
  varying vec3 vView;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float breathe = 1.0 + 0.06 * sin(uTime * (3.5 + 2.5 * fract(aPhase * 0.7)) + aPhase)
                          * (0.5 + 0.5 * aFreshness);
    vec3 p = position * breathe;
    vec4 mv = modelViewMatrix * instanceMatrix * vec4(p, 1.0);
    vNormal = normalize(normalMatrix * mat3(instanceMatrix) * normal);
    vView = normalize(-mv.xyz);
    vColor = instanceColor;
    vAlpha = 0.45 + 0.75 * aFreshness;
    gl_Position = projectionMatrix * mv;
  }
`;

const GLOW_FRAG = /* glsl */ `
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vView;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float facing = max(dot(normalize(vNormal), normalize(vView)), 0.0);
    float core = pow(facing, 2.5);
    float rim = pow(1.0 - facing, 2.0);
    vec3 color = mix(vColor, vec3(1.0), core * 0.85) + vColor * rim * 1.4;
    float alpha = (core * 0.95 + rim * 0.6) * vAlpha * uOpacity;
    gl_FragColor = vec4(color, alpha);
  }
`;

function makeGlowMaterial(color, uTime) {
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uTime, uOpacity: { value: 1.0 },
      uColor: { value: new THREE.Color(color) },
    },
    vertexShader: GLOW_VERT,
    fragmentShader: GLOW_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  // The hover code sets material.opacity, which ShaderMaterial ignores —
  // route it into the uniform so callers don't special-case it.
  Object.defineProperty(mat, "opacity", {
    get() { return this.uniforms.uOpacity.value; },
    set(v) { this.uniforms.uOpacity.value = v; },
  });
  return mat;
}

/* ---------------------------------------------------- billboard aura quads */
const AURA_VERT = /* glsl */ `
  uniform float uTime;
  attribute float aPhase;
  attribute float aFreshness;
  varying vec2 vUv;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vUv = uv;
    vColor = instanceColor;
    vAlpha = 0.45 + 0.75 * aFreshness;
    float breathe = 1.0 + 0.06 * sin(uTime * (3.5 + 2.5 * fract(aPhase * 0.7)) + aPhase)
                          * (0.5 + 0.5 * aFreshness);
    vec4 mv = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
    float s = length(instanceMatrix[0].xyz) * 2.9 * breathe;
    mv.xy += position.xy * s;
    gl_Position = projectionMatrix * mv;
  }
`;

const AURA_FRAG = /* glsl */ `
  uniform float uOpacity;
  varying vec2 vUv;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    float fall = pow(max(1.0 - d, 0.0), 2.2);
    gl_FragColor = vec4(vColor * 0.6, fall * vAlpha * uOpacity * 0.55);
  }
`;

function makeAuraMaterial(uTime) {
  const mat = new THREE.ShaderMaterial({
    uniforms: { uTime, uOpacity: { value: 1.0 } },
    vertexShader: AURA_VERT,
    fragmentShader: AURA_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  Object.defineProperty(mat, "opacity", {
    get() { return this.uniforms.uOpacity.value; },
    set(v) { this.uniforms.uOpacity.value = v; },
  });
  return mat;
}

/* --------------------------------------------------------- shared helpers */
const _dummy = new THREE.Object3D();

export function radialTexture() {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.55)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/* ---------------------------------------------------- one instanced region */
export function makeInstancedRegion(region, nodes, positions, uTime) {
  const count = nodes.length;
  const mesh = new THREE.InstancedMesh(NODE_GEO, makeGlowMaterial("#ffffff", uTime), count);
  const aura = new THREE.InstancedMesh(PLANE_GEO, makeAuraMaterial(uTime), count);

  const phaseAttr = new Float32Array(count);
  const freshAttr = new Float32Array(count);
  const ids = new Array(count);

  nodes.forEach((node, i) => {
    const p = positions.get(node.id) || { x: 0, y: 0, z: 0 };
    _dummy.position.set(p.x, p.y, p.z);
    _dummy.scale.setScalar(node.size || 0.5);
    _dummy.updateMatrix();
    mesh.setMatrixAt(i, _dummy.matrix);
    mesh.setColorAt(i, new THREE.Color(node.color || "#ffffff"));
    phaseAttr[i] = hash01(node.id);
    freshAttr[i] = Math.min(1, Math.max(0, node.freshness ?? 0.5));
    ids[i] = node.id;
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.setAttribute("aPhase", new THREE.InstancedBufferAttribute(phaseAttr, 1));
  mesh.setAttribute("aFreshness", new THREE.InstancedBufferAttribute(freshAttr, 1));

  // Aura shares the SAME instance buffers as the core mesh — any scale
  // animation (flare, bloom) moves both layers in lockstep for free.
  aura.instanceMatrix = mesh.instanceMatrix;
  aura.instanceColor = mesh.instanceColor;
  aura.setAttribute("aPhase", mesh.getAttribute("aPhase"));
  aura.setAttribute("aFreshness", mesh.getAttribute("aFreshness"));
  aura.renderOrder = -1;
  aura.frustumCulled = false;

  mesh.userData.ids = ids;
  mesh.userData.region = region;
  aura.userData.ids = ids;
  aura.userData.region = region;

  return { region, mesh, aura, ids, flares: new Map(), base: nodes.map((n) => n.size || 0.5) };
}

/** Flare a node: scale ×(1 + 1.2), decaying to 1 over ~1s. */
export function flareNode(info, id) {
  if (!info.ids.includes(id)) return false;
  info.flares.set(id, performance.now() / 1000);
  return true;
}

/** Per-frame: apply decaying flares by rewriting the affected instance
 *  matrices. Called from the frame loop; cheap (few writes while active). */
export function updateInstancedFlares(info, nowS) {
  if (!info.flares.size) return;
  const mesh = info.mesh;
  for (const [id, t0] of info.flares) {
    const idx = info.ids.indexOf(id);
    if (idx < 0) { info.flares.delete(id); continue; }
    const dt = nowS - t0;
    if (dt >= 1.0) { info.flares.delete(id); continue; }
    const k = 1 + 1.2 * Math.pow(1 - dt, 2);
    const base = info.base[idx];
    _dummy.position.setFromMatrixPosition(mesh.getMatrixAt(idx, _dummy.matrix).clone());
    _dummy.scale.setScalar(base * k);
    _dummy.updateMatrix();
    mesh.setMatrixAt(idx, _dummy.matrix);
  }
  mesh.instanceMatrix.needsUpdate = true;
}

/* ---------------------------------------------------------------- core sun */
const RIM_VERT = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;
const RIM_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    float rim = pow(abs(dot(normalize(vNormal), normalize(vView))), 2.0);
    gl_FragColor = vec4(uColor * rim, rim * uOpacity);
  }
`;

/** The layered sun at the center — nucleus + two counter-rotating corona
 *  billboards + two prompt rings (stable and dynamic, spinning about Y —
 *  a torus's symmetry axis is Z, so rotating it about Z is invisible). */
export function makeCore(color, uTime) {
  const group = new THREE.Group();
  const nucleus = new THREE.Group();
  group.add(nucleus);

  const inner = new THREE.Mesh(
    new THREE.SphereGeometry(0.5, 32, 24),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
  nucleus.add(inner);

  const glow = new THREE.Mesh(
    new THREE.SphereGeometry(0.9, 32, 24),
    new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) }, uOpacity: { value: 0.5 } },
      vertexShader: RIM_VERT, fragmentShader: RIM_FRAG,
      side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    }),
  );
  nucleus.add(glow);

  const tex = radialTexture();
  const coronaTight = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false }));
  coronaTight.scale.set(3.2, 3.2, 1);
  const coronaWide = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color, transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false }));
  coronaWide.scale.set(6.5, 6.5, 1);
  nucleus.add(coronaTight, coronaWide);

  const ringGeo = (r, tube) => new THREE.TorusGeometry(r, tube, 8, 72);
  const ringMat = (op) => new THREE.MeshBasicMaterial({ color, transparent: true, opacity: op, blending: THREE.AdditiveBlending, depthWrite: false });
  const ringStable = new THREE.Mesh(ringGeo(1.7, 0.028), ringMat(0.5));
  const ringDynamic = new THREE.Mesh(ringGeo(2.5, 0.022), ringMat(0.35));
  ringDynamic.rotation.x = 1.15; // tilt the dynamic ring
  group.add(ringStable, ringDynamic);

  // Agent prompt rings sit outside the nucleus but belong to the core.
  const flareState = { t0: -10 };
  let flareNow = 0;
  return {
    group, nucleus, rings: [ringStable, ringDynamic],
    coronas: [coronaTight, coronaWide],
    flareState,
    update(t) {
      flareNow = t;
      const breath = 1 + 0.05 * Math.sin(t * 2.2);
      nucleus.scale.setScalar(breath * (1 + 0.35 * Math.max(0, Math.cos((t - flareState.t0) * 2))));
      ringStable.rotation.y = t * 0.25;
      ringDynamic.rotation.y = -t * 0.4;
      ringDynamic.rotation.z = 0.15 + 0.05 * Math.sin(t * 0.9);
      ringDynamic.material.opacity = 0.22 + 0.13 * Math.sin(t * 1.7) + 0.6 * Math.max(0, Math.cos((t - flareState.t0) * 2));
      coronaTight.material.rotation = t * 0.6;   // counter-rotate the two coronas
      coronaWide.material.rotation = -t * 0.35;
      coronaTight.material.opacity = 0.42 + 0.1 * Math.sin(t * 1.3);
    },
    flare() { flareState.t0 = flareNow; },
  };
}

/* ---------------------------------------------------------------- agents */
export function makeAgentMesh(node, position, uTime) {
  const color = node.color || "#E88FB3";
  const mesh = new THREE.Mesh(NODE_GEO, makeGlowMaterial(color, uTime));
  mesh.position.set(position.x, position.y, position.z);
  mesh.scale.setScalar(node.size || 1.05);
  mesh.userData.id = node.id;
  mesh.userData.region = node.region;
  mesh.userData.node = node;
  const aura = new THREE.Sprite(new THREE.SpriteMaterial({ map: radialTexture(), color, transparent: true, opacity: 0.28, blending: THREE.AdditiveBlending, depthWrite: false }));
  aura.position.copy(mesh.position);
  aura.scale.setScalar((node.size || 1.05) * 4.2);
  return { mesh, aura };
}
