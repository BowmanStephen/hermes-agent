> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/Users/stephenbowman/open-webui-venv-312/bin/python3
"""TDD test suite for Open WebUI - localhost tests"""
import subprocess, time, os, sys, json, urllib.request, socket

PORT = 3001
BASE = f"http://localhost:{PORT}"

def test_1_startup():
    """Must respond to /health within 15s"""
    for i in range(15):
        try:
            resp = urllib.request.urlopen(f"{BASE}/health", timeout=2)
            assert resp.status == 200
            data = resp.read()
            assert b'"status":true' in data or b'{"status":true}' in data
            print("PASS: Startup - /health returns 200")
            return True
        except:
            time.sleep(1)
    print("FAIL: Startup - /health not responding within 15s")
    return False

def test_2_port():
    """Must bind to TCP port"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", PORT))
    sock.close()
    if result == 0:
        print("PASS: Port bound")
        return True
    else:
        print(f"FAIL: Port not bound ({result})")
        return False

def test_3_frontend():
    """GET / must return HTML"""
    try:
        resp = urllib.request.urlopen(f"{BASE}/", timeout=5)
        html = resp.read().decode()
        if "<!DOCTYPE html>" in html or "<html" in html[:500]:
            print("PASS: Frontend returns HTML")
            return True
        else:
            print("FAIL: Not valid HTML")
            return False
    except Exception as e:
        print(f"FAIL: {e}")
        return False

def test_4_api_config():
    """/api/config must return JSON with status"""
    try:
        req = urllib.request.Request(f"{BASE}/api/config")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        if "status" in data:
            print(f"PASS: API config OK (version={data.get('version','?')})")
            return True
        else:
            print("FAIL: No status in config")
            return False
    except Exception as e:
        print(f"FAIL: {e}")
        return False

def test_5_static():
    """Static files must be served"""
    try:
        resp = urllib.request.urlopen(f"{BASE}/static/favicon.png", timeout=5)
        if resp.status == 200 and len(resp.read()) > 100:
            print("PASS: Static files served")
            return True
        else:
            print("FAIL: Static file issue")
            return False
    except Exception as e:
        print(f"FAIL: {e}")
        return False

def test_6_ollama_proxy():
    """/ollama/api/tags must return models (proxies to Ollama)"""
    try:
        req = urllib.request.Request(f"{BASE}/ollama/api/tags")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        if "models" in data:
            print(f"PASS: Ollama proxy works ({len(data['models'])} models)")
            return True
        else:
            print("FAIL: No models in Ollama response")
            return False
    except Exception as e:
        print(f"FAIL: {e}")
        return False

if __name__ == "__main__":
    print("="*50)
    print("Open WebUI TDD Test Suite")
    print(f"Target: {BASE}")
    print("="*50)

    results = [
        ("TEST 1: Startup/Health", test_1_startup()),
        ("TEST 2: Port Binding", test_2_port()),
        ("TEST 3: Frontend HTML", test_3_frontend()),
        ("TEST 4: API Config", test_4_api_config()),
        ("TEST 5: Static Files", test_5_static()),
        ("TEST 6: Ollama Proxy", test_6_ollama_proxy()),
    ]

    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(("PASS" if ok else "FAIL") + ": " + name)
    print(f"\n{passed}/{len(results)} tests passed")
    sys.exit(0 if passed == len(results) else 1)
