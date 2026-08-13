/* Emma HUD — orb-first, voice-first. The core is the only thing on screen;
   panels (memory / status / guardian) appear only when Emma or the operator
   summons them. */

"use strict";

const $ = (id) => document.getElementById(id);
const terminal = $("terminal");
const orb = $("core-orb");
let statusData = null;        // cached /api/system/status
let displayPanel = null;      // currently shown overlay
let displayPayload = null;    // payload of the current display (e.g. map region)
let activeLine = null;        // streaming reply line
let streamedTokens = false;   // did the current reply stream tokens?
let streaming = false;        // a chat stream is in flight
let seenMemory = new Set();
let killEngaged = false;
let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;
let silenceTimer = null;
let recordingTimeout = null;
let isTextInputVisible = false;
let micEnabled = true;          // user hasn't muted via the 🎙 button
let lastNothingHeard = 0;        // throttle for the always-on silence loop
let silenceAudioContext = null;  // closed between cycles (always-on leaks contexts)
let recordedSpeech = false;      // did this recording contain real audio? (skip silent ones)
let attachedImage = null;        // { name, dataUrl } — image the user wants Emma to describe
let liveWatching = false;        // live-watch mode: Emma re-analyzes the image and reports changes
let liveAbort = null;            // AbortController for the live SSE stream
let liveReconnects = 0;          // consecutive auto-reconnects after an unexpected drop

// ---------------------------------------------------------------- audio queue
// Pipelined speak path: the server emits speak_segment events as each
// sentence is generated.  The client queues them and plays them in order
// via sequential <audio> element swaps — the first segment's audio arrives
// while the LLM is still generating later sentences.
let audioQueue = [];
let currentlyPlaying = false;
let activeBaseTurnId = null;
let lastSegmentWasFinal = false;
let currentFetchAbort = null;
const audioEl = new Audio();
audioEl.preload = "auto";

// Browsers block audio with sound until the user has interacted with the
// page. Emma often speaks without a click (auto-listening), so prime the
// element inside the first real gesture — after that, play() is allowed.
let audioUnlocked = false;
function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  try {
    audioEl.muted = true;
    const p = audioEl.play();
    if (p && typeof p.catch === "function") {
      p.then(() => { audioEl.pause(); audioEl.currentTime = 0; audioEl.muted = false; })
       .catch(() => { audioEl.muted = false; });
    } else {
      audioEl.muted = false;
    }
  } catch (_) { audioEl.muted = false; }
}
document.addEventListener("pointerdown", unlockAudio, { once: true });
document.addEventListener("keydown", unlockAudio, { once: true });
document.addEventListener("touchstart", unlockAudio, { once: true });

// Drag-and-drop image attachment — the whole HUD is a drop target.
document.addEventListener("dragenter", onDragEnter);
document.addEventListener("dragover", onDragOver);
document.addEventListener("dragleave", onDragLeave);
document.addEventListener("drop", onDrop);

// ---------------------------------------------------------------- helpers
function addLine(text, cls) {
  const line = document.createElement("div");
  line.className = "terminal-line" + (cls ? " " + cls : "");
  line.textContent = text;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
  return line;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { const body = await res.json(); detail = body.detail || JSON.stringify(body); } catch (e) { /* ignore */ }
    throw { status: res.status, detail };
  }
  return res.json();
}

// ---------------------------------------------------------------- Emma-controlled content
function showEmmaContent(title, content, contentType = "html") {
  const contentEl = $("emma-content");
  const titleEl = $("emma-content-title");
  const bodyEl = $("emma-content-body");
  
  if (!contentEl || !titleEl || !bodyEl) return;
  
  titleEl.textContent = title;
  
  if (contentType === "html") {
    bodyEl.innerHTML = content;
  } else {
    bodyEl.textContent = content;
  }
  
  contentEl.classList.remove("hidden");
}

function hideEmmaContent() {
  const contentEl = $("emma-content");
  if (contentEl) contentEl.classList.add("hidden");
}

// ---------------------------------------------------------------- panels (legacy - functions kept for compatibility)
function mapFrameSrc(payload) {
  const r = (payload && payload.region) || {};
  const p = new URLSearchParams();
  if (r.name) p.set("region", r.name);
  if (r.country) p.set("country", r.country);
  if (r.code) p.set("code", r.code);
  if (r.lat != null) p.set("lat", r.lat);
  if (r.lon != null) p.set("lon", r.lon);
  if (r.tz) p.set("tz", r.tz);
  if (r.pop_m != null) p.set("pop", r.pop_m);
  if (r.resolved != null) p.set("resolved", r.resolved ? "1" : "0");
  if (r.source) p.set("source", r.source);
  const q = p.toString();
  return "map.html" + (q ? "?" + q : "");
}

function showPanel(name, reason, payload) {
  // Convert old panel calls to Emma-controlled content
  hideAllPanels();
  
  let title = "";
  let content = "";
  let contentType = "html";
  
  switch (name) {
    case "memory":
      title = "EPISODIC MEMORY";
      content = "<div id='memory-stream'></div>";
      break;
    case "status":
      title = "CORE STATUS";
      content = generateStatusContent();
      break;
    case "guardian":
      title = "CYBER GUARDIAN";
      content = generateGuardianContent();
      break;
    case "map":
      title = "REGION INTELLIGENCE";
      content = `<iframe id="map-frame" src="${mapFrameSrc(payload)}" style="width:100%;height:400px;border:none;"></iframe>`;
      break;
    case "board":
      title = "BOARD OF ADVISORS";
      content = renderBoardPanel(payload);
      break;
    default:
      return;
  }
  
  if (reason) title += ` — ${reason}`;
  
  showEmmaContent(title, content, contentType);
  
  // Load dynamic content if needed
  if (name === "memory") {
    setTimeout(refreshMemoryPanel, 100);
  }
  
  displayPanel = name;
  displayPayload = payload || null;
}

