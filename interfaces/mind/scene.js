/* scene.js — assembly and the frame loop.
 *
 * Loads the skeleton, bakes every layout once, builds one instanced mesh
 * per region (plus aura), merges edges per kind, and runs the loop: one
 * shared uTime written once per frame, bloom, pulses, ambient life, an FPS
 * governor, and the live observer socket. The inspector module is loaded
 * with a dynamic import() at the END so there is no import cycle.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

import {
  loadSkeleton, getSkeleton, getNode, hasNode, registerNode, getEdges,
  allNodes, setStats, updateStatsLine, connectObserver,
} from "./data.js";
import { ANCHORS, layoutRegion, hash01 } from "./regions.js";
import {
  makeInstancedRegion, makeCore, makeAgentMesh, radialTexture, flareNode, updateInstancedFlares,
} from "./nodes.js";
import { buildEdgeSystem, EDGE_REGION, EDGE_COLORS } from "./edges.js";

/* ------------------------------------------------------------ context */
export const mind = {
  scene: null, camera: null, controls: null, renderer: null, composer: null,
  positions: new Map(), regionGroups: new Map(), instanced: [],
  core: null, edgeSystem: null, membrane: null, labelRenderer: null,
  bloomPass: null, pulsePool: [], activePulses: [], driftPoints: null,
  agentActivity: new Map(), zoom: null, interacting: false,
  liveHandlers: null,
};

const uTime = { value: 0 };

const CORE_ID = "agent:emma";

/* ------------------------------------------------------------ renderer */
function setupRenderer() {
  const view = document.getElementById("view");
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  view.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#05070B");
  scene.fog = new THREE.FogExp2(0x05070b, 0.012);

  const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 400);
  camera.position.set(4, 7, 30);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.4;
  controls.minDistance = 5;
  controls.maxDistance = 80;
  controls.addEventListener("start", () => { mind.interacting = true; });
  controls.addEventListener("end", () => {
    setTimeout(() => { mind.interacting = false; }, 800);
  });

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 1.15, 0.6, 0.72);
  composer.addPass(bloom);

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(window.innerWidth, window.innerHeight);
  labelRenderer.domElement.style.position = "absolute";
  labelRenderer.domElement.style.top = "0";
  labelRenderer.domElement.style.pointerEvents = "none";
  view.appendChild(labelRenderer.domElement);

  window.addEventListener("resize", () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    composer.setSize(window.innerWidth, window.innerHeight);
    labelRenderer.setSize(window.innerWidth, window.innerHeight);
  });

  Object.assign(mind, { scene, camera, controls, renderer, composer, bloomPass: bloom, labelRenderer });
}

/* ------------------------------------------------------------ starfield */
function buildStarfield() {
  const COUNT = 600;
  const pos = new Float32Array(COUNT * 3);
  const phase = new Float32Array(COUNT);
  const size = new Float32Array(COUNT);
  for (let i = 0; i < COUNT; i++) {
    const r = 80 + hash01("star" + i) * 80;
    const theta = hash01("t" + i) * Math.PI * 2;
    const phi = Math.acos(2 * hash01("p" + i) - 1);
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
    phase[i] = hash01("ph" + i) * Math.PI * 2;
    size[i] = 0.6 + hash01("s" + i) * 1.8;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
  geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: { uTime },
    vertexShader: /* glsl */ `
      attribute float aPhase;
      attribute float aSize;
      uniform float uTime;
      varying float vTwinkle;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * (28.0 / max(-mv.z, 1.0));
        vTwinkle = 0.55 + 0.45 * sin(uTime * 0.9 + aPhase);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      varying float vTwinkle;
      void main() {
        vec2 c = gl_PointCoord - 0.5;
        float d = length(c);
        if (d > 0.5) discard;
        float fall = pow(max(1.0 - d * 2.0, 0.0), 1.6);
        gl_FragColor = vec4(vec3(1.0) * fall * vTwinkle, fall * vTwinkle);
      }
    `,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const pts = new THREE.Points(geo, mat);
  pts.frustumCulled = false;
  mind.scene.add(pts);
}

/* ------------------------------------------------------------ membrane */
function buildMembrane() {
  const mat = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: new THREE.Color("#3B82F6") }, uOpacity: { value: 0.06 }, uRipple: { value: 0 } },
    vertexShader: /* glsl */ `
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vView = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uOpacity;
      uniform float uRipple;
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        // BackSide flips normals — abs() keeps ONLY the silhouette lit.
        float fres = pow(abs(dot(normalize(vNormal), normalize(vView))), 3.0);
        float op = uOpacity + uRipple * 0.16;
        gl_FragColor = vec4(uColor * fres, fres * op);
      }
    `,
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(26, 48, 32), mat);
  mesh.renderOrder = -2;
  mind.scene.add(mesh);
  mind.membrane = mesh;
  mind.membraneRipple = { active: 0 }; // seconds since ripple start
}

