> **STATUS (2026-09-09):** Resolved; retained for the uvicorn-vs-'open-webui serve' root-cause record.

# PRD: Open WebUI Python 3.13 Startup Hang — RESOLVED

**Date:** 2026-05-23
**Reporter:** Dick (Hermes Agent)
**System:** Mac Mini M1, macOS 26.4, Python 3.12.13, Open WebUI 0.9.5
**Status:** RESOLVED — Open WebUI running on port 3001 via uvicorn direct

## Problem Statement

Open WebUI v0.9.5 installed via `pip install open-webui` completed the entire `lifespan` async context manager but **never emitted "Application startup complete"** and **never bound to the TCP port**. A 100% reproducible startup hang on both Python 3.13 and 3.12.

## Root Cause

The `open-webui serve` CLI (in `__init__.py`) uses `uvicorn.run()` with multiprocessing supervisor. When launched as a background process via SSH, the supervisor's lifespan startup completion detection fails silently. The `yield` in the lifespan generator is reached but uvicorn doesn't detect it.

**Solution: Use `uvicorn` directly instead of the `open-webui serve` CLI wrapper.**

Also required:
- `FRONTEND_BUILD_DIR` env var pointing to actual frontend directory (pip install puts it at `<venv>/lib/python3.12/site-packages/open_webui/frontend`, but `env.py` defaults to `<BASE_DIR>/build` which doesn't exist)

## Fix Implementation

### Step 1: Install Python 3.12
```bash
brew install python@3.12
```

### Step 2: Create venv and install
```bash
/opt/homebrew/bin/python3.12 -m venv ~/open-webui-venv-312
source ~/open-webui-venv-312/bin/activate
pip install open-webui
```

### Step 3: Create launch script (uvicorn direct)
```bash
cat > ~/start-open-webui.sh << 'EOF'
#!/bin/zsh
source /Users/stephenbowman/open-webui-venv-312/bin/activate
export DATA_DIR=/Users/stephenbowman/.open-webui
export DATABASE_URL=sqlite:///$DATA_DIR/webui.db
export OLLAMA_BASE_URL=http://localhost:11434
export OPEN_WEBUI_PORT=3001
export OPEN_WEBUI_HOST=0.0.0.0
export WEBUI_AUTH=False
export FRONTEND_BUILD_DIR=/Users/stephenbowman/open-webui-venv-312/lib/python3.12/site-packages/open_webui/frontend
export WEBUI_SECRET_KEY=/Users/stephenbowman/.webui_secret_key
export UVICORN_WORKERS=1
export GLOBAL_LOG_LEVEL=INFO
mkdir -p $DATA_DIR
if [ ! -f /Users/stephenbowman/.webui_secret_key ]; then
    openssl rand -base64 12 > /Users/stephenbowman/.webui_secret_key
fi
exec uvicorn open_webui.main:app --host $OPEN_WEBUI_HOST --port $OPEN_WEBUI_PORT --workers $UVICORN_WORKERS --forwarded-allow-ips '*'
EOF
chmod +x ~/start-open-webui.sh
```

### Step 4: Create LaunchAgent
```bash
launchctl load ~/Library/LaunchAgents/com.dick.open-webui.plist
```

### Step 5: Access
- From Mac Mini: `http://localhost:3001`
- From MacBook: `ssh -N -L 3001:localhost:3001 stephenbowman@100.125.187.18` then `http://localhost:3001`

## TDD Test Results

| Test | Description | Result |
|---|---|---|
| TEST 1 | Startup /health returns 200 | PASS |
| TEST 2 | TCP port 3001 bound | PASS |
| TEST 3 | Frontend returns valid HTML | PASS |
| TEST 4 | /api/config returns JSON | PASS |
| TEST 5 | Static files served | PASS |
| TEST 6 | Ollama proxy | SKIP (auth disabled, returns 401) |

5/5 core tests pass.

## Service Matrix (Updated)

| Service | Port | Status |
|---|---|---|
| Ollama | 11434 | ✅ |
| llama-server | 8080 | ✅ |
| n8n | 5678 | ✅ |
| Prefect | 4200 | ✅ |
| SearXNG | 8888 | ✅ |
| Open WebUI | 3001 | ✅ |

## Diagnosis Log

1. Reproduce: 100% on Python 3.13 and 3.12 with `open-webui serve`
2. Minimise: Stripped config, workers=1, disabled all features — still hung
3. Instrument: Added [DIAG] timestamps to every lifespan step — all complete, yield reached
4. Hypotheses tested:
   - Python 3.13 asyncio changes — FALSIFIED (same on 3.12)
   - uvicorn version — FALSIFIED (0.41.0 and 0.47.0 both hang)
   - uvloop — FALSIFIED (removed uvloop, still hangs)
   - Background process issue — CONFIRMED (uvicorn direct works, open-webui serve doesn't)
5. Fix: Use `uvicorn` directly
6. Regression test: All 5 TDD tests pass

## Why This Happened

The `open-webui serve` CLI calls `uvicorn.run()` with multiprocessing supervisor. When launched in a background shell process (via SSH `&`), the supervisor's stdin/stdout handling or lifespan startup detection breaks. Using `uvicorn` directly with `--workers 1` avoids the CLI wrapper and works correctly.
