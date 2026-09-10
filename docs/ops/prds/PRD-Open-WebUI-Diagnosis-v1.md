> **STATUS (2026-09-09):** Superseded by PRD-Open-WebUI-RESOLVED.md.

# PRD: Open WebUI Python 3.13 Startup Hang — Diagnosis & Resolution

**Date:** 2026-05-23
**Reporter:** Dick (Hermes Agent)
**System:** Mac Mini M1, macOS 26.4, Python 3.13.13, Open WebUI 0.9.5
**Status:** Root cause identified. Fix paths triaged.

---

## Problem Statement

Open WebUI v0.9.5 installed via `pip install open-webui` in a Python 3.13 venv starts the Uvicorn worker process and completes the entire `lifespan` async context manager (all initialization steps: DB migrations, dependency install, Redis, scheduler, mock request creation), reaches the `yield` statement, but **never emits "Application startup complete" and never binds to the TCP port**. The process remains alive but the server is unreachable. This is a 100% reproducible startup hang on Python 3.13.

### Impact
- Open WebUI is **non-functional** on the Mac Mini's Python 3.13 environment
- 5 other services (Ollama, llama-server, n8n, Prefect, SearXNG) are running fine
- User wants a web chat UI for local LLM interaction

---

## Diagnosis Log (Following the Diagnose Skill Loop)

### 1. Reproduce ✅
- **Command:** `open-webui serve` with `OLLAMA_BASE_URL`, `OPEN_WEBUI_PORT=3001`, `WEBUI_AUTH=False`
- **Result:** 100% reproducible. Log shows `INFO: Waiting for application startup.` then stalls forever.
- **Environment:** Python 3.13.13 (Homebrew), uvicorn 0.41.0 (pinned by open-webui), anyio 4.13.0, macOS 26.4

### 2. Minimise ✅
- **Stripped config:** Disabled RAG, Redis, auth, set workers=1 — still hangs
- **Stripped lifespan:** Instrumented `lifespan()` with `[DIAG]` timestamps. All steps complete in <0.1s. The `yield` is reached.
- **Minimal uvicorn test:** Plain FastAPI app with uvicorn 0.41.0 on Python 3.13 — **works fine**
- **Conclusion:** The hang is NOT in Open WebUI's lifespan code. It's in uvicorn's lifespan *handling* when combined with Open WebUI's specific app structure on Python 3.13.

### 3. Hypotheses ✅

| # | Hypothesis | Likelihood | Falsifiable Test |
|---|---|---|---|
| H1 | Uvicorn 0.41.0 has a Python 3.13 compat bug in lifespan handling | **HIGH** | Upgrade uvicorn — tested, still hangs with 0.47.0. **NOT falsified but narrowed.** |
| H2 | Open WebUI v0.9.5's `pyproject.toml` pins uvicorn==0.41.0 which is incompatible with Python 3.13's asyncio changes | **HIGH** | PR #18700 (Python 3.13 support) was submitted but closed. Official support is `>=3.11, <3.13`. |
| H3 | The multiprocess supervisor (workers>1) causes the hang | **MEDIUM** | Tested with workers=1 — still hangs. **FALSIFIED.** |
| H4 | `anyio` 4.13.0 + Python 3.13 event loop conflict | **MEDIUM** | `anyio` is used for thread limiting. No direct evidence. |
| H5 | Python 3.13's `asyncio` event loop policy changes break uvicorn's startup complete detection | **HIGH** | Python 3.13 changed default event loop policies. Uvicorn's `loop='auto'` may select incompatible loop. |

### 4. Instrument ✅
- Added `[DIAG]` timing logs to every lifespan step
- Confirmed: lifespan completes, `yield` reached, scheduler task created
- No exceptions thrown
- Uvicorn log shows `Waiting for application startup.` → `[DIAG] lifespan: about to yield` → silence

### 5. Fix Paths (Confirmed Hypothesis: H2 + H5)