function hideAllPanels() {
  hideEmmaContent();
  displayPanel = null;
  displayPayload = null;
}

function supabaseStatusLabel(s) {
  if (!s.configured) return "[ DISABLED ]";
  if (!s.reachable) return "[ UNREACHABLE ]";
  if (s.schema === "ok") return "[ SYNCED ]";
  if (s.schema === "missing") return "[ SCHEMA MISSING ]";
  return "[ UNKNOWN ]";
}

// ---------------------------------------------------------------- board panel
// The board of advisors renders from the display payload pushed with the
// meeting result — question, opinions with their citation snapshots, the
// chair's synthesis, and the measured cost. Stored citations render from
// their meeting-time snapshot, not the current dossier.
function escapeAttr(s) {
  return escapeHtml(String(s ?? "")).replace(/"/g, "&quot;");
}

function renderBoardPanel(payload) {
  if (!payload) return "<p>No meeting payload.</p>";
  const parts = [];
  parts.push(`<div class="board-q">${escapeHtml(payload.question)}</div>`);
  const verdict = payload.verdict || {};
  if (verdict.spoken_summary) {
    parts.push(`<div class="board-verdict ${verdict.unanimous ? "" : "board-split"}">`);
    parts.push(`<span class="board-tag">${verdict.unanimous ? "UNANIMOUS" : "SPLIT"}</span> ${escapeHtml(verdict.spoken_summary)}</div>`);
  }
  if ((verdict.discount_notes || []).length) {
    parts.push(`<div class="board-meta">Discounts: ${escapeHtml(verdict.discount_notes.join(" · "))}</div>`);
  }
  if ((verdict.flags || []).length) {
    parts.push(`<div class="board-meta board-flag">Flags: ${escapeHtml(verdict.flags.join(" · "))}</div>`);
  }
  if (verdict.synthesis) {
    parts.push(`<div class="board-synthesis">${escapeHtml(verdict.synthesis).replace(/\n/g, "<br>")}</div>`);
  }
  for (const o of payload.opinions || []) {
    parts.push(`<div class="board-seat">`);
    parts.push(`<div class="board-seat-head">${escapeHtml(o.seat_name)}`);
    if (o.abstain) parts.push(`<span class="board-tag">ABSTAINED</span>`);
    if (o.unsourced) parts.push(`<span class="board-tag">NO CITATIONS</span>`);
    if (o.error) parts.push(`<span class="board-tag board-err">ERROR</span>`);
    parts.push(`<span class="board-conf">${(o.confidence * 100).toFixed(0)}%</span></div>`);
    if (o.position) parts.push(`<div class="board-position">${escapeHtml(o.position)}</div>`);
    if (o.reasoning) parts.push(`<div class="board-reasoning">${escapeHtml(o.reasoning)}</div>`);
    if (o.citations && o.citations.length) {
      const srcs = o.citation_sources || {};
      const cites = o.citations.map((c) => {
        const s = srcs[c] || {};
        return `<span class="board-cite" title="${escapeAttr(s.source || "")}">${escapeHtml(c)}${s.title ? " — " + escapeHtml(s.title) : ""}</span>`;
      }).join(" ");
      parts.push(`<div class="board-cites">${cites}</div>`);
    }
    if (o.citations_rejected && o.citations_rejected.length) {
      parts.push(`<div class="board-meta board-flag">stripped: ${escapeHtml(o.citations_rejected.join(", "))}</div>`);
    }
    if (o.would_change_mind) {
      parts.push(`<div class="board-meta">Would change mind if: ${escapeHtml(o.would_change_mind)}</div>`);
    }
    if (o.error) parts.push(`<div class="board-meta board-err">${escapeHtml(o.error)}</div>`);
    parts.push(`</div>`);
  }
  parts.push(`<div class="board-meta">Meeting ${escapeHtml(payload.meeting_id)} · cost $${Number(payload.cost_usd).toFixed(4)} · ${payload.prompted ? "you asked" : "standing review"}</div>`);
  return parts.join("");
}

function generateStatusContent() {
  if (!statusData) return "<p>Loading status...</p>";
  
  return `
    <div class="security-status">
      <div class="status-row"><span>LLM Route</span><span class="highlight">${(statusData.llm.route || "none").toUpperCase()}</span></div>
      <div class="status-row"><span>LLM Model</span><span class="highlight">${statusData.llm.model || "—"}</span></div>
      <div class="status-row"><span>Ollama</span><span class="highlight">${statusData.services.ollama ? "[ ONLINE ]" : "[ OFFLINE ]"}</span></div>
      <div class="status-row"><span>Groq</span><span class="highlight">${statusData.services.groq ? "[ ONLINE ]" : "[ OFFLINE ]"}</span></div>
      <div class="status-row"><span>MQTT Broker</span><span class="highlight">${statusData.services.mqtt.connected ? "[ CONNECTED ]" : "[ OFFLINE ]"}</span></div>
      <div class="status-row"><span>Supabase</span><span class="highlight ${statusData.services.supabase.schema === "missing" ? "alert" : ""}">${supabaseStatusLabel(statusData.services.supabase)}</span></div>
      <div class="status-row"><span>Memory Episodes</span><span class="highlight">${statusData.memory.episodes}</span></div>
    </div>
  `;
}

function generateGuardianContent() {
  const s = statusData || {};
  const sec = s.security || {};
  
  return `
    <div class="security-status">
      <div class="status-row"><span>Guardian</span><span class="highlight">${killEngaged ? "DISARMED" : "ARMED"}</span></div>
      <div class="status-row"><span>Consent Mode</span><span class="highlight">${(sec.consent_mode || "once").toUpperCase()}</span></div>
      <div class="status-row"><span>Network Gate</span><span class="highlight ${sec.network_gate ? "" : "alert"}">${sec.network_gate ? "OPEN" : "CLOSED"}</span></div>
      <div class="status-row"><span>Kill Switch</span><span class="${killEngaged ? "alert" : "dim"}">${killEngaged ? "ENGAGED" : "STANDBY"}</span></div>
      <div class="status-row"><span>Network Threat</span><span class="highlight ${killEngaged ? "alert" : ""}">${killEngaged ? "CRITICAL" : (s.llm && s.llm.route === "none" ? "ELEVATED" : "LOW")}</span></div>
      <div class="btn-row">
        <button id="kill-btn" class="hud-btn">${killEngaged ? "DISENGAGE" : "ARM KILL SWITCH"}</button>
        <button id="gate-btn" class="hud-btn">${sec.network_gate ? "CLOSE GATE" : "OPEN GATE"}</button>
      </div>
    </div>
  `;
}

async function setDisplay(panel, reason) {
  try {
    await fetchJSON("/api/system/display", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ panel: panel || null, reason: reason || "operator request" }),
    });
    if (panel) showPanel(panel, reason);
    else hideAllPanels();
  } catch (err) {
    addLine("⚠ panel request failed: " + (err.detail || err.status), "error");
  }
}

