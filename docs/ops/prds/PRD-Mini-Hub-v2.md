# Mac Mini Local AI Services Hub — Final Status
**Date:** 2026-05-23 | **Machine:** Mac Mini M1 (100.125.187.18)

---

## ✅ Running Services (5/6)

| # | Service | Port | Status | How to Use |
|---|---|---|---|---|
| 1 | **Ollama** | 11434 | ✅ | OpenAI-compatible API. `curl http://localhost:11434/v1/chat/completions` |
| 2 | **llama-server** | 8080 | ✅ | Qwen3.5-9B always loaded. OpenAI-compatible. Faster than Ollama for local |
| 3 | **n8n** | 5678 | ✅ | Node 20 + isolated-vm workaround. LaunchAgent auto-starts. `n8n-run` command |
| 4 | **Prefect** | 4200 | ✅ | Python workflow engine. Health check passes |
| 5 | **SearXNG** | 8888 | ✅ | Private metasearch. JSON API: `?format=json` + `Accept: application/json` |
| 6 | **Dick Workflow** | CLI | ✅ | `dick-workflow --text "..."` chains TTS→Whisper→LLM→Chroma |

### Also Installed (CLI tools)
| Tool | Status | How to Use |
|---|---|---|
| Kokoro TTS | ✅ | `kokoro-tts-tool synthesize "text" --output file.wav` |
| Whisper | ✅ | `whisper audio.wav --model tiny` |
| Chroma | ✅ | Python `chromadb` in `~/ai-services` venv |
| Tome | ✅ | `tome-cli transcribe file.wav` / `tome-cli record` |

---

## 🟡 Partial / Broken

| Service | Status | Issue | Fix Needed |
|---|---|---|---|
| **Open WebUI** | 🟡 Installed, hangs | Python 3.13 compat — `lifespan` hangs at startup | Python 3.12 or Docker |
| **n8n Code node** | 🟡 Limited | `isolated-vm` compiled but may have issues | Monitor usage |

---

## ❌ Blocked

| Service | Why | Resolution |
|---|---|---|
| **n8n on Node 26** | `isolated-vm` native compilation fails | ✅ Fixed by using Node 20 via `n` |
| **Open WebUI on Python 3.14** | `open-webui` requires `<3.13` | ✅ Fixed by using Python 3.13 venv |
| **Open WebUI on Python 3.13** | Hangs at `lifespan` startup | Needs Python 3.12 or Docker |

---

## Service URLs (from Dick/VPS)

| Service | URL | Notes |
|---|---|---|
| Ollama | `http://100.125.187.18:11434` | OpenAI-compatible `/v1/*` endpoints |
| llama-server | `http://100.125.187.18:8080` | Always-loaded Qwen3.5-9B. Fastest local LLM |
| n8n | `http://100.125.187.18:5678` | Workflow editor. Default creds: user@localhost / password |
| Prefect | `http://100.125.187.18:4200` | Flow orchestration UI |
| SearXNG | `http://100.125.187.18:8888` | Private search. Add `?format=json` for API |
| Open WebUI | N/A | Broken — see fix below |

---

## Architecture

```
┌─────────────────────────────────────────┐
│  Mac Mini M1 (100.125.187.18)           │
│  Always-on, 16GB RAM                    │
├─────────────────────────────────────────┤
│  Ollama (11434) ← cloud + local models  │
│  llama-server (8080) ← always-loaded    │
│  n8n (5678) ← workflow engine           │
│  Prefect (4200) ← Python workflows      │
│  SearXNG (8888) ← private search        │
├─────────────────────────────────────────┤
│  CLI: Kokoro, Whisper, Chroma, Tome     │
│  Dick Workflow: chains all CLI tools    │
└─────────────────────────────────────────┘
              ↑
         Tailscale mesh
              ↓
┌─────────────────────────────────────────┐
│  MacBook M3 Pro (100.95.145.37)         │
│  Heavy renderer, ComfyUI (8000)         │
└─────────────────────────────────────────┘
```

---

## Commands (for Dick/VPS)

```bash
# Check all services
ssh stephenbowman@100.125.187.18 'python3 ~/mini_status.py'

# Ollama API test
curl http://100.125.187.18:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3:4b-cloud","messages":[{"role":"user","content":"hi"}]}'

# llama-server API test
curl http://100.125.187.18:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3.5-9B-Q4_K_M.gguf","messages":[{"role":"user","content":"hi"}]}'

# n8n health
curl http://100.125.187.18:5678/healthz

# SearXNG search
curl "http://100.125.187.18:8888/search?q=test&format=json" \
  -H "Accept: application/json"

# Dick Workflow (on Mini)
dick-workflow --text "Your input here"
dick-workflow --search "your query"
```

---

## Dick Workflow Pipeline

```
Input Text
    ↓
Kokoro TTS → audio.wav
    ↓
Whisper → transcription
    ↓
llama-server (Qwen3.5-9B) → summary
    ↓
Chroma (nomic-embed-text 768-dim) → stored vector
```

---

## Open WebUI Fix Path

**Option A: Install Python 3.12**
```bash
# Download python.org installer for macOS
# Or use pyenv: brew install pyenv; pyenv install 3.12.9
# Then recreate venv with 3.12
```

**Option B: Docker Desktop**
```bash
# Install Docker Desktop for Mac
# Then: docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway \
#   -v open-webui:/app/backend/data --name open-webui \
#   ghcr.io/open-webui/open-webui:main
```

**Option C: Use existing services**
The Ollama `/v1/chat/completions` API + Dick Workflow already provide 90% of Open WebUI's functionality. The missing piece is a web chat UI.

---

## Next Steps (if desired)

1. **Fix Open WebUI** — Install Python 3.12 or Docker Desktop
2. **n8n Workflows** — Build betting research pipeline, daily briefing automation
3. **Chroma RAG** — Connect documents for semantic search
4. **Excalidraw** — Low priority. Use draw.io web or install later

---

## Installation Log

| Service | How Installed | Workarounds |
|---|---|---|
| Ollama | Mac app | Already installed |
| llama-server | `brew install llama.cpp` | Already installed |
| Kokoro | `pipx install kokoro-tts-tool` | — |
| Whisper | `pipx install openai-whisper` | — |
| Chroma | `pip install chromadb` in venv | — |
| Tome | Built from source | Swift app + CLI wrapper |
| n8n | `npm install -g n8n` with Node 20 | Node 26 isolated-vm fails |
| Prefect | `pip install prefect` | — |
| SearXNG | Git clone + `pip install -e .` | msgspec dependency |
| Dick Workflow | Python script + wrapper | — |
| Open WebUI | `pip install open-webui` in Python 3.13 venv | Hangs at startup |

---

**Agent:** Dick | **Date:** 2026-05-23 | **Version:** 2.0