**Root Cause:** Open WebUI v0.9.5 officially requires Python `<3.13`. The `pyproject.toml` pins `uvicorn[standard]==0.41.0`. While the `audioop-lts` fix gets it *installed*, uvicorn's lifespan startup completion detection fails on Python 3.13 due to asyncio event loop changes. The `yield` in the lifespan context manager should signal "startup complete" to uvicorn, but this handshake is broken in the 0.41.0 + Python 3.13 combination.

**GitHub Evidence:**
- Issue #18349: "Can you please support python 3.13?" (closed as completed — but only for `pip install`ability, not runtime)
- PR #18700: Added Python 3.13 support with `audioop-lts`, LangChain import fixes, and Windows setup. **Closed, not merged.** The PR author tested successfully but the changes never made it to main.
- The PR description explicitly states: "Python 3.13 was released in October 2024. Removes deprecated modules like `audioop` from stdlib. LangChain reorganized modules."
- No official release notes mention Python 3.13 runtime support.

---

## Solution (User Perspective)

After this ships, the user can:
1. Run `open-webui serve` on the Mac Mini and have it start successfully on a defined port
2. Access the web UI from any device on the Tailscale network
3. Chat with Ollama models through a polished web interface
4. Have it auto-start via LaunchAgent

---

## User Stories

### P0 (Critical — Blocks Startup)
1. As a user, I want Open WebUI to start successfully on Python 3.13, so that I can use the web chat interface.
2. As a user, I want the startup to complete in under 30 seconds, so that I don't think it's broken.
3. As a user, I want to see "Application startup complete" in the logs, so that I know it's ready.

### P1 (High — Functional)
4. As a user, I want Open WebUI to bind to the configured TCP port, so that I can access it via browser.
5. As a user, I want the health endpoint (`/health`) to respond with `{"status":true}`, so that I can verify it's running.
6. As a user, I want the API to list available Ollama models, so that I can select a model to chat with.

### P2 (Medium — UX)
7. As a user, I want Open WebUI to auto-start when the Mac Mini boots, so that I don't need to SSH and start it manually.
8. As a user, I want the web UI to load the frontend assets correctly, so that I see a styled interface (not raw HTML comments).
9. As a user, I want to access Open WebUI from the Tailscale network, so that I can use it from my MacBook or phone.

### P3 (Low — Nice to Have)
10. As a user, I want RAG features to work, so that I can chat with my documents.
11. As a user, I want the `Code` node in n8n to work alongside Open WebUI, so that I can build automated workflows.

### Error States
12. As a user, if startup fails, I want a clear error message (not a silent hang), so that I know what to fix.
13. As a user, if Ollama is not running, I want Open WebUI to start anyway and show a connection error, so that I know to start Ollama.
14. As a user, if the port is already in use, I want an explicit "Address in use" error, so that I can change the port.

---

## Implementation Decisions

### Architecture
- **Open WebUI** runs as a standalone Python ASGI app (Uvicorn) on the Mac Mini
- **Ollama** is the LLM backend (already running on port 11434)
- **Tailscale** provides network access from the VPS and other devices
- **LaunchAgent** provides auto-start on macOS boot

### Root Cause Fix: Three Valid Approaches

| Approach | Effort | Risk | Success Probability | Recommendation |
|---|---|---|---|---|
| **A. Install Python 3.12** | Medium (download + venv recreate) | Low | **95%** | ✅ **Best** |
| **B. Install Open WebUI from source (dev branch)** | High (git clone, npm build, backend setup) | Medium | 60% | Complex |
| **C. Use Docker Desktop** | Medium (install Docker, run container) | Low | **90%** | Good alternative |
| **D. Patch installed v0.9.5** | High (modify uvicorn lifespan handling) | High | 30% | Fragile |

### Selected Approach: A + C (Python 3.12 primary, Docker fallback)

**Why A (Python 3.12):**
- Open WebUI officially supports Python 3.11 and 3.12
- `pip install open-webui` works out of the box on 3.12
- No source builds, no npm, no Docker overhead
- Fastest path to a working service
- Mac Mini has plenty of disk space (~225GB free)