async function pollDisplay() {
  try {
    const s = await fetchJSON("/api/system/display");
    const payloadChanged = s.panel === "map" && JSON.stringify(s.payload) !== JSON.stringify(displayPayload);
    if (s.panel !== displayPanel || payloadChanged) {
      if (s.panel) showPanel(s.panel, s.reason, s.payload);
      else hideAllPanels();
    }
  } catch (e) { /* transient */ }
}

async function refreshMemoryPanel() {
  try {
    const items = await fetchJSON("/api/system/memory/recent?limit=12");
    const memoryStreamEl = document.getElementById("memory-stream");
    if (!memoryStreamEl) return;
    
    memoryStreamEl.innerHTML = "";
    for (const item of items) {
      seenMemory.add(item.id);
      const node = document.createElement("div");
      node.className = "memory-node";
      node.innerHTML = `> ${escapeHtml(String(item.content).slice(0, 160))}<br><span class="meta">[${escapeHtml(item.ts)} · ${escapeHtml(item.kind)}]</span>`;
      memoryStreamEl.appendChild(node);
    }
  } catch (e) { /* transient */ }
}

function addMemoryNode(content, meta) {
  const memoryStreamEl = document.getElementById("memory-stream");
  if (!memoryStreamEl) return;
  
  const node = document.createElement("div");
  node.className = "memory-node";
  node.innerHTML = `> ${escapeHtml(String(content).slice(0, 160))}<br><span class="meta">[${escapeHtml(meta || "")}]</span>`;
  memoryStreamEl.prepend(node);
  while (memoryStreamEl.children.length > 40) memoryStreamEl.lastChild.remove();
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  try {
    statusData = await fetchJSON("/api/system/status");
    killEngaged = statusData.security.kill_switch;
    orb.classList.toggle("danger", killEngaged);
    const threat = killEngaged ? "CRITICAL" : (statusData.llm.route === "none" ? "ELEVATED" : "LOW");
    // Refresh Emma content if status/guardian panels are currently shown
    if (displayPanel === "status") {
      showPanel("status", "status update");
    }
    if (displayPanel === "guardian") {
      showPanel("guardian", "security update");
    }
  } catch (e) { /* transient */ }
}

// ---------------------------------------------------------------- image attach
function setAttachedImage(file) {
  const reader = new FileReader();
  reader.onload = () => {
    attachedImage = { name: file.name || "image", dataUrl: reader.result };
    $("image-chip-label").textContent = "🖼 " + attachedImage.name;
    $("image-chip").classList.remove("hidden");
    $("attach-img-btn").classList.add("active");
    $("live-btn").classList.remove("hidden");
  };
  reader.readAsDataURL(file);
}

function clearAttachedImage() {
  // A live watch keeps running on the captured image; if the user removes
  // the chip while watching, stop the watch too.
  if (liveWatching) stopLive();
  attachedImage = null;
  $("image-input").value = "";
  $("image-chip").classList.add("hidden");
  $("attach-img-btn").classList.remove("active");
  $("live-btn").classList.add("hidden");
  $("live-btn").classList.remove("active");
}

// ---------------------------------------------------------------- drag-drop
// Drag an image file (or an image URL from another tab) onto the HUD to
// attach it for Emma to describe — same path as the 📷 button.
let dragDepth = 0;

function isImageFile(file) {
  return file && (
    file.type === "image/png" || file.type === "image/jpeg" ||
    /\\.(png|jpe?g)$/i.test(file.name || "")
  );
}

function ensureTextInputOpen() {
  if (isTextInputVisible) return;
  isTextInputVisible = true;
  $("text-input-container").classList.remove("hidden");
  $("text-toggle-btn").classList.add("active");
  if (isRecording) stopRecording();
}

