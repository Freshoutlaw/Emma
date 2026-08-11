# Emma — A Self-Improving, Autonomous AI Assistant

> *"The user is the authority — Emma serves the user completely."*

Emma is a production-grade, Jarvis-class autonomous AI assistant with full
system access. She can read and write any file, execute shell commands,
manage processes, deploy containers, control the desktop, automate a browser,
search the web, talk to your smart home over MQTT, remember everything, and —
critically — **modify her own code to become better over time**.

Everything Emma does is logged to an append-only audit trail. Nothing is
silently blocked; risky actions are gated by a consent manager *you* control,
and a global kill switch can freeze her instantly.

---

## Quick start (local, no Docker)

**One click:** double-click `start-emma.bat` in the repo root (or run
`./start-emma.sh` in Git Bash). It stops any old Emma on port 8000, boots the
backend with the latest code, and opens the HUD.

Manual:

```bash
cd emma-ai
./infrastructure/bootstrap.sh        # venv + deps + .env + pulls Ollama models
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open the **Nexus HUD** at <http://localhost:8000> and start commanding Emma.

- **Local LLM (laptop):** Ollama with `qwen3:5.4b` at `localhost:11434`.
- **Cloud LLM (phone/remote):** Groq with `llama-3.3-70b-versatile` — set `GROQ_API_KEY`.
- The router auto-detects: local Ollama when reachable, Groq otherwise. No
  provider → Emma still works for memory/security/system tasks and tells you
  when an LLM is required.

## Quick start (Docker)

```bash
cd emma-ai
cp infrastructure/.env.example .env
docker compose -f infrastructure/docker-compose.yml up -d
# Emma: http://localhost:8000 · Ollama: 11434 · MQTT: 1883 · Postgres(pgvector): 5432
```

---

## Architecture

```
                 ┌──────────────────────────────┐
                 │        HUD (interfaces/hud)  │  ← voice, vision, status
                 └──────────────┬───────────────┘
                                │ HTTP / SSE
                 ┌──────────────▼───────────────┐
                 │   FastAPI backend (backend/) │  chat · voice · system · security
                 └──────────────┬───────────────┘
                                │
                 ┌──────────────▼───────────────┐
                 │   AgentRouter (agents/router)│  intent classification + dispatch
                 └──────┬──────┬──────┬─────────┘
                        │      │      │
              ┌─────────▼──┐ ┌─▼──────────┐ ┌─▼───────────┐
              │ Reasoning  │ │  Control   │ │ Self-Improve│
              │ (plans via │ │ (tools)    │ │ (rewrites   │
              │  LLM)      │ │            │ │  own code)  │
              └─────────┬──┘ └─┬──────┬───┘ └─────────────┘
                        │      │      │
     ┌──────────────────┼──────┼──────┼─────────────────────┐
     │  Guardian (security/)   │      │                     │
     │  consent · audit · kill-switch · risk classification │
     └──────────────────┼──────┼──────┼─────────────────────┘
                        │      │      │
     ┌──────────────────▼──────▼──────▼─────────────────────┐
     │ Capabilities: system_io · web_search · git · docker  │
     │               mqtt_home · browser · desktop          │
     └──────────────────────────────────────────────────────┘
     LLM router (llm/) ── Ollama (local) ⇄ Groq (cloud)
     Memory (memory/)  ── SQLite episodes + embeddings + Supabase pgvector
