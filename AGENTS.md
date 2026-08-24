# Hermes Agent: Operating Rules

Hermes is a personal AI agent with one core shared by the CLI, gateway, TUI,
and Desktop. It learns across sessions, delegates work, and is extended mainly
through plugins and skills.

## Non-negotiable invariants

- Preserve a conversation's cached prefix. Do not mutate earlier context,
  change its toolset, or rebuild its system prompt mid-conversation; context
  compression is the sole exception. System-prompt-changing commands must
  apply next session by default and offer an explicit immediate option.
- Keep the core narrow. Every core model tool is sent on every API call.
  Prefer extending existing code, then a CLI command plus skill, a gated tool,
  a plugin, or an MCP server. Add a core tool only when it is fundamental and
  cannot be reached otherwise.
- Put non-secret settings in `config.yaml`; `.env` is for credentials only.
- Make all user state profile-safe. Use `get_hermes_home()` for stored state
  and `display_hermes_home()` in messages—never hard-code `~/.hermes`.
- Verify real behavior, especially at config, profile, tool, remote-backend,
  and I/O boundaries. Do not wire unused code into production without an
  end-to-end path test.
- Favor behavioral contracts and invariants over catalog snapshots, source-text
  tests, and exact counts that routine updates should change.
- Do not add speculative hooks or extension frameworks. Widen a generic
  boundary only for a real consumer, and keep third-party product integrations
  as standalone plugins rather than new in-tree plugins.

## Working conventions

- Trace a reported bug to the current code and verify the original design
  intent before changing it. Fix the entire reachable bug class, not only the
  reported call site.
- Before adding a module, command, provider, or hook, search for the existing
  ownership point and extend it. Keep state and policies with their authority.
- Preserve contributor authorship when salvaging external work. Before a
  squash merge, update the branch from `main` and inspect the resulting diff.
- Pin dependencies: PyPI packages need an upper bound, Git dependencies and
  GitHub Actions need immutable SHAs, and lockfile changes must accompany
  dependency changes.

## Tests and local setup

Activate the checkout's `.venv` (or `venv`) when needed. Run Python tests only
through `scripts/run_tests.sh`; it provides the hermetic, per-file isolation
used in CI. The detailed test rules are in [`tests/AGENTS.md`](tests/AGENTS.md).

## Read the local guide before editing a subsystem

| Area | Local guide |
| --- | --- |
| Agent loop, memory, delegation | [`agent/AGENTS.md`](agent/AGENTS.md) |
| Gateway, sessions, platforms, streaming | [`gateway/AGENTS.md`](gateway/AGENTS.md) |
| CLI, config, profiles, updater | [`hermes_cli/AGENTS.md`](hermes_cli/AGENTS.md) |
| Built-in tools and toolsets | [`tools/AGENTS.md`](tools/AGENTS.md) |
| Plugins and providers | [`plugins/AGENTS.md`](plugins/AGENTS.md) |
| Built-in skills | [`skills/AGENTS.md`](skills/AGENTS.md) |
| Optional skills | [`optional-skills/AGENTS.md`](optional-skills/AGENTS.md) |
| Scheduler jobs | [`cron/AGENTS.md`](cron/AGENTS.md) |
| Ink TUI | [`ui-tui/AGENTS.md`](ui-tui/AGENTS.md) |
| TUI gateway | [`tui_gateway/AGENTS.md`](tui_gateway/AGENTS.md) |
| Desktop | [`apps/desktop/AGENTS.md`](apps/desktop/AGENTS.md) |
| Tests | [`tests/AGENTS.md`](tests/AGENTS.md) |

The architecture overview and non-local reference material live in
[`docs/development-guide.md`](docs/development-guide.md). The filesystem is
the source of truth when this map becomes stale.