function showDropOverlay() { $("drop-overlay").classList.remove("hidden"); }
function hideDropOverlay() { $("drop-overlay").classList.add("hidden"); }

function hasFilesInDrag(e) {
  const types = e.dataTransfer && e.dataTransfer.types;
  if (!types) return false;
  const t = Array.from(types);
  // Accept file drags AND image-URL drags (from another tab or the address
  // bar, which carry text/uri-list but no File).
  return t.indexOf("Files") !== -1 || t.indexOf("text/uri-list") !== -1;
}

function onDragEnter(e) {
  if (!hasFilesInDrag(e)) return;
  e.preventDefault();
  dragDepth++;
  showDropOverlay();
}

function onDragOver(e) {
  if (!hasFilesInDrag(e)) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
}

function onDragLeave(e) {
  if (!hasFilesInDrag(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) hideDropOverlay();
}

function onDrop(e) {
  if (!hasFilesInDrag(e)) return;
  e.preventDefault();
  dragDepth = 0;
  hideDropOverlay();
  const dt = e.dataTransfer;
  const file = dt && dt.files && dt.files[0];
  if (file) {
    if (isImageFile(file)) {
      setAttachedImage(file);
      ensureTextInputOpen();
      addLine("📷 " + file.name + " attached — Emma will describe it when you send (or tap 🔴 to watch it live).", "meta");
    } else {
      addLine("⚠ Only PNG/JPEG images can be attached.", "error");
    }
    return;
  }
  // No file — maybe an image URL dragged from another tab.
  const uri = (dt && (dt.getData("text/uri-list") || dt.getData("text/plain"))) || "";
  const url = uri.trim().split(/\s+/)[0];
  if (/^https?:\/\/\S+$/i.test(url)) {
    ensureTextInputOpen();
    const input = $("text-input");
    input.value = url;
    input.dispatchEvent(new Event("input"));  // reveals the 🔴 watch button
    addLine("🔗 " + url + " — send to describe it, or tap 🔴 to watch it live.", "meta");
  }
}

// ---------------------------------------------------------------- live watch
// Live mode: Emma re-analyzes a changing source — the attached image, a
// pasted image URL, the desktop screen, or the browser — every few seconds
// and speaks up ONLY when the scene changes.  `liveCfg` remembers the active
// source so auto-reconnects resume the same watch.
let liveCfg = null;

const WATCH_LABELS = {
  screen:  { label: "WATCHING SCREEN…",  meta: "👁 watching the screen — Emma will speak up when something changes…",  btn: "watch-screen-btn" },
  browser: { label: "WATCHING BROWSER…", meta: "👁 watching the browser — Emma will speak up when the page changes…",  btn: "watch-browser-btn" },
  image:   { label: "WATCHING…",         meta: "👁 watching the image — Emma will report when the scene changes…",      btn: "live-btn" },
};

function setWatchButtons(activeBtnId) {
  ["live-btn", "watch-screen-btn", "watch-browser-btn"].forEach((id) => {
    $(id).classList.toggle("active", id === activeBtnId);
  });
}

function startWatch(cfg) {
  liveCfg = cfg;
  liveWatching = true;
  liveReconnects = 0;
  setWatchButtons(cfg.btnId);
  addLine(cfg.metaText, "meta");
  setVoiceStatus(cfg.label, true);
  liveAbort = new AbortController();
  runWatchStream();
}

function toggleLive() {
  if (liveWatching) {
    stopLive();
    return;
  }
  let source = attachedImage ? attachedImage.dataUrl : null;
  if (!source) {
    const t = $("text-input").value.trim();
    if (/^https?:\/\/\S+$/i.test(t)) source = t;
  }
  if (!source) {
    addLine("⚠ Attach an image (📷) or paste an image URL (webcam/monitoring feed) to watch it live.", "error");
    return;
  }
  const def = WATCH_LABELS.image;
  startWatch({ image: source, source: "image", btnId: def.btn, label: def.label, metaText: def.meta });
}

// 🖥 / 🌐 buttons — watch the desktop screen or the headless browser.
function toggleScreenWatch(kind, toggle = true) {
  const def = WATCH_LABELS[kind];
  if (liveWatching && liveCfg && liveCfg.source === kind) {
    if (toggle) stopLive();
    return;
  }
  if (liveWatching) stopLive();
  startWatch({ image: "", source: kind, btnId: def.btn, label: def.label, metaText: def.meta });
}

function stopLive() {
  if (liveAbort) {
    liveAbort.abort();
    liveAbort = null;
  }
  liveWatching = false;
  liveCfg = null;
  setWatchButtons(null);
}

async function runWatchStream() {
  const cfg = liveCfg;
  // graceful = ended by design (vision_stop/vision_error/HTTP error); anything
  // else (network blip, browser throttling) is an unexpected drop we retry.
  let graceful = false;
  try {
    const res = await fetch("/api/chat/vision/live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "",
        image: cfg.image,
        source: cfg.source,
        interval_seconds: 5,
        min_change_interval: 15,  // "only when needed": cooldown between spoken changes
      }),
      signal: liveAbort.signal,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      addLine("⚠ " + (body.detail || res.statusText), "error");
      graceful = true;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let event;
        try { event = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
        if (event.type === "vision_stop") graceful = true;
        // vision_error with retry:true (feed/model blip) is NOT graceful —
        // the client reconnects; retry:false (text-only model) is.
        if (event.type === "vision_error" && !event.retry) graceful = true;
        handleEvent(event);
      }
    }
  } catch (err) {
    if (!(err && err.name === "AbortError")) {
      addLine("⚠ live watch error: " + (err && err.message ? err.message : err), "error");
    }
  } finally {
    // stopLive() flips liveWatching to false — capture whether the user (or a
    // vision event) already stopped this before we got here.
    const userStopped = !liveWatching;
    stopLive();
    if (!attachedImage && cfg.source === "image") $("live-btn").classList.add("hidden");
    if (!graceful && !userStopped) {
      // Unexpected drop: reconnect so the watch keeps going.  Vision errors
      // are graceful (they carry their own message); retries are capped so a
      // dead source doesn't loop forever.
      if (liveReconnects < 5) {
        liveReconnects++;
        addLine("[watch interrupted — reconnecting…]", "meta");
        setTimeout(() => {
          if (liveWatching) return;
          liveCfg = cfg;
          liveWatching = true;
          setWatchButtons(cfg.btnId);
          if (cfg.source === "image") $("live-btn").classList.remove("hidden");
          liveAbort = new AbortController();
          runWatchStream();
        }, 3000);
      } else {
        addLine("⚠ live watch could not reconnect after several attempts — tap the watch button to try again.", "error");
      }
    }
  }
}

