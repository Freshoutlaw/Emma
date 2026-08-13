/* inspector.js — navigation and inspection. Loaded dynamically by
 * scene.js (dynamic import at the end of boot) so there is no static
 * import cycle.
 *
 * Every server-sourced string is inserted with textContent, never
 * innerHTML — memory bodies contain raw user text and this is a live
 * XSS surface.
 */

import * as THREE from "three";
import { fetchNodeDetail } from "./data.js";

const START_POS = new THREE.Vector3(4, 7, 30);
const START_TARGET = new THREE.Vector3(0, 0, 0);

export function initInspector(mind) {
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const dom = mind.renderer.domElement;

  const tooltip = document.getElementById("tooltip");
  const inspector = document.getElementById("inspector");
  const insBadge = document.getElementById("ins-badge");
  const insTitle = document.getElementById("ins-title");
  const insBody = document.getElementById("ins-body");
  const insClose = document.getElementById("ins-close");

  let hoverId = null;
  let hoverOverlay = null;
  let pressPos = null;
  let detailToken = 0;
  let focus = null;              // { nodeId, target, goal, dist } | null

  /* ------------------------------------------------ visibility helpers */
  function visibleUp(obj) {
    let o = obj;
    while (o) { if (o.visible === false) return false; o = o.parent; }
    return true;
  }

  function raycastTargets() {
    const targets = [];
    for (const info of mind.instanced) {
      if (visibleUp(info.mesh)) targets.push(info.mesh);
    }
    if (mind.core) {
      mind.core.group.traverse((o) => { if (o.isMesh) targets.push(o); });
    }
    if (mind.standalone) {
      for (const st of mind.standalone.values()) {
        if (visibleUp(st.mesh)) targets.push(st.mesh);
      }
    }
    return targets;
  }

  function nodeFromHit(hit) {
    if (hit.object.isInstancedMesh) {
      const info = hit.object.userData && hit.object.userData.ids;
      if (info && hit.instanceId != null) return info[hit.instanceId] || null;
      return null;
    }
    return hit.object.userData && hit.object.userData.id ? hit.object.userData.id : null;
  }

  /* ------------------------------------------------ tooltip + highlight */
  function showTooltip(clientX, clientY, nodeId) {
    const node = mind.getNode(nodeId);
    if (!node) { hideTooltip(); return; }
    tooltip.style.display = "block";
    tooltip.style.left = clientX + 14 + "px";
    tooltip.style.top = clientY + 14 + "px";
    const line = node.extra
      ? (node.extra.description || node.extra.category || node.extra.kind || node.extra.ts || "")
      : "";
    tooltip.innerHTML = "";
    const t = document.createElement("div"); t.className = "tt-title"; t.textContent = node.label;
    const r = document.createElement("div"); r.className = "tt-region"; r.textContent = node.region; r.style.color = node.color;
    const l = document.createElement("div"); l.className = "tt-line"; l.textContent = line;
    tooltip.appendChild(t); tooltip.appendChild(r); if (line) tooltip.appendChild(l);
  }
  function hideTooltip() { tooltip.style.display = "none"; }

  function dimBaseEdges() {
    for (const line of mind.edgeSystem.lines) line.material.opacity = 0.04;
  }
  function restoreBaseEdges() {
    for (const line of mind.edgeSystem.lines) line.material.opacity = line.baseOpacity;
  }
  function setHoverEdges(nodeId) {
    if (hoverOverlay) { mind.scene.remove(hoverOverlay); hoverOverlay.geometry.dispose(); hoverOverlay = null; }
    if (!nodeId) return;
    const recs = mind.edgeSystem.nodeEdges.get(nodeId);
    if (!recs || !recs.length) return;
    const positions = [];
    for (const rec of recs) {
      const pts = rec.curve.getPoints(24);
      for (let s = 0; s < 24; s++) {
        positions.push(pts[s].x, pts[s].y, pts[s].z, pts[s + 1].x, pts[s + 1].y, pts[s + 1].z);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false });
    hoverOverlay = new THREE.LineSegments(geo, mat);
    mind.scene.add(hoverOverlay);   // scene root, so region toggles never hide it
  }

  function onPointerMove(ev) {
    pointer.x = (ev.clientX / window.innerWidth) * 2 - 1;
    pointer.y = -(ev.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(pointer, mind.camera);
    const hits = raycaster.intersectObjects(raycastTargets(), false);
    const id = hits.length ? nodeFromHit(hits[0]) : null;
    if (id !== hoverId) {
      if (hoverId) restoreBaseEdges();
      hoverId = id;
      if (id) { dimBaseEdges(); setHoverEdges(id); showTooltip(ev.clientX, ev.clientY, id); }
      else { setHoverEdges(null); hideTooltip(); }
    } else if (id) {
      showTooltip(ev.clientX, ev.clientY, id);
    }
  }

  /* ------------------------------------------------ click vs drag + fly */
  function flyTo(node, dist) {
    const p = mind.positions.get(node.id);
    if (!p) return;
    mind.controls.autoRotate = false;
    focus = { nodeId: node.id, target: p.clone(), goal: null, dist: dist ?? 7 };
    try { history.replaceState(null, "", "#node=" + encodeURIComponent(node.id)); } catch (_) {}
  }

  function toOverview() {
    focus = { nodeId: null, target: START_TARGET.clone(), goal: START_POS.clone(), dist: 0 };
    closeInspector();
  }

  function updateFly() {
    if (!focus) return;
    const t = focus.target;
    if (focus.nodeId) {
      const p = mind.positions.get(focus.nodeId);
      if (!p) { focus = null; return; }
      t.copy(p);
    }
    const dir = mind.camera.position.clone().sub(t).normalize();
    const goal = focus.goal || t.clone().addScaledVector(dir, focus.dist);
    mind.camera.position.lerp(goal, 0.08);
    mind.controls.target.lerp(t, 0.08);
    mind.controls.update();
  }

  mind.inspectorTick = () => { updateFly(); };

  mind.controls.addEventListener("start", () => {
    if (pressPos === null) focus = null;   // user grabbed — cancel any glide
  });

  dom.addEventListener("pointerdown", (ev) => { pressPos = { x: ev.clientX, y: ev.clientY }; });
  dom.addEventListener("pointerup", (ev) => {
    if (!pressPos) return;
    const moved = Math.hypot(ev.clientX - pressPos.x, ev.clientY - pressPos.y);
    pressPos = null;
    if (moved > 6) return;                 // a drag, not a click
    pointer.x = (ev.clientX / window.innerWidth) * 2 - 1;
    pointer.y = -(ev.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(pointer, mind.camera);
    const hits = raycaster.intersectObjects(raycastTargets(), false);
    if (!hits.length) return;
    const id = nodeFromHit(hits[0]);
    const node = id && mind.getNode(id);
    if (node) flyTo(node);
  });

  dom.addEventListener("dblclick", () => toOverview());
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") toOverview();
  });

  /* ------------------------------------------------ inspector panel */
  function openInspector(nodeId) {
    const node = mind.getNode(nodeId);
    if (!node) return;
    inspector.classList.add("open");
    insBadge.style.color = node.color;
    insBadge.textContent = node.type || node.region;
    insTitle.textContent = node.label;
    insBody.innerHTML = "";
    const loading = document.createElement("div");
    loading.className = "loading";
    loading.textContent = "loading…";
    insBody.appendChild(loading);

    const token = ++detailToken;
    fetchNodeDetail(nodeId).then((detail) => {
      if (token !== detailToken) return;   // stale — clicked elsewhere
      renderDetail(detail, node);
    }).catch(() => {
      if (token !== detailToken) return;
      insBody.innerHTML = "";
      const err = document.createElement("div");
      err.textContent = "could not load detail.";
      insBody.appendChild(err);
    });
  }

  function renderDetail(detail, node) {
    insBody.innerHTML = "";
    const add = (el) => insBody.appendChild(el);
    const meta = document.createElement("div");
    meta.className = "meta";

    if (detail && detail.type === "memory") {
      meta.textContent = `${detail.kind || "episode"} · ${(detail.ts || "").slice(0, 16)} · freshness ${(detail.freshness || 0).toFixed(2)}`;
      add(meta);
      const f = document.createElement("div"); f.className = "field"; f.textContent = "Memory";
      add(f);
      const pre = document.createElement("pre"); pre.textContent = detail.body || "(empty)";
      add(pre);
      if (detail.neighbors && detail.neighbors.length) {
        const nf = document.createElement("div"); nf.className = "field"; nf.textContent = "Nearest neighbors (live similarity)";
        add(nf);
        for (const nb of detail.neighbors) {
          const a = document.createElement("a");
          a.textContent = nb.label;
          const sim = document.createElement("span"); sim.className = "sim"; sim.textContent = `  ${(nb.score || 0).toFixed(3)}`;
          a.appendChild(sim);
          a.addEventListener("click", () => {
            const target = mind.getNode(nb.id);
            if (target) flyTo(target);
            else openInspector(nb.id);   // unknown id — try detail anyway
          });
          add(a);
        }
      }
    } else if (detail && detail.type === "agent") {
      meta.textContent = `agent · model ${detail.model || "router default"}`;
      add(meta);
      const d = document.createElement("div"); d.className = "field"; d.textContent = "Specialty";
      add(d);
      const p = document.createElement("pre"); p.textContent = detail.description || "";
      add(p);
      if (detail.tools && detail.tools.length) {
        const tf = document.createElement("div"); tf.className = "field"; tf.textContent = "Tools (" + detail.tools.length + ")";
        add(tf);
        const tp = document.createElement("pre"); tp.textContent = detail.tools.join(", ");
        add(tp);
      } else {
        const tf = document.createElement("div"); tf.className = "field"; tf.textContent = "Tools";
        add(tf);
        const tp = document.createElement("pre"); tp.textContent = "full catalog";
        add(tp);
      }
    } else if (detail && detail.type === "tool") {
      meta.textContent = `tool · category ${detail.category || ""}`;
      add(meta);
      const d = document.createElement("div"); d.className = "field"; d.textContent = "Description";
      add(d);
      const p = document.createElement("pre"); p.textContent = detail.description || "";
      add(p);
      const af = document.createElement("div"); af.className = "field"; af.textContent = "Arguments";
      add(af);
      const ap = document.createElement("pre"); ap.textContent = JSON.stringify(detail.args || {}, null, 2);
      add(ap);
    } else if (detail && detail.type === "knowledge") {
      meta.textContent = `knowledge · ${detail.path || ""}`;
      add(meta);
      const d = document.createElement("div"); d.className = "field"; d.textContent = "Preview (always-loaded)";
      add(d);
      const p = document.createElement("pre"); p.textContent = detail.preview || "";
      add(p);
    } else if (detail && detail.type === "thread") {
      meta.textContent = `conversation · ${(detail.ts || "").slice(0, 16)}`;
      add(meta);
      const p = document.createElement("pre"); p.textContent = detail.body || "";
      add(p);
    } else if (detail && detail.type === "core") {
      meta.textContent = "the agent itself";
      add(meta);
      const p = document.createElement("pre"); p.textContent = detail.description || "";
      add(p);
    } else {
      const p = document.createElement("pre"); p.textContent = JSON.stringify(detail || node, null, 2);
      add(p);
    }
  }

  insClose.addEventListener("click", closeInspector);
  function closeInspector() { inspector.classList.remove("open"); }

  /* ------------------------------------------------ search */
  const searchInput = document.getElementById("search-input");
  searchInput.addEventListener("keydown", (ev) => {
    ev.stopPropagation();                // typing must never drive the scene
    if (ev.key === "Enter") {
      ev.preventDefault();
      const q = searchInput.value.trim().toLowerCase();
      if (!q) return;
      const ranked = [];
      for (const n of mind.allNodes()) {
        const label = (n.label || "").toLowerCase();
        const id = (n.id || "").toLowerCase();
        if (label.includes(q) || id.includes(q)) {
          const score = (label.startsWith(q) || id.startsWith(q) ? 0 : 1) * 100 + label.length;
          ranked.push({ node: n, score });
        }
      }
      ranked.sort((a, b) => a.score - b.score);
      if (ranked.length) flyTo(ranked[0].node);
    }
  });

  /* ------------------------------------------------ legend toggles */
  const legend = document.getElementById("legend");
  legend.innerHTML = "";
  const skeleton = mind.getSkeleton ? mind.getSkeleton() : null;
  const regions = (skeleton && skeleton.regions) ? skeleton.regions : [
    { id: "core", label: "core", color: "#2DD4A8" }, { id: "memory", label: "memory", color: "#A78BFA" },
    { id: "working", label: "working", color: "#67E8F9" }, { id: "agents", label: "agents", color: "#E88FB3" },
    { id: "knowledge", label: "knowledge", color: "#F5A524" }, { id: "rim", label: "rim", color: "#8B93A1" },
  ];
  for (const r of regions) {
    const chip = document.createElement("div");
    chip.className = "chip";
    const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = r.color || "#fff";
    const name = document.createElement("span"); name.textContent = r.label || r.id;
    chip.appendChild(sw); chip.appendChild(name);
    chip.addEventListener("click", () => {
      const group = mind.regionGroups.get(r.id);
      if (!group) return;
      group.visible = !group.visible;
      chip.classList.toggle("off", !group.visible);
    });
    legend.appendChild(chip);
  }
  // Known limitation, stated honestly: edge-kind → region mapping is lossy
  // (an edge between two regions has to pick one), so a few edges survive a
  // toggle. Nodes, halos, labels and most edges follow the group.

  /* ------------------------------------------------ deep links */
  const m = location.hash.match(/#node=([^&]+)/);
  if (m) {
    const node = mind.getNode(decodeURIComponent(m[1]));
    if (node) setTimeout(() => flyTo(node), 300);
  }

  dom.addEventListener("pointermove", onPointerMove);
  window.addEventListener("resize", () => {});
}
