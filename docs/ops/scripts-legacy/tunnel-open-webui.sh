> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/bin/zsh
# SSH tunnel to Open WebUI on Mac Mini
# Run this from MacBook to access http://localhost:3001
ssh -N -L 3001:localhost:3001 stephenbowman@100.125.187.18
