/* data.js — fetching + the live observer socket.
 *
 * The skeleton is fetched once; node detail is lazy (one request per
 * inspection). The observer WebSocket is a spectator: it reconnects with
 * exponential backoff (1s doubling to a 15s cap) and is closed entirely
 * while the tab is hidden, so a hidden page never queues a stale burst to
 * replay at you.
 */

const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 15000;

let skeleton = null;           // the full skeleton once loaded
let nodeIndex = new Map();     // id -> node
let edgeIndex = [];            // all edges

const statsEl = () => document.getElementById("stats");

/** Fetch the skeleton. On failure, write a visible error and rethrow —
 *  a silent black page is the worst possible outcome for this page. */
export async function loadSkeleton() {
  const res = await fetch("/api/mind-map", { headers: { Accept: "application/json" } });
  if (!res.ok) {
    setStats("failed to load mind — is the server up? (HTTP " + res.status + ")", true);
    throw new Error("mind-map fetch failed: HTTP " + res.status);
  }
  skeleton = await res.json();
  nodeIndex = new Map();
  for (const n of skeleton.nodes || []) nodeIndex.set(n.id, n);
  edgeIndex = skeleton.edges || [];
  updateStatsLine();
  return skeleton;
}

export function getSkeleton() { return skeleton; }
export function getNode(id) { return nodeIndex.get(id) || null; }
export function hasNode(id) { return nodeIndex.has(id); }
export function getEdges() { return edgeIndex; }
export function allNodes() { return [...nodeIndex.values()]; }
export function registerNode(node) { nodeIndex.set(node.id, node); }

export function updateStatsLine() {
  if (!skeleton) return;
  const s = skeleton.stats || {};
  const n = (skeleton.nodes || []).length;
  const m = (skeleton.edges || []).length;
  const mem = s.memory_shown !== undefined ? ` · memories ${s.memory_shown}/${s.memory_total}` : "";
  const errs = Object.entries(s.sources || {}).filter(([, v]) => v === "error");
  const err = errs.length ? ` · ⚠ ${errs.map(([k]) => k).join(", ")} errored` : "";
  setStats(`${n} nodes · ${m} edges${mem}${err}`);
}

export function setStats(text, isError = false) {
  const el = statsEl();
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("err", !!isError);
}

/** Lazy full detail for one node id. */
export async function fetchNodeDetail(id) {
  const res = await fetch("/api/mind-map/node/" + encodeURIComponent(id), {
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("detail fetch failed: HTTP " + res.status);
  return res.json();
}

/* ------------------------------------------------------------ observer WS */
let socket = null;
let retryMs = RETRY_BASE_MS;
let retryTimer = null;
let visible = !document.hidden;
let onEvent = null;

export function connectObserver(handler) {
  onEvent = handler;
  document.addEventListener("visibilitychange", () => {
    visible = !document.hidden;
    if (!visible) {
      closeSocket();           // render loop paused — drop the socket entirely
    } else if (!socket && onEvent) {
      retryMs = RETRY_BASE_MS; // fresh look on return, no stale replay
      openSocket();
    }
  });
  if (visible) openSocket();
}

function openSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${proto}//${location.host}/ws/observe`);
  socket.onmessage = (ev) => {
    try { onEvent && onEvent(JSON.parse(ev.data)); }
    catch (_) { /* malformed event — drop silently, never fake */ }
  };
  socket.onclose = () => {
    socket = null;
    if (visible && onEvent) {
      retryTimer = setTimeout(openSocket, retryMs);
      retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
    }
  };
  socket.onerror = () => { try { socket.close(); } catch (_) {} };
}

function closeSocket() {
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  if (socket) {
    try { socket.onclose = null; socket.close(); } catch (_) {}
    socket = null;
  }
}

/** Console hook to test live animations without real activity. */
export function testEvent(event) { onEvent && onEvent(event); }
