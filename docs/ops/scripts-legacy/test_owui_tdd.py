> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/Users/stephenbowman/open-webui-venv-312/bin/python3
"""TDD test suite for Open WebUI startup on Python 3.12"""
import subprocess, time, os, sys, json, urllib.request, socket

def kill_owui():
    os.system("pkill -f 'open-webui serve' 2>/dev/null || true")
    time.sleep(1)

def test_1_startup():
    """Must start within 30s and respond to /health"""
    kill_owui()
    env = {**os.environ,
           "OLLAMA_BASE_URL": "http://localhost:11434",
           "OPEN_WEBUI_PORT": "3999",
           "OPEN_WEBUI_HOST": "127.0.0.1",
           "WEBUI_AUTH": "False",
           "RAG_EMBEDDING_ENGINE": "",
           "ENABLE_RAG": "False"}
    proc = subprocess.Popen(["open-webui", "serve"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for i in range(30):
            time.sleep(1)
            try:
                resp = urllib.request.urlopen("http://localhost:3999/health", timeout=2)
                assert resp.status == 200
                data = resp.read()
                assert b'"status":true' in data or b'{"status":true}' in data
                print("PASS TEST 1: Startup within 30s, /health OK")
                return True, ""
            except:
                if i == 29:
                    print("FAIL TEST 1: No startup within 30s")
                    return False, "timeout"
                continue
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill()

def test_2_port():
    """Must bind to TCP port"""
    kill_owui()
    env = {**os.environ, "OLLAMA_BASE_URL": "http://localhost:11434",
           "OPEN_WEBUI_PORT": "3998", "OPEN_WEBUI_HOST": "127.0.0.1", "WEBUI_AUTH": "False"}
    proc = subprocess.Popen(["open-webui", "serve"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(10)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("127.0.0.1", 3998))
        sock.close()
        if result == 0:
            print("PASS TEST 2: Port 3998 bound")
            return True, ""
        else:
            print("FAIL TEST 2: Port not bound")
            return False, str(result)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill()

def test_3_log():
    """Log must contain startup complete"""
    kill_owui()
    env = {**os.environ, "OLLAMA_BASE_URL": "http://localhost:11434",
           "OPEN_WEBUI_PORT": "3997", "OPEN_WEBUI_HOST": "127.0.0.1", "WEBUI_AUTH": "False"}
    proc = subprocess.Popen(["open-webui", "serve"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(15)
        out, _ = proc.communicate(timeout=1)
        if "Application startup complete" in (out or ""):
            print("PASS TEST 3: Startup complete found in log")
            return True, ""
        else:
            print("FAIL TEST 3: No startup complete in log")
            return False, "missing"
    except:
        proc.kill()
        return False, "exception"
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill()

def test_4_frontend():
    """GET / must return HTML"""
    kill_owui()
    env = {**os.environ, "OLLAMA_BASE_URL": "http://localhost:11434",
           "OPEN_WEBUI_PORT": "3996", "OPEN_WEBUI_HOST": "127.0.0.1", "WEBUI_AUTH": "False"}
    proc = subprocess.Popen(["open-webui", "serve"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(12)
        resp = urllib.request.urlopen("http://localhost:3996/", timeout=5)
        html = resp.read().decode()
        if "<!DOCTYPE html>" in html or "<html" in html[:500]:
            print("PASS TEST 4: Frontend returns HTML")
            return True, ""
        else:
            print("FAIL TEST 4: Not valid HTML")
            return False, html[:100]
    except Exception as e:
        print("FAIL TEST 4: " + str(e))
        return False, str(e)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill()

def test_5_api():
    """/api/models must return JSON"""
    kill_owui()
    env = {**os.environ, "OLLAMA_BASE_URL": "http://localhost:11434",
           "OPEN_WEBUI_PORT": "3995", "OPEN_WEBUI_HOST": "127.0.0.1", "WEBUI_AUTH": "False"}
    proc = subprocess.Popen(["open-webui", "serve"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(12)
        req = urllib.request.Request("http://localhost:3995/api/models")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        if "models" in data or "data" in data:
            print("PASS TEST 5: /api/models returns JSON")
            return True, ""
        else:
            print("FAIL TEST 5: Wrong structure")
            return False, str(list(data.keys()))
    except Exception as e:
        print("FAIL TEST 5: " + str(e))
        return False, str(e)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill()

if __name__ == "__main__":
    results = []
    print("=" * 50)
    print("Open WebUI TDD Test Suite - Python 3.12")
    print("=" * 50)

    results.append(("TEST 1: Startup", test_1_startup()))
    time.sleep(2)
    results.append(("TEST 2: Port", test_2_port()))
    time.sleep(2)
    results.append(("TEST 3: Log", test_3_log()))
    time.sleep(2)
    results.append(("TEST 4: Frontend", test_4_frontend()))
    time.sleep(2)
    results.append(("TEST 5: API", test_5_api()))

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    passed = 0
    for name, (ok, detail) in results:
        status = "PASS" if ok else "FAIL"
        print(status + ": " + name)
        if detail and not ok:
            print("      " + detail[:80])
        if ok:
            passed += 1

    print("\n" + str(passed) + "/" + str(len(results)) + " tests passed")
    sys.exit(0 if passed == len(results) else 1)
