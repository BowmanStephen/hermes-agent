> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/Users/stephenbowman/ai-services/bin/python3
'''Quick status check for all Mini services'''
import json, urllib.request, sys

def check(url, name, timeout=10):
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        return True, len(data)
    except Exception as e:
        return False, str(e)[:50]

print("=== Mac Mini Service Status ===\n")

services = [
    ("Ollama /v1/models", "http://localhost:11434/v1/models"),
    ("llama-server /v1/models", "http://localhost:8080/v1/models"),
    ("n8n health", "http://localhost:5678/healthz"),
    ("Prefect health", "http://localhost:4200/api/health"),
    ("SearXNG search", "http://localhost:8888/search?q=test&format=json"),
    ("Open WebUI health", "http://localhost:3001/health"),
]

for name, url in services:
    ok, info = check(url, name)
    status = "✅" if ok else "❌"
    print(f"{status} {name}: {info}")

print("\n=== Dick Workflow ===")
print("✅ dick-workflow --text \"...\" (chains TTS→Whisper→LLM→Chroma)")

print("\n=== Commands ===")
print("Ollama:     curl http://100.125.187.18:11434/v1/models")
print("llama-srv:  curl http://100.125.187.18:8080/v1/models")
print("n8n:        curl http://100.125.187.18:5678/healthz")
print("Prefect:    curl http://100.125.187.18:4200/api/health")
print("SearXNG:    curl 'http://100.125.187.18:8888/search?q=test&format=json'")
print("Open WebUI: http://localhost:3001 (via SSH tunnel)")