/* ------------------------------------------------------------ assembly */
function placeRegions(skeleton) {
  const nodesByRegion = new Map();
  for (const n of skeleton.nodes || []) {
    if (!nodesByRegion.has(n.region)) nodesByRegion.set(n.region, []);
    nodesByRegion.get(n.region).push(n);
  }
  const edges = skeleton.edges || [];
  for (const [region, nodes] of nodesByRegion) {
    const positions = layoutRegion(region, nodes, edges, ANCHORS[region] || { x: 0, y: 0, z: 0 });
    for (const [id, p] of positions) mind.positions.set(id, new THREE.Vector3(p.x, p.y, p.z));
  }
  // Core sits at the origin.
  const coreNode = (skeleton.nodes || []).find((n) => n.type === "core");
  if (coreNode) mind.positions.set(coreNode.id, new THREE.Vector3(0, 0, 0));
}

function buildRegionGroups(skeleton) {
  const nodesByRegion = new Map();
  for (const n of skeleton.nodes || []) {
    if (!nodesByRegion.has(n.region)) nodesByRegion.set(n.region, []);
    nodesByRegion.get(n.region).push(n);
  }
  for (const [region, nodes] of nodesByRegion) {
    const group = new THREE.Group();
    mind.scene.add(group);
    mind.regionGroups.set(region, group);

    if (region === "core") {
      mind.core = makeCore(EDGE_COLORS.core || "#2DD4A8", uTime);
      group.add(mind.core.group);
      continue;
    }
    // Agents are instanced too — one draw call, raycast-friendly.
    const info = makeInstancedRegion(region, nodes, mind.positions, uTime);
    group.add(info.mesh, info.aura);
    mind.instanced.push(info);
    if (region === "agents") mind.agentInfo = info;
  }
}

function buildEdges() {
  mind.edgeSystem = buildEdgeSystem(mind.getEdges(), mind.positions, uTime);
  for (const { kind, object } of mind.edgeSystem.lines) {
    const region = EDGE_REGION[kind] || "memory";
    const group = mind.regionGroups.get(region);
    (group || mind.scene).add(object);
  }
}

function buildLabels(skeleton) {
  // Region headings only — never one label per node.
  for (const [region, anchor] of Object.entries(ANCHORS)) {
    if (!mind.regionGroups.has(region)) continue;
    const el = document.createElement("div");
    el.className = "mind-label region";
    el.textContent = region;
    const obj = new CSS2DObject(el);
    obj.position.set(anchor.x, anchor.y - 5.2, anchor.z);
    mind.scene.add(obj);
  }
  // Agent labels.
  for (const n of skeleton.nodes || []) {
    if (n.type !== "agent" || n.id === CORE_ID) continue;
    const p = mind.positions.get(n.id);
    if (!p) continue;
    const el = document.createElement("div");
    el.className = "mind-label";
    el.textContent = n.label;
    const obj = new CSS2DObject(el);
    obj.position.copy(p).y += 1.5;
    mind.scene.add(obj);
  }
}

/* ------------------------------------------------------------ pulses */
function makePulsePool() {
  const tex = radialTexture();
  for (let i = 0; i < 64; i++) {
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: tex, color: 0xffffff, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    sprite.visible = false;
    mind.scene.add(sprite);
    mind.pulsePool.push(sprite);
  }
}

/** Fire a pulse from one node to another. Drops silently if either end
 *  cannot be resolved — an animation between two meaningless points is a
 *  lie. If no static edge exists, an ephemeral straight curve is used. */
export function firePulse(fromId, toId, color, opts = {}) {
  const p0 = mind.positions.get(fromId);
  const p1 = mind.positions.get(toId);
  if (!p0 || !p1) return false;
  const key = fromId + "|" + toId;
  const record = mind.edgeSystem && mind.edgeSystem.curveMap.get(key);
  const curve = record ? record.curve : new THREE.LineCurve3(p0.clone(), p1.clone());
  const sprite = mind.pulsePool.find((s) => !s.visible);
  if (!sprite) return false;
  sprite.visible = true;
  sprite.material.color.set(color);
  sprite.material.opacity = opts.opacity ?? 1.0;
  const base = opts.scale ?? 0.8;
  mind.activePulses.push({
    sprite, curve, t0: performance.now() / 1000,
    duration: opts.duration ?? 0.9, base,
    swell: opts.swell ?? 1.6,
  });
  return true;
}

