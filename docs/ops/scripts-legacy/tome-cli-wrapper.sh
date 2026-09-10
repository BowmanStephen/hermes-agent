> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/bin/zsh
# tome-cli wrapper — activates shared venv then runs the script
source /Users/stephenbowman/ai-services/bin/activate
exec /Users/stephenbowman/ai-services/bin/python3 /Users/stephenbowman/tome-cli.py "$@"
