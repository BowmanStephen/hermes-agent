# PRD: Local AI Services Node — Mac Mini M1

## Status: ✅ Complete
Installed: May 23, 2026 | Updated: May 23, 2026

## Goal
Turn the always-on Mac Mini from a "ComfyUI image worker" into a local AI services hub — TTS, transcription, vector search, LLM inference, workflows, and private search — all running offline, no API keys, no cloud bills.

## Services Installed

| # | Service | Status | Port | How to Use |
|---|---|---|---|---|
| 1 | **Kokoro TTS** | ✅ | CLI | `kokoro-tts-tool synthesize "text" --output file.wav` |
| 2 | **Whisper** | ✅ | CLI | `whisper audio.wav --model tiny` |
| 3 | **Chroma** | ✅ | Local DB | Python `chromadb` in `~/ai-services` venv |
| 4 | **llama.cpp** | ✅ | CLI | Python `llama_cpp` module |
| 5 | **Tome** | ✅ | CLI | `tome-cli transcribe file.wav` or `tome-cli record` |
| 6 | **Ollama** | ✅ | 11434 | Already installed, local + cloud models |
| 7 | **llama-server** | ✅ | 8080 | Qwen3.5-9B always loaded, OpenAI-compatible API |
| 8 | **Prefect** | ✅ | 4200 | Python workflow engine, health check passes |
| 9 | **SearXNG** | ✅ | 8888 | Private metasearch, JSON API enabled |
| 10 | **Dick Workflow** | ✅ | CLI | `dick-workflow --text "..."` chains all services |

### Tome Notes
- Swift app built from source: `~/Tome` → `/Applications/Tome.app` (needs GUI for first launch)
- **Headless CLI wrapper**: `tome-cli` (Python script) uses Whisper in venv

### Failed (macOS/Python limits)
| Service | Why |
|---|---|
| **n8n** | `isolated-vm` native compilation broken on macOS Node 26 |
| **Open WebUI** | Requires Python 3.11-3.12, Mini has 3.14 |

## Shared Environment
```
~/ai-services/bin/activate    # Chroma + llama.cpp + Whisper + Prefect + SearXNG
pipx apps                      # Kokoro + Whisper CLI
```

## Dick Workflow
```bash
# Full pipeline: TTS → Whisper → LLM → Chroma
dick-workflow --text "Your input here"

# Search stored results
dick-workflow --search "your query"
```

**Pipeline steps:**
1. Kokoro TTS generates audio from text
2. Whisper transcribes audio back to text
3. llama-server (Qwen3.5-9B) summarizes the transcript
4. Chroma stores the summary with nomic-embed-text embeddings (768 dims)

## Service Ports
| Port | Service |
|---|---|
| 8080 | llama-server (Qwen3.5-9B, OpenAI-compatible) |
| 11434 | Ollama (local + cloud models) |
| 4200 | Prefect workflow engine |
| 8888 | SearXNG private search |

## Models
| Model | Size | Location |
|---|---|---|
| Kokoro ONNX | ~350MB | `~/.kokoro-tts/models/` |
| Whisper tiny | ~72MB | `~/.cache/whisper/` |
| nomic-embed-text | ~274MB | `~/.ollama/models/` |
| Qwen 2.5 0.5B GGUF | ~409MB | `~/ai-services/models/` |
| Qwen3:14B | ~9.2GB | `~/.ollama/models/` |
| Qwen3.5-9B-Q4_K_M | ~5.6GB | `~/models/` |

## Two-Node Setup
| Machine | Role | Services |
|---|---|---|
| **MacBook M3 Pro** (port 8000) | Heavy renderer | ComfyUI SDXL-Lightning |
| **Mac Mini M1** | Always-on hub | TTS, Whisper, Chroma, llama.cpp, Ollama, llama-server, Prefect, SearXNG, Dick Workflow |

---
Agent: Dick | Date: 2026-05-23
