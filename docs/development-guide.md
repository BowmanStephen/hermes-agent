# Development Reference

This document is reference material; the repository's short operating rules
are in [`AGENTS.md`](../AGENTS.md), and each subsystem has a local guide that
loads with the code being changed.

## Architecture map

| Component | Responsibility |
| --- | --- |
| `run_agent.py`, `agent/` | Conversation loop, provider calls, memory, compression, delegation. |
| `model_tools.py`, `tools/`, `toolsets.py` | Tool discovery, dispatch, and session-visible toolsets. |
| `cli.py`, `hermes_cli/` | Interactive CLI, configuration, setup, profiles, updater. |
| `gateway/` | Messaging platforms, session routing, lifecycle, streaming. |
| `ui-tui/`, `tui_gateway/` | Ink terminal experience and its Python JSON-RPC backend. |
| `apps/desktop/` | Electron-native chat surface. |
| `plugins/`, `skills/`, `optional-skills/` | Extensible capabilities and operating instructions. |
| `cron/` | Scheduled work. |

## Local setup

Prefer the checkout's `.venv`, falling back to `venv`. Use the project scripts
for Python tests and the applicable package scripts for TypeScript packages.
Configuration is profile-scoped under `HERMES_HOME`; `config.yaml` holds
behavioral settings and `.env` holds credentials.

## Source of detailed guidance

The following guides are intentionally colocated so that subsystem rules are
present when an agent works in that directory:

- [`agent/AGENTS.md`](../agent/AGENTS.md)
- [`gateway/AGENTS.md`](../gateway/AGENTS.md)
- [`hermes_cli/AGENTS.md`](../hermes_cli/AGENTS.md)
- [`tools/AGENTS.md`](../tools/AGENTS.md)
- [`plugins/AGENTS.md`](../plugins/AGENTS.md)
- [`skills/AGENTS.md`](../skills/AGENTS.md)
- [`optional-skills/AGENTS.md`](../optional-skills/AGENTS.md)
- [`cron/AGENTS.md`](../cron/AGENTS.md)
- [`ui-tui/AGENTS.md`](../ui-tui/AGENTS.md)
- [`tui_gateway/AGENTS.md`](../tui_gateway/AGENTS.md)
- [`tests/AGENTS.md`](../tests/AGENTS.md)

For end-user and contributor documentation, use the website documentation and
`CONTRIBUTING.md`. Keep operational rules out of broad reference documents
when a local `AGENTS.md` can state them nearer to the implementation.
