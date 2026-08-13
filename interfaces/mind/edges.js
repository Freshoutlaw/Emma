/* edges.js — curves and pulses. Every edge is an arced QuadraticBezier
 * (control point = midpoint pushed ~12% of the chord along a perpendicular
 * of the outward-from-origin direction), merged per kind into ONE
 * LineSegments so the whole nervous system is ~6 draw calls, not hundreds.
 *
 * Two shader treatments: the similarity web shimmers on a per-edge phase;
 * the trunks (dispatch / recall / tools / knowledge) carry comets whose
 * direction shows the direction of thought — out to agents and tools,
 * in from memory.
 */

import * as THREE from "three";
import { hash01 } from "./regions.js";

export const EDGE_COLORS = {
  similarity: "#A78BFA",
  recall: "#A78BFA",
  dispatch: "#E88FB3",
  tools: "#8B93A1",
  knowledge: "#F5A524",
  recent: "#67E8F9",
};

export const EDGE_BASE_OPACITY = {
  similarity: 0.18, recall: 0.20, dispatch: 0.16, tools: 0.13, knowledge: 0.17, recent: 0.13,
};

// Flow kinds carry comets. uSpeed sign sets direction (positive = along the
// edge's source→target). Dispatch/tools/knowledge stream OUT from the core;
// recall is drawn memory→core so it streams IN.
export const EDGE_FLOW = {
  dispatch: 0.5, tools: 0.4, knowledge: 0.35, recall: 0.5, recent: 0.25,
};

// Lossy but honest edge-kind → region mapping for the legend toggle.
export const EDGE_REGION = {
  similarity: "memory", recall: "memory",
  dispatch: "agents", tools: "agents",
  knowledge: "knowledge", recent: "working",
};

const SEGMENTS = 24;
const _v0 = new THREE.Vector3(), _v1 = new THREE.Vector3();
const _chord = new THREE.Vector3(), _perp = new THREE.Vector3(), _mid = new THREE.Vector3(), _out = new THREE.Vector3();

/* ------------------------------------------------------------ shimmer shader */
const SHIMMER_VERT = /* glsl */ `
  attribute float aPhase;
  varying float vPhase;
  void main() {
    vPhase = aPhase;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const SHIMMER_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uTime;
  uniform float uShimmer;
  varying float vPhase;
  void main() {
    float breathe = 0.7 + 0.3 * sin(uTime * 1.6 + vPhase);
    gl_FragColor = vec4(uColor, uOpacity * breathe * uShimmer);
  }
`;

/* -------------------------------------------------------------- flow shader */
const FLOW_VERT = /* glsl */ `
  attribute float aPhase;
  attribute float aT;
  attribute float aWeight;
  varying float vPhase;
  varying float vT;
  varying float vWeight;
  void main() {
    vPhase = aPhase; vT = aT; vWeight = aWeight;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const FLOW_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uTime;
  uniform float uSpeed;
  uniform float uFlow;
  varying float vPhase;
  varying float vT;
  varying float vWeight;
  void main() {
    float base = uOpacity * 0.6 * vWeight;
    float head = fract(uTime * uSpeed + vPhase);
    float d = fract(head - vT);
    float band = exp(-d * 9.0);
    vec3 col = mix(uColor, vec3(1.0), band * 0.85);
    float alpha = base + band * 0.9 * uFlow;
    gl_FragColor = vec4(col, alpha);
  }
`;

/* --------------------------------------------------------------- materials */
function opacityShim(mat) {
  Object.defineProperty(mat, "opacity", {
    get() { return this.uniforms.uOpacity.value; },
    set(v) { this.uniforms.uOpacity.value = v; },
  });
  return mat;
}

