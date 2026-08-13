/* regions.js — pure layout math. No three.js scene dependency.
 *
 * Every region is positioned relative to a fixed ANCHOR. Layouts are
 * baked once at load and never recomputed per frame. The memory web runs
 * a real force-directed simulation (~150 iterations) seeded from a
 * deterministic id hash, so a reload lands on the same shape.
 */

export const ANCHORS = {
  memory:    { x: -10.0, y:  0.5, z: -4.0 },
  working:   { x:  9.5,  y:  2.5, z: -6.0 },
  agents:    { x: -5.0,  y:  6.0, z:  8.5 },
  knowledge: { x:  6.5,  y: -4.0, z:  9.0 },
  rim:       { x: 10.5,  y: -2.5, z:  2.5 },
};

/** Deterministic FNV-1a hash → float in [0, 1). Stable across reloads. */
export function hash01(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return (h >>> 0) / 4294967296;
}

const V = { x: 0, y: 0, z: 0 };
function sub(a, b, out = V) { out.x = a.x - b.x; out.y = a.y - b.y; out.z = a.z - b.z; return out; }
function add(a, b, out = V) { out.x = a.x + b.x; out.y = a.y + b.y; out.z = a.z + b.z; return out; }
function scale(a, s, out = V) { out.x = a.x * s; out.y = a.y * s; out.z = a.z * s; return out; }
function len(a) { return Math.hypot(a.x, a.y, a.z); }

/** Force-directed memory web — precomputed once, never per frame. */
export function layoutMemory(nodes, edges, anchor) {
  const positions = new Map();
  for (const n of nodes) {
    const h = hash01(n.id);
    positions.set(n.id, {
      x: anchor.x + (h - 0.5) * 18,
      y: anchor.y + (hash01(n.id + ":y") - 0.5) * 14,
      z: anchor.z + (hash01(n.id + ":z") - 0.5) * 12,
    });
  }
  const vel = new Map();
  for (const id of positions.keys()) vel.set(id, { x: 0, y: 0, z: 0 });

  const simEdges = edges.filter((e) => e.kind === "similarity")
    .filter((e) => positions.has(e.source) && positions.has(e.target));

  const ITER = 150, KR = 6.0, KS = 0.055, REST = 2.4, KG = 0.035, DAMP = 0.82, MIN_D = 0.8;
  const ids = [...positions.keys()];

  for (let it = 0; it < ITER; it++) {
    for (const id of ids) {
      const p = positions.get(id);
      // gravity toward the anchor
      vel.get(id).x += (anchor.x - p.x) * KG;
      vel.get(id).y += (anchor.y - p.y) * KG;
      vel.get(id).z += (anchor.z - p.z) * KG;
    }
    for (let a = 0; a < ids.length; a++) {
      for (let b = a + 1; b < ids.length; b++) {
        const pa = positions.get(ids[a]), pb = positions.get(ids[b]);
        sub(pb, pa, V);
        let d = len(V) || MIN_D;
        d = Math.max(d, MIN_D);
        const f = KR / (d * d);
        const fx = (V.x / d) * f, fy = (V.y / d) * f, fz = (V.z / d) * f;
        vel.get(ids[a]).x -= fx; vel.get(ids[a]).y -= fy; vel.get(ids[a]).z -= fz;
        vel.get(ids[b]).x += fx; vel.get(ids[b]).y += fy; vel.get(ids[b]).z += fz;
      }
    }
    for (const e of simEdges) {
      const pa = positions.get(e.source), pb = positions.get(e.target);
      sub(pb, pa, V);
      const d = len(V) || MIN_D;
      const f = KS * (d - REST) * e.weight;
      const fx = (V.x / d) * f, fy = (V.y / d) * f, fz = (V.z / d) * f;
      vel.get(e.source).x += fx; vel.get(e.source).y += fy; vel.get(e.source).z += fz;
      vel.get(e.target).x -= fx; vel.get(e.target).y -= fy; vel.get(e.target).z -= fz;
    }
    for (const id of ids) {
      const v = vel.get(id), p = positions.get(id);
      v.x *= DAMP; v.y *= DAMP; v.z *= DAMP;
      p.x += v.x; p.y += v.y; p.z += v.z;
    }
  }

  // Normalize the cluster to a readable radius around the anchor.
  let maxD = 0;
  for (const id of ids) {
    const p = positions.get(id);
    maxD = Math.max(maxD, len(sub(p, anchor)));
  }
  if (maxD > 0) {
    const target = 7.2;
    const k = target / maxD;
    for (const id of ids) {
      const p = positions.get(id);
      positions.set(id, {
        x: anchor.x + (p.x - anchor.x) * k,
        y: anchor.y + (p.y - anchor.y) * k,
        z: anchor.z + (p.z - anchor.z) * k,
      });
    }
  }
  return positions;
}