// ---------------------------------------------------------------- chat
function startReplyLine() {
  const line = document.createElement("div");
  line.className = "terminal-line agent";
  line.appendChild(document.createElement("span"));
  line.lastChild.className = "cursor";
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
  activeLine = { el: line, text: "" };
  streamedTokens = false;
}

function appendToken(token) {
  if (!activeLine) startReplyLine();
  activeLine.text += token;
  streamedTokens = true;
  activeLine.el.innerHTML = escapeHtml(activeLine.text) + '<span class="cursor"></span>';
  terminal.scrollTop = terminal.scrollHeight;
}

function finishReplyLine() {
  if (!activeLine) return;
  activeLine.el.innerHTML = escapeHtml(activeLine.text);
  activeLine = null;
  streamedTokens = false;
}

// Local panel commands — instant, before Emma even answers.
function handleLocalCommand(message) {
  const low = message.toLowerCase().trim();
  const show = low.match(/^(?:show|open|display)\s+(?:the\s+)?(memory|status|guardian|security|map|panels?)\b/);
  if (show) {
    const panel = show[1] === "security" ? "guardian"
      : (show[1] === "panels" || show[1] === "panel" ? "status" : show[1]);
    showPanel(panel, "operator request");
    return true;
  }
  if (/^(?:hide|close|dismiss)\b/.test(low) && /(panel|display|everything|view)/.test(low)) {
    hideAllPanels();
    return true;
  }
  if (low === "clear" || low === "/clear" || low === "cls") {
    terminal.innerHTML = "";
    return true;
  }
  if (low === "help" || low === "/help" || low === "?") {
    addLine("Commands: automatic voice listening · toggle text input with ⌨ · say \"show memory/status/guardian/map\" · say \"help\". Emma controls what content to display.", "meta");
    return true;
  }
  return false;
}

async function sendMessage(message) {
  message = (message || "").trim();
  if (!message && !attachedImage) return;
  if (streaming) {
    addLine("⚠ Emma is still working — wait for the reply before sending another command.", "error");
    return;
  }
  // Stash the message so an inline consent event (mid-stream) can resend it
  // after approval — both the 409 path and the SSE consent event use this.
  // Stop any audio still playing from a previous turn.
  stopAudio();
  pendingMessage = message;
  if (attachedImage) {
    addLine("User: 📷 " + attachedImage.name + (message ? " — " + message : ""), "user");
  } else {
    addLine("User: " + message, "user");
  }
  if (!attachedImage && handleLocalCommand(message)) return;
  streaming = true;
  orb.classList.add("thinking");
  // Only vision turns consume the attached image — text/voice turns must not
  // disturb an active live watch or a pending attachment.
  const hadImage = !!attachedImage;

  try {
    let res;
    if (attachedImage) {
      // 'Describe this image' flow: the image rides along to the vision
      // endpoint, which narrates what Emma sees (gemma4 cloud primary).
      res = await fetch("/api/chat/vision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, image: attachedImage.dataUrl }),
      });
    } else {
      res = await fetch("/api/chat/stream?message=" + encodeURIComponent(message));
    }
    if (res.status === 409) {
      const body = await res.json();
      showConsent(body.decision, message);
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      addLine("⚠ " + (body.detail || res.statusText), "error");
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let event;
        try { event = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
        handleEvent(event);
      }
    }
  } catch (err) {
    addLine("⚠ stream error: " + (err && err.message ? err.message : err), "error");
  } finally {
    finishReplyLine();
    orb.classList.remove("thinking");
    streaming = false;
    // The image was consumed by this turn (vision path) — clear it so the
    // next message goes back to the normal text path.  Text/voice turns
    // leave the attachment and any live watch untouched.
    if (hadImage) clearAttachedImage();
    // If the stream ended without speak_segments (non-LLM intent or
    // LLM unavailable), make sure the audio UI is clean — unless a live
    // watch is active, whose status label must stay on screen.
    if (!currentlyPlaying && audioQueue.length === 0) {
      activeBaseTurnId = null;
      if (liveWatching && liveCfg) setVoiceStatus(liveCfg.label, true);
      else setVoiceStatus("HOLD TO TALK", false);
    }
    // Whatever the outcome, return to always-on listening.
    ensureListening();
  }
}