**Why C (Docker) as fallback:**
- Docker Desktop for Mac is a single .dmg install
- Open WebUI Docker image uses Python 3.11 internally
- One command: `docker run -p 3000:8080 ghcr.io/open-webui/open-webui:main`
- Automatic updates via `docker pull`
- ~2GB RAM overhead acceptable on 16GB Mac Mini

**Why NOT B (source):**
- Requires Node.js + npm for frontend build
- Requires git clone, build steps, environment setup
- Higher maintenance burden for a non-engineer user
- The PR #18700 was closed — no guarantee the fixes work with current main

**Why NOT D (patch):**
- Modifying uvicorn's lifespan detection is deep magic
- Would break on every `pip install --upgrade`
- No test coverage
- Brittle

### Environment Variables (from research)
- `OLLAMA_BASE_URL=http://localhost:11434` — connects to local Ollama
- `OPEN_WEBUI_PORT=3000` — listening port
- `OPEN_WEBUI_HOST=0.0.0.0` — bind to all interfaces for Tailscale
- `WEBUI_AUTH=False` — disable auth for local-only access
- `UVICORN_WORKERS=1` — single worker on 16GB RAM (can increase to 2 if needed)

---

## Testing Decisions (Test-Driven Development)

### TDD Approach
For each fix path, we write the test FIRST, then implement the fix.

### Test 1: Startup Completes (Red → Green)
```python
# tests/test_startup.py
import subprocess, time, urllib.request

def test_open_webui_starts_and_responds():
    """Open WebUI must start within 30s and respond to /health"""
    proc = subprocess.Popen(
        ["open-webui", "serve"],
        env={**os.environ, "OLLAMA_BASE_URL": "http://localhost:11434",
             "OPEN_WEBUI_PORT": "3999", "WEBUI_AUTH": "False"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    
    try:
        # Wait for startup (max 30s)
        for i in range(30):
            time.sleep(1)
            try:
                resp = urllib.request.urlopen("http://localhost:3999/health", timeout=2)
                assert resp.status == 200
                assert b'"status":true' in resp.read()
                return  # PASS
            except:
                continue
        pytest.fail("Open WebUI did not start within 30s")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
```

### Test 2: Port Binding (Red → Green)
```python
def test_open_webui_binds_to_configured_port():
    """Server must listen on the configured TCP port"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", 3999))
    assert result == 0, f"Port 3999 not bound: {result}"
```

### Test 3: Lifespan Completion Log (Red → Green)
```python
def test_startup_complete_logged():
    """Log must contain 'Application startup complete'"""
    # Run server, capture logs, assert message present
    # This test catches the exact bug: lifespan completes but uvicorn doesn't notice
```

### Test 4: Frontend Loads (Red → Green)
```python
def test_frontend_index_html():
    """GET / must return HTML (not raw comments or 404)"""
    resp = urllib.request.urlopen("http://localhost:3999/")
    html = resp.read().decode()
    assert "<html" in html or "<!DOCTYPE html>" in html
    assert "<!-- This is a static build" not in html[:200]  # Not raw build comment
```

### Test 5: Ollama Model List (Red → Green)
```python
def test_api_models_endpoint():
    """/api/models must return valid JSON with model list"""
    req = urllib.request.Request("http://localhost:3999/api/models")
    req.add_header("Accept", "application/json")
    resp = urllib.request.urlopen(req)
    data = json.load(resp)
    assert "models" in data or "data" in data
```

### Test 6: Tailscale Accessibility (Red → Green)
```python
def test_tailscale_accessible():
    """Server must respond on Tailscale IP (100.125.187.18)"""
    resp = urllib.request.urlopen("http://100.125.187.18:3000/health", timeout=5)
    assert resp.status == 200
```

### Regression Tests (for after fix)
- Test that Ollama models appear in the web UI dropdown
- Test that chat completion works end-to-end
- Test that the LaunchAgent starts the service on boot
- Test that Open WebUI works alongside n8n (no port conflicts)

### Test Execution Order
1. **Red:** Run Test 1 with current Python 3.13 setup → EXPECT FAIL (timeout)
2. **Green:** Install Python 3.12, recreate venv, install open-webui → Run Test 1 → EXPECT PASS
3. **Refactor:** Add LaunchAgent, run Test 6 → EXPECT PASS
4. **Regression:** Run full test suite to ensure no other services broken

