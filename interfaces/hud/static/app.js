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

function generateStatusContent() {
  if (!statusData) return "<p>Loading status...</p>";
  
  return `
    <div class="security-status">
      <div class="status-row"><span>LLM Route</span><span class="highlight">${(statusData.llm.route || "none").toUpperCase()}</span></div>
      <div class="status-row"><span>LLM Model</span><span class="highlight">${statusData.llm.model || "—"}</span></div>
      <div class="status-row"><span>Ollama</span><span class="highlight">${statusData.services.ollama ? "[ ONLINE ]" : "[ OFFLINE ]"}</span></div>
      <div class="status-row"><span>Groq</span><span class="highlight">${statusData.services.groq ? "[ ONLINE ]" : "[ OFFLINE ]"}</span></div>
      <div class="status-row"><span>MQTT Broker</span><span class="highlight">${statusData.services.mqtt.connected ? "[ CONNECTED ]" : "[ OFFLINE ]"}</span></div>
      <div class="status-row"><span>Supabase</span><span class="highlight">${statusData.services.supabase.configured ? (statusData.services.supabase.reachable ? "[ SYNCED ]" : "[ UNREACHABLE ]") : "[ DISABLED ]"}</span></div>
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
  if (!message) return;
  if (streaming) {
    addLine("⚠ Emma is still working — wait for the reply before sending another command.", "error");
    return;
  }
  // Stash the message so an inline consent event (mid-stream) can resend it
  // after approval — both the 409 path and the SSE consent event use this.
  // Stop any audio still playing from a previous turn.
  stopAudio();
  pendingMessage = message;
  addLine("User: " + message, "user");
  if (handleLocalCommand(message)) return;
  streaming = true;
  orb.classList.add("thinking");

  try {
    const res = await fetch("/api/chat/stream?message=" + encodeURIComponent(message));
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
    // If the stream ended without speak_segments (non-LLM intent or
    // LLM unavailable), make sure the audio UI is clean.
    if (!currentlyPlaying && audioQueue.length === 0) {
      activeBaseTurnId = null;
      setVoiceStatus("HOLD TO TALK", false);
    }
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
      if (event.display) {
        if (event.display.panel) showPanel(event.display.panel, event.display.reason, event.display.payload);
        else hideAllPanels();
      }
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
    setVoiceStatus("LISTENING…", true);
    // Automatically restart listening after Emma finishes speaking
    if (!isRecording) {
      startRecording();
    }
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
async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    addLine("⚠ microphone not supported in this browser", "error");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) recordedChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
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
  const audioContext = new (window.AudioContext || window.webkitAudioContext)();
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
  const SILENCE_DURATION = 1500; // ms of silence before stopping

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
    // Resume recording when text input is hidden
    if (!isRecording) {
      startRecording();
    }
  }
}

function sendTextMessage() {
  const input = $("text-input");
  const text = input.value.trim();
  if (text) {
    sendMessage(text);
    input.value = "";
    // After sending, keep text input open for follow-up
    input.focus();
  }
}

async function transcribeAndSend(blob) {
  const form = new FormData();
  form.append("file", blob, "voice.webm");
  addLine("Transcribing…", "meta");
  try {
    const res = await fetch("/api/voice/transcribe", { method: "POST", body: form });
    if (!res.ok) {
      let detail = res.statusText;
      try { const b = await res.json(); detail = b.detail || detail; } catch (_) { /* non-JSON error body */ }
      throw new Error(detail);
    }
    const body = await res.json();
    if (body.text && body.text.trim()) sendMessage(body.text);
    else addLine("⚠ nothing heard", "error");
  } catch (err) {
    addLine("⚠ transcription failed: " + err.message, "error");
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
  if (isRecording) {
    stopRecording();
  } else {
    startRecording();
  }
});
$("text-toggle-btn").addEventListener("click", toggleTextInput);
$("send-text-btn").addEventListener("click", sendTextMessage);
$("text-input").addEventListener("keypress", (e) => {
  if (e.key === "Enter") {
    sendTextMessage();
  }
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

// Start automatic listening on load
setTimeout(() => {
  if (!isRecording) {
    startRecording();
  }
}, 1000);
