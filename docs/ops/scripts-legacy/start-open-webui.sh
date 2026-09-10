> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/bin/zsh
# Open WebUI startup script

source /Users/stephenbowman/open-webui-venv-312/bin/activate

export DATA_DIR=/Users/stephenbowman/.open-webui
export DATABASE_URL=sqlite:///$DATA_DIR/webui.db
export OLLAMA_BASE_URL=http://localhost:11434
export OPEN_WEBUI_PORT=3001
export OPEN_WEBUI_HOST=127.0.0.1
export WEBUI_AUTH=True
export FRONTEND_BUILD_DIR=/Users/stephenbowman/open-webui-venv-312/lib/python3.12/site-packages/open_webui/frontend
export WEBUI_SECRET_KEY=/Users/stephenbowman/.webui_secret_key
export UVICORN_WORKERS=1
export GLOBAL_LOG_LEVEL=INFO

mkdir -p $DATA_DIR

if [ ! -f /Users/stephenbowman/.webui_secret_key ]; then
    openssl rand -base64 12 > /Users/stephenbowman/.webui_secret_key
fi

exec uvicorn open_webui.main:app     --host $OPEN_WEBUI_HOST     --port $OPEN_WEBUI_PORT     --workers $UVICORN_WORKERS     --forwarded-allow-ips '127.0.0.1'
# Updated to port 3001 to avoid node.js conflict