function handleEvent(event) {
  switch (event.type) {
    case "token":
      appendToken(event.text);
      break;
    case "speak_segment":
      onSpeakSegment(event);
      break;
    case "action":
      addLine(`Agent [Control]: ${event.action.tool} → ${String(event.action.output).replace(/\s+/g, " ").slice(0, 140)}`, "action");
      break;
    case "memory":
      if (!seenMemory.has(event.episode.id)) {
        seenMemory.add(event.episode.id);
        if (displayPanel === "memory") addMemoryNode(event.episode.content, event.episode.ts || new Date().toISOString());
      }
      break;
    case "consent":
      showConsent(event.decision);
      break;
    case "display":
      if (!event.display) break;
      // Continuous vision watch — "watch my screen" / "watch the browser"
      // (voice or text) tells the HUD to open the watch SSE stream.
      if (event.display.watch) {
        toggleScreenWatch(event.display.watch, false);
        break;
      }
      if (event.display.watch_stop) {
        if (liveWatching) {
          stopLive();
          addLine("👁 Watch stopped.", "meta");
        }
        break;
      }
      if (event.display.panel) showPanel(event.display.panel, event.display.reason, event.display.payload);
      else hideAllPanels();
      break;
    case "vision_start":
      setVoiceStatus(liveCfg ? liveCfg.label : "WATCHING…", true);
      break;
    case "vision_change":
      finishReplyLine();
      addLine(event.description, "agent");
      addLine("[vision change]", "meta");
      break;
    case "vision_heartbeat":
      // keep-alive frame — nothing to render; the active 🔴 button is the indicator
      break;
    case "vision_error":
      // The stream ends right after this event; the finally in startLiveStream
      // cleans up and reconnects (retry:true) or stops (retry:false).  Don't
      // stopLive() here — that would look like a user-initiated stop.
      addLine("⚠ " + event.message, "error");
      break;
    case "vision_stop":
      addLine("[watch ended — " + event.reason + "]", "meta");
      break;
    case "done":
      if (event.result && event.result.pending_consent) showConsent(event.result.pending_consent);
      if (event.result && event.result.intent && event.result.intent !== "chat") {
        addLine("[" + event.result.intent + " intent]", "meta");
      }
      // Intents that don't stream tokens (self_improve, control, security,
      // memory, map) deliver their answer in result.output — render it.
      if (event.result && event.result.output && !streamedTokens) {
        if (activeLine) {
          activeLine.el.innerHTML = escapeHtml(event.result.output);
          activeLine = null;
        } else {
          addLine(event.result.output, "agent");
        }
      }
      finishReplyLine();
      // Show which model actually answered this turn, e.g. "[via gemma4:31b-cloud]"
      // or "[via local qwen3.5:2b fallback]" when the cloud quota ran out.
      if (event.served_by) {
        const sb = event.served_by;
        const label = sb.provider === "local"
          ? "local " + sb.model + " fallback"
          : sb.model;
        addLine("[via " + label + "]", "meta");
      }
      break;
  }
}

// ---------------------------------------------------------------- consent
let pendingMessage = null;
let pendingConsent = null;

function showConsent(decision, resend) {
  pendingConsent = decision;
  pendingMessage = resend || pendingMessage;
  $("consent-text").textContent = `[${decision.severity}] ${decision.action}: ${decision.reason}`;
  $("consent-banner").classList.remove("hidden");
}