function updatePulses(nowS) {
  for (let i = mind.activePulses.length - 1; i >= 0; i--) {
    const p = mind.activePulses[i];
    const t = (nowS - p.t0) / p.duration;
    if (t >= 1) {
      p.sprite.visible = false;
      p.sprite.material.opacity = 0;
      mind.activePulses.splice(i, 1);
      continue;
    }
    const pt = p.curve.getPoint(t);
    p.sprite.position.copy(pt);
    const k = p.base * (1 + p.swell * Math.sin(Math.PI * t));
    p.sprite.scale.setScalar(k);
    p.sprite.material.opacity = Math.sin(Math.PI * t) * (p.opacity ?? 1);
    p.sprite.material.needsUpdate = true;
  }
}

/** Pulse between two absolute positions (used for nodes that aren't in
 *  the skeleton, e.g. a brand-new memory). Same pooled sprites. */
export function firePulsePos(fromPos, toPos, color, opts = {}) {
  const sprite = mind.pulsePool.find((s) => !s.visible);
  if (!sprite) return false;
  const curve = new THREE.LineCurve3(fromPos.clone(), toPos.clone());
  sprite.visible = true;
  sprite.material.color.set(color);
  sprite.material.opacity = opts.opacity ?? 1.0;
  const base = opts.scale ?? 0.8;
  mind.activePulses.push({
    sprite, curve, t0: performance.now() / 1000,
    duration: opts.duration ?? 0.9, base,
    swell: opts.swell ?? 1.6, opacity: opts.opacity ?? 1.0,
  });
  return true;
}