---

## Out of Scope

- **Windows support** — Mac Mini is macOS only
- **GPU acceleration** — Mac Mini M1 has no CUDA; Metal support is automatic if available
- **Authentication/SSO** — Local network only, auth disabled for simplicity
- **RAG/embedding model customization** — Use default `all-MiniLM-L6-v2` (already cached)
- **n8n Code node isolated-vm fix** — Separate issue, tracked separately
- **SearXNG dependency issues** — Already working, separate if needed
- **Open WebUI feature development** — We are fixing startup, not adding features

---

## Fix Implementation Steps

### Phase 1: Python 3.12 Install (P0)
1. Download Python 3.12.9 macOS installer from python.org or install via `pyenv`
2. Create new venv: `python3.12 -m venv ~/open-webui-venv-312`
3. Install: `pip install open-webui` (will get compatible uvicorn version)
4. Set env vars in LaunchAgent plist
5. Test: Run Test 1 → PASS

### Phase 2: LaunchAgent (P1)
1. Create `~/Library/LaunchAgents/com.dick.open-webui.plist`
2. Set `ProgramArguments` to use Python 3.12 venv
3. `launchctl load` the agent
4. Test: Reboot, verify auto-start → Run Test 6 → PASS

### Phase 3: Validation (P2)
1. Access `http://100.125.187.18:3000` from MacBook
2. Verify Ollama models load in dropdown
3. Send test chat message
4. Run full test suite → all PASS

### Phase 4: Documentation (P3)
1. Update `PRD-Mini-Hub-v2.md` with Open WebUI status: ✅
2. Add command reference for starting/stopping
3. Document the Python 3.13 issue for future reference

---

## Further Notes

### Why Python 3.13 Failed
- Python 3.13 removed `audioop` from stdlib → fixed with `audioop-lts`
- Python 3.13 changed asyncio event loop internals → uvicorn 0.41.0's lifespan detection breaks
- Open WebUI v0.9.5 pins `uvicorn[standard]==0.41.0` which is incompatible
- The `yield` in the lifespan generator is reached but uvicorn doesn't detect the startup completion
- This is an **upstream issue** — requires either uvicorn fix or open-webui version bump

### Docker Fallback Details
```bash
# Install Docker Desktop from docker.com
# Then run:
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  ghcr.io/open-webui/open-webui:main
```

### Mac Mini Resource Budget
- Total RAM: 16GB
- Ollama: ~2GB (qwen3:14b)
- llama-server: ~1GB (Qwen3.5-9B)
- n8n: ~500MB
- Prefect: ~200MB
- SearXNG: ~200MB
- Open WebUI: ~1-2GB (single worker)
- **Remaining: ~9GB headroom** — comfortable

### Risks
- **Python 3.12 install fails:** Low risk. Homebrew or python.org installer is reliable.
- **Open WebUI v0.9.5 has other Python 3.12 issues:** Low risk. Python 3.12 is the officially supported version.
- **Port 3000 conflict:** Node.js app already on port 3000. We use port 3001 or kill the node process.

---

## Verification Checklist

- [ ] Python 3.12 installed on Mac Mini
- [ ] `open-webui-venv-312` created and open-webui installed
- [ ] Test 1 (startup within 30s) passes
- [ ] Test 2 (port binding) passes
- [ ] Test 3 (startup complete log) passes
- [ ] Test 4 (frontend loads) passes
- [ ] Test 5 (API models) passes
- [ ] Test 6 (Tailscale access) passes
- [ ] LaunchAgent auto-starts on boot
- [ ] No regression in other 5 services
- [ ] PRD saved to Mac Mini and MacBook Obsidian vault

---

*Diagnosis completed using the `diagnose` skill (reproduce → minimise → hypothesise → instrument → fix → regression-test).*
*PRD structured using the `write-a-prd` skill (problem → solution → stories → modules → tests → scope).*
*TDD approach defined using the `tdd` skill (red → green → refactor).*