async function resolveConsent(approve) {
  if (!pendingConsent) return;
  const token = pendingConsent.token;
  const message = pendingMessage;
  pendingConsent = null;
  pendingMessage = null;
  $("consent-banner").classList.add("hidden");
  try {
    await fetchJSON("/api/security/consent/" + (approve ? "approve" : "deny"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    addLine(approve ? "✔ Consent approved — resuming." : "✘ Consent denied.", approve ? "meta" : "error");
    if (approve && message) sendMessage(message);
  } catch (err) {
    addLine("⚠ consent request failed: " + (err.detail || err.status), "error");
  }
}

// ---------------------------------------------------------------- audio queue
function onSpeakSegment(msg) {
  // First segment of a new turn: flip the UI to speaking state.
  if (activeBaseTurnId === null && audioQueue.length === 0 && !currentlyPlaying) {
    activeBaseTurnId = msg.base_turn_id;
    orb.classList.add("thinking");
    setVoiceStatus("SPEAKING…", true);
  } else if (msg.base_turn_id !== activeBaseTurnId) {
    // Stale segment from a previous turn — drop it.
    return;
  }
  audioQueue.push(msg);
  pumpQueue();
}

function pumpQueue() {
  if (currentlyPlaying || audioQueue.length === 0) return;
  const seg = audioQueue.shift();
  lastSegmentWasFinal = !!seg.is_final;
  currentlyPlaying = true;
  playSegmentAudio(seg.turn_id).catch((err) => {
    if (err && err.name === "AbortError") return;
    console.error("segment playback failed", err);
    if (err && err.name === "NotAllowedError") {
      addLine("🔇 Emma's voice is muted by the browser — click anywhere on the page to enable sound.", "meta");
    }
    onSegmentEnded();
  });
}

async function playSegmentAudio(turnId) {
  currentFetchAbort = new AbortController();
  const url = "/api/tts/" + encodeURIComponent(turnId);
  const resp = await fetch(url, { signal: currentFetchAbort.signal });
  if (!resp.ok) throw new Error("tts " + resp.status);
  const arr = await resp.arrayBuffer();
  const blob = new Blob([arr], { type: "audio/mpeg" });
  const objUrl = URL.createObjectURL(blob);
  return new Promise((resolve, reject) => {
    audioEl.onended = () => { URL.revokeObjectURL(objUrl); onSegmentEnded(); resolve(); };
    audioEl.onerror = (e) => { URL.revokeObjectURL(objUrl); reject(e); };
    audioEl.src = objUrl;
    audioEl.play().catch(reject);
  });
}

function onSegmentEnded() {
  currentlyPlaying = false;
  currentFetchAbort = null;
  if (audioQueue.length > 0) {
    pumpQueue();
    return;
  }
  if (lastSegmentWasFinal) {
    activeBaseTurnId = null;
    lastSegmentWasFinal = false;
    orb.classList.remove("thinking");
    // A live watch keeps its own status label after Emma speaks a change.
    if (liveWatching && liveCfg) setVoiceStatus(liveCfg.label, true);
    else setVoiceStatus("LISTENING…", true);
    // Automatically go back to listening after Emma finishes speaking
    ensureListening();
  }
  // else: queue empty but more segments expected — idle until next event.
}

function stopAudio() {
  // Interrupt: clear the queue, abort in-flight fetch, stop playback.
  audioQueue.length = 0;
  if (currentFetchAbort) {
    try { currentFetchAbort.abort(); } catch (_) {}
    currentFetchAbort = null;
  }
  currentlyPlaying = false;
  activeBaseTurnId = null;
  lastSegmentWasFinal = false;
  try { audioEl.pause(); audioEl.currentTime = 0; } catch (_) {}
  orb.classList.remove("thinking");
  setVoiceStatus("LISTENING…", true);
}

// ---------------------------------------------------------------- voice
function ensureListening() {
  // Always-on mic: restart listening whenever the user hasn't muted and
  // nothing else is on the audio path (text-input mode, Emma currently
  // speaking, or a turn in flight).  This is what keeps the mic on after
  // every transcription / silence / reply cycle.
  if (!micEnabled || isTextInputVisible) return;
  if (!isRecording && !currentlyPlaying && !streaming) {
    startRecording();
  }
}

async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    addLine("⚠ microphone not supported in this browser", "error");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    recordedSpeech = false;
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) recordedChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
      if (!recordedSpeech || blob.size === 0) {
        // Pure silence (or an empty capture) — nothing to transcribe.  Skip
        // the API call so the always-on loop doesn't burn STT quota or 500
        // on empty audio; just go straight back to listening.
        ensureListening();
        return;
      }
      await transcribeAndSend(blob);
    };
    mediaRecorder.start();
    isRecording = true;
    $("mic-btn").classList.add("recording");
    orb.classList.add("listening");
    setVoiceStatus("LISTENING…", true);
    
    // Set up silence detection using Web Audio API
    setupSilenceDetection(stream);
  } catch (err) {
    addLine("⚠ mic error: " + err.message, "error");
  }
}

function stopRecording() {
  // If audio is playing, this is an interrupt — stop the audio queue first.
  if (currentlyPlaying || audioQueue.length > 0) {
    stopAudio();
    addLine("⚠ interrupt — playback stopped.", "meta");
  }
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  isRecording = false;
  $("mic-btn").classList.remove("recording");
  orb.classList.remove("listening");
  setVoiceStatus("PROCESSING…", false);
  
  // Clear silence detection timers
  if (silenceTimer) {
    clearTimeout(silenceTimer);
    silenceTimer = null;
  }
  if (recordingTimeout) {
    clearTimeout(recordingTimeout);
    recordingTimeout = null;
  }
}

function setupSilenceDetection(stream) {
  // Always-on listening restarts the detection loop every few seconds — close
  // the previous AudioContext so we don't leak one per cycle.
  if (silenceAudioContext) {
    try { silenceAudioContext.close(); } catch (_) { /* already closed */ }
  }
  const audioContext = new (window.AudioContext || window.webkitAudioContext)();
  silenceAudioContext = audioContext;
  const analyser = audioContext.createAnalyser();
  const microphone = audioContext.createMediaStreamSource(stream);
  const scriptProcessor = audioContext.createScriptProcessor(2048, 1, 1);

  analyser.smoothingTimeConstant = 0.8;
  analyser.fftSize = 1024;

  microphone.connect(analyser);
  analyser.connect(scriptProcessor);
  scriptProcessor.connect(audioContext.destination);

  let silenceStartTime = null;
  const SILENCE_THRESHOLD = 0.02; // Adjust based on environment
  // Level that counts as actual speech (vs. ambient noise) — used to decide
  // whether a recording is worth sending to STT at all.
  const SPEECH_THRESHOLD = 0.04;
  // How long the room must stay quiet before Emma stops listening.  Long
  // enough that natural mid-thought pauses don't cut you off — she stops
  // when she's confident you're done talking.  (Tune here: 5000 = 5s.)
  const SILENCE_DURATION = 5000; // ms of silence before stopping

  scriptProcessor.onaudioprocess = () => {
    if (!isRecording) return;
    
    const array = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(array);
    
    let values = 0;
    for (let i = 0; i < array.length; i++) {
      values += array[i];
    }
    const average = values / array.length;
    const normalized = average / 255;
    
    if (normalized < SILENCE_THRESHOLD) {
      if (!silenceStartTime) {
        silenceStartTime = Date.now();
      } else if (Date.now() - silenceStartTime > SILENCE_DURATION) {
        // Silence detected for long enough, stop recording
        stopRecording();
        silenceStartTime = null;
      }
    } else {
      // Sound detected, reset silence timer
      silenceStartTime = null;
      if (normalized >= SPEECH_THRESHOLD) {
        recordedSpeech = true;
      }
    }
  };
}

