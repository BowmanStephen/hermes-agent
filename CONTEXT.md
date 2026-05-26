# Domain Context — hermes-agent (Dale fork)

> Lazily created during an architecture-deepening review (2026-05-23). Records
> domain vocabulary so future reviews use consistent names. Add terms here as
> deepened modules are named.

## Sessions

- **session id** — the canonical, stable identifier for a stored conversation
  (e.g. `cron_22593f12f387_20260523_120048`, `api-96771d87aa5c34f8`). What the
  agent loads/resumes against.

- **session reference** — any user-supplied way of pointing at a session: a
  session id, a human title ("Praising Previous Work #3"), the sentinel
  "last" (most recent for a given source), or an interactive pick. Resolving a
  *session reference* to a *session id* is the job of the **SessionResolver**.

- **SessionResolver** — deep module that turns a *session reference* into a
  *session id*. Takes an injected **SessionStore** so resolution logic is
  testable against a fake in-memory store (no filesystem). Replaces the
  `_resolve_session_by_name_or_id` / `_resolve_last_session` /
  `_session_browse_picker` / `_read_tui_active_session_file` helpers currently
  private to `hermes_cli/main.py`. Callers (`main.py`, `commands/chat.py`,
  root `cli.py`) depend *down* on it rather than reaching up into main.py.

- **SessionStore** — seam over session persistence (`~/.hermes/sessions`).
  Real adapter reads the filesystem; fake adapter holds an in-memory list for
  tests. Interface: list sessions (id, title, source, last-active) and read the
  TUI active-session marker file.

- **session source** — "cli" or "tui"; the surface a session was created from.
  `resolve_last` is source-aware and falls back cli→tui when no match.

## Auxiliary client

- **auxiliary client router** — `agent/auxiliary_client.py`. Resolves the best
  available LLM backend for *side tasks* (context compression, session search,
  web extraction, vision, browser vision) via an auto-detection fallback chain,
  then calls it through `call_llm`. Deep public interface; sprawling internals.

- **resolution chain** — the ordered provider auto-detection (main provider →
  OpenRouter → Nous → custom endpoint → native Anthropic → direct-key providers)
  plus the HTTP-402/credit-exhaustion retry. Stays in the router; this is policy.

- **CompletionsAdapter** — seam that translates a provider-specific API (Codex
  OAuth, Anthropic Messages) into the OpenAI chat-completions shape the chain
  expects. Minimal interface: `create(**kwargs) -> response` (+ async). Two
  concrete adapters (Codex, Anthropic) = a real seam.

- **provider adapter** — a concrete CompletionsAdapter plus its ChatShim and
  public Client wrapper, sync and async. To be extracted from the router into
  `agent/auxiliary_adapters/{codex,anthropic}.py` over a shared `base.py`.

## Gateway runner

- **GatewayRunner** — `gateway/run.py`. The single 210-method class that runs
  the messaging gateway: connects platform adapters, routes messages, manages
  per-session runtime, drains/queues during shutdown, tracks exit state. A
  god-object; clusters share mutable `self` state, so decomposition is per-
  cluster design work, not a mechanical lift.

- **voice mode** — per-chat TTS preference ("off" | "voice_only" | "all"),
  keyed by platform+chat_id, persisted to `~/.hermes/gateway_voice_mode.json`.

- **VoiceModeManager** — first deep-module slice extracted from GatewayRunner:
  owns the voice-mode map, its JSON persistence, and adapter TTS-flag sync,
  behind `get` / `set(key, mode, adapter)` / `sync_to_adapter`. Removes the
  mutate→persist→sync triad currently duplicated at ~6 command sites.

## Fork integration (BowmanStephen/hermes-agent)

- **upstream** — `NousResearch/hermes-agent` (`origin` remote). Source of upstream
  features; not where Dale fork work lands first.
_Avoid_: origin (when meaning “my fork”), main repo

- **fork remote** — `BowmanStephen/hermes-agent` (`fork` remote). Stephen’s GitHub
  copy; default branch `main`; target for pushes and pull requests.
_Avoid_: GitHub, my repo (ambiguous)

- **integration branch** — `feat/gateway-event-bus` on the fork remote. All
  gateway-decomposition work lands here until deliberately merged into fork
  `main`. Agents must not treat `codex/Consolidation` or fork `main` as the
  active timeline without an explicit decision.
_Avoid_: main (until integration is deliberate), codex/Consolidation (parallel
  refactor line)

- **pull request** — draft PR on the fork merges `feat/gateway-event-bus` into
  fork `main` when gateway smoke-test passes. Opening a PR is not the same as
  merging; agents keep committing to the integration branch until merge.
_Avoid_: merge (when meaning “open a PR”), PR merge (when meaning local git merge)

- **local merge** — combining an integration branch into another branch on the
  Mac with `git merge`, with or without a pull request.
_Avoid_: syncing, updating GitHub