/** Working — an even ring around its anchor with a slight vertical wobble. */
export function layoutWorking(nodes, anchor) {
  const out = new Map();
  const n = nodes.length || 1;
  nodes.forEach((node, i) => {
    const a = (i / n) * Math.PI * 2;
    out.set(node.id, {
      x: anchor.x + Math.cos(a) * 2.6,
      y: anchor.y + Math.sin(a * 2) * 0.7,
      z: anchor.z + Math.sin(a) * 2.6,
    });
  });
  return out;
}

/** Agents — a vertical arc, a column of presences off to one side. */
export function layoutAgents(nodes, edges, anchor) {
  const out = new Map();
  const n = nodes.length || 1;
  nodes.forEach((node, i) => {
    const t = n === 1 ? 0.5 : i / (n - 1);
    const a = -1.1 + t * 2.2;
    out.set(node.id, {
      x: anchor.x + Math.sin(a) * 4.4,
      y: anchor.y + (t - 0.5) * 6.2,
      z: anchor.z + Math.cos(a) * 4.4,
    });
  });
  return out;
}

/** Knowledge — a flat grid, ~ceil(sqrt(n)) columns. */
export function layoutKnowledge(nodes, anchor) {
  const out = new Map();
  const cols = Math.max(1, Math.ceil(Math.sqrt(nodes.length || 1)));
  const spacing = 1.9;
  nodes.forEach((node, i) => {
    const c = i % cols, r = Math.floor(i / cols);
    out.set(node.id, {
      x: anchor.x + (c - (cols - 1) / 2) * spacing,
      y: anchor.y,
      z: anchor.z + (r - (nodes.length - 1) / cols / 2) * spacing,
    });
  });
  return out;
}

/** Rim — a compact capability ball. Categories sit on an inner sphere via
 *  golden-angle distribution; tools bunch around their category like grapes. */
export function layoutRim(nodes, anchor) {
  const out = new Map();
  const categories = [...new Set(nodes.map((n) => n.extra && n.extra.category || "misc"))];
  const GA = Math.PI * (3 - Math.sqrt(5));

  const catAnchor = new Map();
  categories.forEach((cat, i) => {
    const t = categories.length === 1 ? 0 : i / (categories.length - 1);
    const y = 1 - 2 * t;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = GA * i;
    catAnchor.set(cat, {
      x: anchor.x + r * Math.cos(phi) * 3.4,
      y: anchor.y + y * 3.4,
      z: anchor.z + r * Math.sin(phi) * 3.4,
    });
  });

  const byCat = new Map();
  for (const n of nodes) {
    const cat = n.extra && n.extra.category || "misc";
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat).push(n);
  }
  for (const [cat, list] of byCat) {
    const c = catAnchor.get(cat);
    list.forEach((node, i) => {
      const a = (i / (list.length || 1)) * Math.PI * 2;
      const rr = 1.1;
      out.set(node.id, {
        x: c.x + Math.cos(a) * rr * (0.6 + 0.4 * hash01(node.id)),
        y: c.y + Math.sin(a) * rr * (0.6 + 0.4 * hash01(node.id + ":y")),
        z: c.z + (hash01(node.id + ":z") - 0.5) * 0.8,
      });
    });
  }
  return out;
}

/** Dispatch to the right layout for a region. Returns Map id -> {x,y,z}. */
export function layoutRegion(region, nodes, edges, anchor) {
  switch (region) {
    case "memory": return layoutMemory(nodes, edges, anchor);
    case "working": return layoutWorking(nodes, anchor);
    case "agents": return layoutAgents(nodes, edges, anchor);
    case "knowledge": return layoutKnowledge(nodes, anchor);
    case "rim": return layoutRim(nodes, anchor);
    default: {
      const out = new Map();
      nodes.forEach((n, i) => out.set(n.id, {
        x: anchor.x + (hash01(n.id) - 0.5) * 3,
        y: anchor.y + (hash01(n.id + ":y") - 0.5) * 3,
        z: anchor.z + (hash01(n.id + ":z") - 0.5) * 3,
      }));
      return out;
    }
  }
}