/* ------------------------------------------------------------ ambient */
function buildAmbientDrift() {
  const COUNT = 40;
  const positions = new Float32Array(COUNT * 3);
  const seed = [];
  for (let i = 0; i < COUNT; i++) {
    seed.push({
      a: new THREE.Vector3().copy(new THREE.Vector3(ANCHORS.working.x, ANCHORS.working.y, ANCHORS.working.z)),
      b: new THREE.Vector3().copy(new THREE.Vector3(ANCHORS.memory.x, ANCHORS.memory.y, ANCHORS.memory.z)),
      c: new THREE.Vector3(),
      t: hash01("drift" + i),
      speed: 0.004 + hash01("ds" + i) * 0.006,
      off: new THREE.Vector3((hash01("ox" + i) - 0.5) * 2.5, (hash01("oy" + i) - 0.5) * 2.5, (hash01("oz" + i) - 0.5) * 2.5),
    });
    positions[i * 3] = seed[i].a.x; positions[i * 3 + 1] = seed[i].a.y; positions[i * 3 + 2] = seed[i].a.z;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const mat = new THREE.PointsMaterial({
    map: radialTexture(), color: 0x67e8f9, size: 0.22, transparent: true, opacity: 0.35,
    blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  });
  const pts = new THREE.Points(geo, mat);
  pts.frustumCulled = false;
  mind.scene.add(pts);
  mind.driftPoints = { points: pts, seed, geo };
}

function updateDrift(nowS) {
  if (!mind.driftPoints) return;
  const attr = mind.driftPoints.geo.attributes.position;
  const seed = mind.driftPoints.seed;
  for (let i = 0; i < seed.length; i++) {
    const s = seed[i];
    s.t += s.speed;
    if (s.t > 1) {
      s.t = 0;
      s.off.set((hash01("ox" + i + Math.floor(nowS)) - 0.5) * 2.5, (hash01("oy" + i) - 0.5) * 2.5, (hash01("oz" + i) - 0.5) * 2.5);
    }
    const t = s.t;
    s.c.copy(s.a).add(s.off);
    const mid = s.c.clone().add(s.b.clone().add(s.off)).multiplyScalar(0.5);
    mid.y += 1.2;
    const inv = 1 - t;
    const px = inv * inv * s.c.x + 2 * inv * t * mid.x + t * t * s.b.x;
    const py = inv * inv * s.c.y + 2 * inv * t * mid.y + t * t * s.b.y;
    const pz = inv * inv * s.c.z + 2 * inv * t * mid.z + t * t * s.b.z;
    attr.setXYZ(i, px, py, pz);
  }
  attr.needsUpdate = true;
}

/* ------------------------------------------------------------ governor */
const fps = { samples: [], lowSince: 0, degraded1: false, degraded2: false, disabled: false };

function governor(nowS, dt) {
  if (document.hidden) return;
  fps.samples.push(dt);
  if (fps.samples.length > 60) fps.samples.shift();
  if (fps.samples.length < 30) return;
  const avg = fps.samples.reduce((a, b) => a + b, 0) / fps.samples.length;
  const low = avg > 1 / 30;
  if (low) {
    if (!fps.lowSince) fps.lowSince = nowS;
    if (!fps.degraded1 && nowS - fps.lowSince > 3) {
      fps.degraded1 = true;
      mind.bloomPass.enabled = false;
      console.info("[mind] FPS governor step 1: bloom off");
    } else if (fps.degraded1 && !fps.degraded2 && nowS - fps.lowSince > 6) {
      fps.degraded2 = true;
      fps.disabled = true;
      for (const line of mind.edgeSystem.lines) {
        if (line.material.uniforms.uShimmer) line.material.uniforms.uShimmer.value = 0;
        if (line.material.uniforms.uFlow) line.material.uniforms.uFlow.value = 0;
      }
      console.info("[mind] FPS governor step 2: pulses and shimmer frozen");
    }
  } else {
    fps.lowSince = 0;
  }
}

/* ------------------------------------------------------------ idle drift */
let prevDriftY = 0;
function idleDrift(nowS) {
  if (mind.interacting) { prevDriftY = 0; return; }
  const y = Math.sin(nowS * 0.12) * 0.5;
  const delta = y - prevDriftY;
  prevDriftY = y;
  mind.camera.position.y += delta;
}

/* ------------------------------------------------------------ live layer */
const liveHandlers = {
  onRecall(nodeIds) {
    for (const id of nodeIds || []) {
      if (!hasNode(id)) continue;         // never animate a node that isn't there
      for (const info of mind.instanced) flareNode(info, id);
      firePulse(id, CORE_ID, "#A78BFA", { opacity: 0.95 });
    }
  },
  onWrite(id, kind) {
    if (!id) return;
    if (hasNode(id)) {
      for (const info of mind.instanced) flareNode(info, id);
      firePulse(id, CORE_ID, "#67E8F9", { opacity: 0.8 });
    } else {
      // A brand-new memory — bloom it in near the memory region.
      const anchor = ANCHORS.memory;
      const p = new THREE.Vector3(
        anchor.x + (hash01("new" + id) - 0.5) * 6,
        anchor.y + (hash01("newy" + id) - 0.5) * 6,
        anchor.z + (hash01("newz" + id) - 0.5) * 5,
      );
  mind.positions.set(id, p);
  registerNode({ id, type: "memory", region: "memory", label: id, color: "#A78BFA", size: 0.6, freshness: 1, extra: {} });
  // Standalone sphere — a fresh node can't join an InstancedMesh
  // allocated at load size, so it blooms honestly as its own bead.
  const { mesh, aura } = makeAgentMesh(
    { id, type: "memory", region: "memory", color: "#A78BFA", size: 0.6, freshness: 1 }, p, uTime);
  mesh.scale.setScalar(0.01);
  aura.scale.setScalar(0.01);
  mind.scene.add(mesh, aura);
  mind.sceneBloom = mind.sceneBloom || [];
  mind.standalone = mind.standalone || new Map();
  mind.standalone.set(id, { mesh, aura, target: 0.6 });
  mind.sceneBloom.push({ id, t0: performance.now() / 1000 });
  firePulsePos(p.clone().addScaledVector(new THREE.Vector3(0, 1, 0), 0.5), p, "#67E8F9", { opacity: 0.7, scale: 0.5 });
    }
  },
  onDispatch(id) {
    if (!hasNode(id)) return;
    firePulse(CORE_ID, id, "#E88FB3", { opacity: 0.9 });
    mind.agentActivity.set(id, performance.now() / 1000 + 30);
  },
  onTurn() {
    if (mind.core) { mind.core.flare(); }
  },
  onAlert() {
    if (mind.membrane) mind.membraneRipple.active = performance.now() / 1000;
  },
};

function handleLiveEvent(ev) {
  if (!ev || !ev.type) return;
  try {
    switch (ev.type) {
      case "memory_recalled": liveHandlers.onRecall(ev.node_ids); break;
      case "memory_written": liveHandlers.onWrite(ev.node_id, ev.kind); break;
      case "agent_dispatched": liveHandlers.onDispatch(ev.node_id); break;
      case "turn_complete": liveHandlers.onTurn(); break;
      case "alert": liveHandlers.onAlert(); break;
      default: break;
    }
  } catch (e) {
    console.warn("[mind] live event error:", e);
  }
}

/* ------------------------------------------------------------ ambient firing */
let nextSynapse = 0;
function ambientSynapses(nowS) {
  if (nowS < nextSynapse || fps.disabled) return;
  nextSynapse = nowS + 0.8 + hash01("syn" + Math.floor(nowS / 2)) * 1.2;
  if (!mind.edgeSystem) return;
  const sim = [...mind.edgeSystem.curveMap.values()].filter((r) => r.kind === "similarity");
  if (!sim.length) return;
  const rec = sim[Math.floor(hash01("pick" + Math.floor(nowS * 3)) * sim.length)];
  firePulse(rec.source, rec.target, "#A78BFA", { opacity: 0.28, scale: 0.35, duration: 0.7, swell: 1.2 });
}

/* ------------------------------------------------------------ bloom for new nodes */
function updateSceneBlooms(nowS) {
  const list = mind.sceneBloom || [];
  for (let i = list.length - 1; i >= 0; i--) {
    const b = list[i];
    const t = (nowS - b.t0) / 0.8;
    if (t >= 1) {
      const st = mind.standalone && mind.standalone.get(b.id);
      if (st) { st.mesh.scale.setScalar(st.target); st.aura.scale.setScalar(st.target * 4.2); }
      list.splice(i, 1);
      continue;
    }
    const st = mind.standalone && mind.standalone.get(b.id);
    if (st) {
      st.mesh.scale.setScalar(0.01 + t * st.target);
      st.aura.scale.setScalar((0.01 + t * st.target) * 4.2);
    }
  }
}

/* ------------------------------------------------------------ main loop */
let last = 0;
function frame(nowMs) {
  requestAnimationFrame(frame);
  if (document.hidden) return;          // skip the whole body when hidden
  const nowS = nowMs / 1000;
  const dt = last ? nowS - last : 0;
  last = nowS;
  uTime.value = nowS;

  governor(nowS, dt);
  idleDrift(nowS);

  if (mind.core) mind.core.update(nowS);
  for (const info of mind.instanced) updateInstancedFlares(info, nowS);

  // Agent activity: brighten the active agent's halo.
  if (mind.agentInfo && mind.agentInfo.flares) {
    for (const [id, until] of mind.agentActivity) {
      if (nowS > until) { mind.agentActivity.delete(id); continue; }
      flareNode(mind.agentInfo, id);
    }
  }

  // Membrane ripple on alert.
  if (mind.membraneRipple && mind.membraneRipple.active) {
    const t = nowS - mind.membraneRipple.active;
    if (t > 2) { mind.membraneRipple.active = 0; mind.membrane.material.uniforms.uRipple.value = 0; }
    else { mind.membrane.material.uniforms.uRipple.value = Math.sin(Math.PI * t / 2); }
  }

  if (!fps.disabled) {
    updatePulses(nowS);
    updateDrift(nowS);
    ambientSynapses(nowS);
  }
  updateSceneBlooms(nowS);

  if (mind.inspectorTick) mind.inspectorTick(nowS);

  mind.controls.update();
  mind.composer.render();
  mind.labelRenderer.render(mind.scene, mind.camera);
}

/* ------------------------------------------------------------ boot */
async function boot() {
  setupRenderer();
  buildStarfield();
  buildMembrane();
  makePulsePool();

  let skeleton;
  try {
    skeleton = await loadSkeleton();   // rethrows on failure — fail visibly
  } catch (e) {
    setStats("failed to load mind — is the server up? (" + e.message + ")", true);
    throw e;
  }

  placeRegions(skeleton);
  buildRegionGroups(skeleton);
  buildEdges();
  buildLabels(skeleton);
  buildAmbientDrift();

  connectObserver(handleLiveEvent);
  mind.liveHandlers = liveHandlers;
  mind.getNode = getNode;
  mind.getEdges = getEdges;
  mind.allNodes = allNodes;
  mind.hasNode = hasNode;
  mind.getSkeleton = getSkeleton;
  mind.CORE_ID = CORE_ID;
  mind.firePulse = firePulse;
  mind.firePulsePos = firePulsePos;

  requestAnimationFrame(frame);

  // Load interaction LAST via dynamic import — no static import cycle.
  try {
    const { initInspector } = await import("./inspector.js");
    initInspector(mind);
  } catch (e) {
    console.error("[mind] inspector failed to load:", e);
  }

  console.info(`[mind] live — ${mind.positions.size} nodes placed; drive animations via liveHandlers`);
}

boot().catch((e) => console.error("[mind] boot failed:", e));