function makeShimmerMaterial(uTime) {
  return opacityShim(new THREE.ShaderMaterial({
    uniforms: { uColor: { value: new THREE.Color(EDGE_COLORS.similarity) }, uOpacity: { value: EDGE_BASE_OPACITY.similarity }, uTime, uShimmer: { value: 1.0 } },
    vertexShader: SHIMMER_VERT, fragmentShader: SHIMMER_FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
}

function makeFlowMaterial(kind, uTime) {
  return opacityShim(new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(EDGE_COLORS[kind] || "#8B93A1") },
      uOpacity: { value: EDGE_BASE_OPACITY[kind] || 0.15 },
      uTime, uSpeed: { value: EDGE_FLOW[kind] || 0.3 }, uFlow: { value: 1.0 },
    },
    vertexShader: FLOW_VERT, fragmentShader: FLOW_FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
}

/* -------------------------------------------------------------- the system */
export function buildEdgeSystem(edges, positions, uTime) {
  const byKind = new Map();           // kind -> edge records
  const curveMap = new Map();         // "src|tgt" and "tgt|src" -> record
  const nodeEdges = new Map();        // node id -> records touching it

  for (const e of edges) {
    const p0 = positions.get(e.source), p1 = positions.get(e.target);
    if (!p0 || !p1) continue;          // never draw what isn't true
    _v0.set(p0.x, p0.y, p0.z);
    _v1.set(p1.x, p1.y, p1.z);
    _chord.subVectors(_v1, _v0);
    const chordLen = _chord.length() || 0.001;
    _mid.addVectors(_v0, _v1).multiplyScalar(0.5);
    _out.copy(_mid).normalize();
    _perp.crossVectors(_chord, _out).normalize();
    const control = _mid.clone().addScaledVector(_perp, chordLen * 0.12);
    const curve = new THREE.QuadraticBezierCurve3(_v0.clone(), control, _v1.clone());

    const key = e.source + "|" + e.target;
    const record = { source: e.source, target: e.target, kind: e.kind, weight: e.weight || 0.5, curve };
    if (!byKind.has(e.kind)) byKind.set(e.kind, []);
    byKind.get(e.kind).push(record);
    curveMap.set(key, record);
    curveMap.set(e.target + "|" + e.source, record);
    for (const id of [e.source, e.target]) {
      if (!nodeEdges.has(id)) nodeEdges.set(id, []);
      nodeEdges.get(id).push(record);
    }
  }

  const lines = [];   // { kind, object, material, baseOpacity }
  for (const [kind, records] of byKind) {
    const isFlow = EDGE_FLOW[kind] !== undefined;
    const mat = isFlow ? makeFlowMaterial(kind, uTime) : makeShimmerMaterial(uTime);
    const positionsArr = [];
    const phases = [];
    const ts = [];
    const weights = [];

    for (const rec of records) {
      const pts = rec.curve.getPoints(SEGMENTS);
      const phase = hash01("edge:" + rec.source + "|" + rec.target);
      for (let s = 0; s < SEGMENTS; s++) {
        const a = pts[s], b = pts[s + 1];
        positionsArr.push(a.x, a.y, a.z, b.x, b.y, b.z);
        phases.push(phase, phase);
        const t = s / SEGMENTS;
        ts.push(t, t + 1 / SEGMENTS);
        weights.push(rec.weight, rec.weight);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positionsArr, 3));
    geo.setAttribute("aPhase", new THREE.Float32BufferAttribute(phases, 1));
    if (isFlow) {
      geo.setAttribute("aT", new THREE.Float32BufferAttribute(ts, 1));
      geo.setAttribute("aWeight", new THREE.Float32BufferAttribute(weights, 1));
    }
    const object = new THREE.LineSegments(geo, mat);
    object.frustumCulled = false;
    lines.push({ kind, object, material: mat, baseOpacity: EDGE_BASE_OPACITY[kind] || 0.15 });
  }

  return { lines, curveMap, nodeEdges };
}

/** All edge records touching a node (for hover highlight). */
export function edgesForNode(nodeEdges, id) {
  return nodeEdges.get(id) || [];
}