```

### The layers

| Layer | Modules | Responsibility |
|---|---|---|
| **backend/** | `main.py`, `config.py`, `routers/*`, `middleware/*` | FastAPI server, API-key auth, request logging, SSE streaming |
| **agents/** | `router.py`, `reasoning.py`, `control.py`, `memory.py`, `security.py`, `self_improve.py`, `base.py` | Intent routing, planning, tool execution, self-modification |
| **capabilities/** | `system_io.py`, `web_search.py`, `mqtt_home.py`, `browser_automation.py`, `desktop_control.py`, `git_manager.py`, `docker_manager.py` | Emma's hands — every tool gates through the Guardian |
| **security/** | `guardian.py`, `consent_manager.py`, `audit_log.py`, `kill_switch.py`, `encryption.py` | Risk classification, consent flow, append-only audit, emergency stop |
| **memory/** | `episodic.py`, `embeddings.py`, `rag_pipeline.py`, `supabase_client.py` | Episodic store (SQLite + cosine recall), pgvector sync, RAG context |
| **llm/** | `router.py`, `local.py`, `cloud.py` | Hybrid router: local Ollama ⇄ Groq fallback, OpenAI-style messages |
| **interfaces/** | `voice/*`, `hud/*`, `vision/*` | STT/TTS/wake-word, Nexus HUD dashboard, mediapipe perception |
| **agents/** (`map.py`) | `map` intent | Resolves regions and flips the HUD to the region intelligence dashboard |
| **infrastructure/** | `docker-compose.yml`, `Dockerfile`, `bootstrap.sh`, `mosquitto.conf` | Full stack deployment |

---

## The security model — Guardian, Consent, Kill Switch

Emma is powerful, so her power is *governed*, not hidden. Every action flows
through `security/guardian.py`:

1. **Kill switch check** — engaged ⇒ everything is denied (severity CRITICAL).
   Engaging it is always allowed; it is the emergency stop and must never be
   locked behind consent.
2. **Risk classification** — shell commands are pattern-scored
   (recursive force delete, filesystem format, raw block writes, `git push`,
   `DROP TABLE`, pipe-to-shell, privilege escalation, force-kill, service
   control, system shutdown) and file writes are path-scored
   (`/etc`, `.ssh`, `.env`, …).
3. **Consent manager** — three modes, per action:
   | Severity | Mode `auto` | Mode `once` (default) | Mode `strict` |
   |---|---|---|---|
   | LOW (read, search) | allow | allow | allow |
   | MED (run cmd, write) | allow | ask once/session | ask every time |
   | HIGH (destructive, self-modify) | allow | ask once/session | ask every time |
4. **Append-only audit log** — every verdict, action and HTTP request is
   recorded in `data/audit.log` (JSONL, auto-rotating).

### Consent over HTTP

When consent is needed, the API responds `409` with a one-time token:

```json
{"detail": "consent required",
 "decision": {"action": "run_command", "severity": "HIGH",
              "reason": "recursive force delete", "token": "…"}}
```

Approve or deny it:

```bash
curl -X POST localhost:8000/api/security/consent/approve -H 'Content-Type: application/json' \
     -d '{"token": "<token>"}'
```

The HUD surfaces this as an inline banner. Approvals last an hour
(`EMMA_APPROVAL_TTL`).

---

## Talking to Emma

### HTTP chat (JSON)

```bash
curl -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
     -d '{"message": "create a file called notes.md that says hello from emma"}'
```

### Streaming chat (SSE) — used by the HUD

```bash
curl -N "localhost:8000/api/chat/stream?message=list my docker containers"
```

Events: `token` (LLM narration), `action` (tool executed), `consent`
(approval needed), `memory` (episode stored), `done`.

### Other endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/system/status` | LLM route, services, security posture, memory count |
| `POST /api/system/command` | Guardian-gated shell execution |
| `GET /api/system/memory/recent` | Recent episodes |
| `GET /api/system/activity` | Recent audit entries |
| `GET /api/security/status` · `/audit` | Guardian state + audit trail |
| `POST /api/security/killswitch` | Engage / disengage the kill switch |
| `POST /api/security/network-gate` | Open / close network egress |
| `POST /api/voice/transcribe` · `/respond` | STT + agent reply with TTS |
| `GET /api/voice/status` | STT/TTS backend availability |

### Example commands

- `"remember that my wifi password hint is stored in data/secrets"` → memory
- `"what do you remember about my server?"` → recall
- `"run git status in this repo"` → control
- `"search the web for the latest FastAPI release"` → web search (needs network gate open)
- `"publish lights/kitchen on with payload on"` → MQTT
- `"list docker containers"` → docker
- `"engage the kill switch"` / `"disengage the kill switch"` → security
- `"show me a map of london"` / `"where is tokyo"` / `"weather in paris"` → map (region dashboard)
- `🌌` button (or open `/cosmos.html`) → the **living cosmic core**: a voice-reactive orb in a procedural deep-space nebula, surrounded by Emma's six sub-agents orbiting as a constellation
- `💰` button (or open `/cost.html`) → the **LLM cost dashboard**: live month-to-date spend, per-model breakdown, cache savings, day-by-day grid, and recent calls — every LLM call's `usage` is captured best-effort (never breaks a turn) into `data/usage.db` and served via `GET /api/system/usage` (60s cached)

### Region map dashboard

Any question that needs a map (`map`, `where is`, `location`, `region`, `flight`,
`weather in`, `coordinates`, …) surfaces a full-screen DataV-style region
dashboard (`/map.html`). Emma resolves the region from a curated city/country
dataset of **real** facts (coordinates, country, timezone, population), falling
back to **Open-Meteo geocoding** for anything else — never invented
coordinates. The dashboard shows **real data only**: an OpenStreetMap map of
the actual coordinates (Leaflet) and **live Open-Meteo weather**. If a region
cannot be resolved (unknown place or the network gate is closed), the
dashboard says so honestly instead of fabricating figures. The 🗺 button in
the HUD opens it manually.
- `"review your own code and suggest improvements"` → self-improve
- `"apply {\"path\": \"agents/memory.py\", \"content\": \"…\"}"` → self-modify (HIGH consent)

---

## Memory & RAG

- Every user exchange is stored as an **episode** in `data/memory.db` (SQLite)
  with an embedding (Ollama `nomic-embed-text`, hashed fallback offline).
- Recall ranks episodes by cosine similarity; the **RAG pipeline** injects the
  top matches into the LLM context automatically.
- **Supabase / pgvector sync** is optional. Enable it by setting
  `EMMA_SUPABASE_URL` + `EMMA_SUPABASE_SERVICE_KEY` and creating the RPC:

```sql
create or replace function match_episodes(query_embedding vector(384), match_count int)
returns table (id text, content text, kind text, created_at timestamptz, similarity float)
language sql stable as $$
  select e.id, e.content, e.kind, e.created_at,
         1 - (e.embedding <=> query_embedding) as similarity
  from episodes e
  order by e.embedding <=> query_embedding
  limit match_count;
$$;
```

## Voice & Vision (optional extras)

```bash
pip install -r requirements.txt        # core
pip install 'vosk' 'edge-tts'          # local STT + TTS
pip install 'mediapipe' 'opencv-python'# vision
pip install 'playwright'               # browser automation (then: playwright install chromium)
pip install 'pyautogui'                # desktop control
```

- **STT:** Groq Whisper when `GROQ_API_KEY` is set, vosk locally otherwise.
- **TTS:** `edge-tts` (Microsoft neural voices, no key).
- **Wake word:** `interfaces/voice/wake_word.py` — energy VAD + keyword match.
- **Vision:** `interfaces/vision/mediapipe_handler.py` — hands, face mesh, pose.

## Self-improvement

`SelfImproveAgent` can read Emma's own source, ask the LLM for concrete
improvements, and apply patches. Writes are:

- restricted to the **project tree** (no system paths, no home-dir escapes),
- gated behind the `self_modify` consent rule (HIGH severity),
- **backed up** to `data/backups/` before every write,
- audit-logged with path, reason and backup location.

## Configuration

All settings are in `backend/config.py`, read from `.env` (prefix `EMMA_`)
with `GROQ_API_KEY` aliased. See `infrastructure/.env.example` for the full
list. Runtime state lives in `data/` (audit log, memory db, kill switch,
network gate, backups, master key).

## Project structure

```
emma-ai/
├── backend/            FastAPI server: main, config, routers, middleware
├── agents/             router, reasoning, memory, security, control, self_improve, base
├── capabilities/       system_io, web_search, mqtt_home, browser, desktop, git, docker
├── security/           guardian, consent_manager, audit_log, kill_switch, encryption
├── memory/             supabase_client, embeddings, rag_pipeline, episodic
├── llm/                router, local (Ollama), cloud (Groq)
├── interfaces/         voice (stt/tts/wake), hud (Nexus dashboard), vision (mediapipe)
├── infrastructure/     docker-compose, Dockerfile, bootstrap.sh, mosquitto.conf, .env.example
├── flags/              network_gate
├── requirements.txt · pyproject.toml · README.md
```

## License

MIT. Emma is a tool; the operator is the authority.