function setVoiceStatus(text, active) {
  const el = $("voice-status");
  if (el) el.textContent = text;
  const dot = $("voice-dot");
  if (dot) dot.classList.toggle("active", !!active);
}

function toggleTextInput() {
  isTextInputVisible = !isTextInputVisible;
  const container = $("text-input-container");
  const toggleBtn = $("text-toggle-btn");
  
  if (isTextInputVisible) {
    container.classList.remove("hidden");
    toggleBtn.classList.add("active");
    // Pause recording when text input is shown
    if (isRecording) {
      stopRecording();
    }
    $("text-input").focus();
  } else {
    container.classList.add("hidden");
    toggleBtn.classList.remove("active");
    // Resume listening when text input is hidden
    ensureListening();
  }
}

function sendTextMessage() {
  const input = $("text-input");
  const text = input.value.trim();
  if (text || attachedImage) {
    sendMessage(text);
    input.value = "";
    // After sending, keep text input open for follow-up
    input.focus();
  }
}

async function transcribeAndSend(blob) {
  const form = new FormData();
  form.append("file", blob, "voice.webm");
  try {
    const res = await fetch("/api/voice/transcribe", { method: "POST", body: form });
    if (!res.ok) {
      let detail = res.statusText;
      try { const b = await res.json(); detail = b.detail || detail; } catch (_) { /* non-JSON error body */ }
      throw new Error(detail);
    }
    const body = await res.json();
    if (body.text && body.text.trim()) {
      sendMessage(body.text); // Emma answers — onSegmentEnded restarts the mic
    } else {
      // Nothing said: stay always-on.  Log this at most every 30s so a quiet
      // room doesn't flood the terminal with "nothing heard" lines.
      const now = Date.now();
      if (now - lastNothingHeard > 30000) {
        lastNothingHeard = now;
        addLine("⚠ nothing heard — still listening", "error");
      }
      ensureListening();
    }
  } catch (err) {
    addLine("⚠ transcription failed: " + err.message, "error");
    ensureListening();
  }
}

// ---------------------------------------------------------------- toggles
async function toggleKillSwitch() {
  try {
    const body = await fetchJSON("/api/security/killswitch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engaged: !killEngaged, reason: "HUD operator" }),
    });
    killEngaged = body.engaged;
    orb.classList.toggle("danger", killEngaged);
    addLine(killEngaged ? "KILL SWITCH ENGAGED — Emma frozen." : "Kill switch disengaged.", killEngaged ? "error" : "meta");
    loadStatus();
  } catch (err) {
    addLine("⚠ kill switch toggle failed: " + (err.detail || err.status), "error");
  }
}

async function toggleNetworkGate() {
  const gate = $("network-gate").textContent === "OPEN";
  try {
    await fetchJSON("/api/security/network-gate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ open: !gate, reason: "HUD operator" }),
    });
    addLine(gate ? "Network gate CLOSED — egress blocked." : "Network gate OPEN.", "meta");
    loadStatus();
  } catch (err) {
    addLine("⚠ gate toggle failed: " + (err.detail || err.status), "error");
  }
}

// ---------------------------------------------------------------- wiring
$("consent-approve").addEventListener("click", () => resolveConsent(true));
$("consent-deny").addEventListener("click", () => resolveConsent(false));
$("mic-btn").addEventListener("click", () => {
  micEnabled = !micEnabled;
  if (micEnabled) {
    startRecording();
  } else {
    stopRecording();
  }
});
$("text-toggle-btn").addEventListener("click", toggleTextInput);
$("send-text-btn").addEventListener("click", sendTextMessage);
$("attach-img-btn").addEventListener("click", () => $("image-input").click());
$("live-btn").addEventListener("click", toggleLive);
$("watch-screen-btn").addEventListener("click", () => toggleScreenWatch("screen"));
$("watch-browser-btn").addEventListener("click", () => toggleScreenWatch("browser"));
$("image-input").addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  if (file) setAttachedImage(file);
});
$("image-chip-remove").addEventListener("click", clearAttachedImage);
$("text-input").addEventListener("keypress", (e) => {
  if (e.key === "Enter") {
    sendTextMessage();
  }
});
$("text-input").addEventListener("input", () => {
  // Show the 🔴 watch button for a pasted image URL (webcam/monitoring feed)
  // even when no file is attached.
  const isUrl = /^https?:\/\/\S+$/i.test($("text-input").value.trim());
  $("live-btn").classList.toggle("hidden", !(attachedImage || isUrl));
  if (!isUrl && !attachedImage) $("live-btn").classList.remove("active");
});
$("emma-content-close").addEventListener("click", hideEmmaContent);

// Dynamic event delegation for dynamically generated buttons
document.addEventListener("click", (e) => {
  if (e.target.id === "kill-btn") {
    toggleKillSwitch();
    // Regenerate guardian content if it's currently shown
    if (displayPanel === "guardian") {
      showPanel("guardian", "operator request");
    }
  }
  if (e.target.id === "gate-btn") {
    toggleNetworkGate();
    // Regenerate guardian content if it's currently shown
    if (displayPanel === "guardian") {
      showPanel("guardian", "operator request");
    }
  }
});

// ---------------------------------------------------------------- bootloadStatus();
setInterval(loadStatus, 6000);
setInterval(pollDisplay, 2000);

addLine("Emma online — automatic listening enabled. Click 🎙 to toggle, or say \"help\".", "meta");

// Start automatic listening on load — the mic stays on from here on.
setTimeout(ensureListening, 1000);
