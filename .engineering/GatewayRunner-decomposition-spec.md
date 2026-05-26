# GatewayRunner Decomposition Spec

## Status: Phase B Composition Slices Started

This document is the result of Phase 2 analysis. The mixin-extraction approach was abandoned because it moves code without reducing coupling. The composition architecture described below is the target design for a future rewrite. The current method-by-method owner map lives in `GatewayRunner-method-map.md`.

## Problem Statement

`GatewayRunner` in `gateway/run.py` is a 207-method, ~15,900-line God class. It handles platform I/O, session management, agent scheduling, kanban orchestration, progress streaming, voice mode, authentication, full-text search, slash commands, status checks, and telemetry — all with implicit mutable state shared across `self.*` attributes.

**Why this matters:**
- Every platform feature addition touches `run.py` (file is touched in ~80% of gateway-related PRs)
- No unit test coverage for gateway handlers (file is I/O-bound and stateful)
- Upstream `run.py` is also 207 methods / 15,814 lines — Nous has not solved this either

## Why Mixin Extraction Failed

Phase 2 attempted to extract handler clusters into mixin modules. AST analysis of all 207 methods showed:

| Cluster | Methods | Self attributes accessed* | Cross-cluster method calls |
|---|---|---|---|
| voice | 7 | 18 | 11 |
| kanban | 7 | 24 | 14 |
| session | 22 | 31 | 19 |
| agent_run | 10 | 27 | 16 |
| platform | 7 | 22 | 13 |
| send/delivery | 6 | 15 | 9 |

*Average per method, excluding common boilerplate (`self.logger`, `self.config`, etc.)

**The God class isn't an accidental aggregation — it's an imperative flowchart.** Every method reads/writes shared mutable state (sessions, adapters, agent cache, event loop) and cross-calls 5–10 peers. Moving methods to mixins preserves the spaghetti; it just spreads it across files.

## Proposed Architecture: Composed Services

Replace `class GatewayRunner` with a lightweight composition root:

```python
class GatewayRunner:
    def __init__(self, *, config, event_loop, credentials, profile):
        self._router = MessageRouter(config)
        self._sessions = SessionManager(config, credentials)
        self._agents = AgentRunner(config, profile)
        self._platforms = PlatformManager(config)
        self._delivery = DeliveryManager(config)
        self._commands = CommandRegistry(config)
        self._background = BackgroundServices(config)
        self._telemetry = TelemetryBus(config)
```

Each service owns its state. `GatewayRunner` only wires them and handles cross-cutting concerns (shutdown, health, restart).

### Service Decomposition

| Service | Owns | From Methods | Est. Lines |
|---|---|---|---|
| `MessageRouter` | Platform dispatch, auth gating, command parsing, thread routing | `handle_*`, `_is_user_authorized`, `_parse_command`, `_route_message` | ~1,800 |
| `SessionManager` | LRU cache, session lifecycle, expiry, re-binding, handoff watcher | `_session_*`, `_get_or_create_session`, `_is_session_run_current` | ~2,400 |
| `AgentRunner` | Agent construction, `_run_agent` monster, proxy mode, interruption | `_run_*`, `_build_agent`, `_proxy_*` | ~3,200 |
| `PlatformManager` | Adapter lifecycle, connect/reconnect/fatal, channel directory | `_connect_platform`, `_disconnect_platform`, `_get_mutable_adapter` | ~1,400 |
| `DeliveryManager` | Progress streaming, media delivery, chunking, notifications | `_send_*`, `_stream_*`, `_deliver_*`, `_notify_*` | ~2,600 |
| `CommandRegistry` | Slash command dispatch, argument parsing, help generation | `_handle_command`, `_cmd_*` | ~1,600 |
| `BackgroundServices` | Kanban watcher, pairing watcher, session expiry, voice mode TTL | `_watch_*`, `_expire_*`, `_kanban_*` | ~1,200 |
| `TelemetryBus` | Logging, metrics, status collection, gateway health | `_status_*`, `_log_*` | ~600 |

**GatewayRunner remaining:** ~500 lines (composition, init, `run()`, `_shutdown()`, `_restart()`, `_health_check()`).

### Interface Boundary Rules

1. **Services communicate via events, not direct method calls**
   ```python
   # Bad — preserves spaghetti
   self._agents.run(message, session=self._sessions.get(key))

   # Good — event bus decouples
   self._event_bus.emit("message.received", source=src, text=msg)
   ```

2. **GatewayRunner passes read-only views, never `self`**
   Services receive `config` snapshot at init. No `self._parent` back-references.

3. **State ownership is exclusive**
   `SessionManager` owns `sessions` dict. `AgentRunner` can read it (via `get_session(id)` API) but never write directly.

## Migration Path

This is **not** a single PR. The composition rewrite requires:

1. **Extract event bus** — add `gateway/event_bus.py` (32 lines, zero risk)
2. **Extract `MessageRouter`** — move `_parse_command` and `_route_message` first (they're relatively isolated)
3. **Parallel extraction** — each service can be developed as a standalone module and opt-in tested via feature flag
4. **Strangler fig pattern** — once a service is extracted, `GatewayRunner` method delegates to it (`return self._commands.handle(...)`)
5. **Final cutover** — when all services extracted, delete 15,000 lines from `run.py`

## What We Already Have

Local fork has composition slices that align with this spec:

| Module | Lines | Service it serves | From `run.py`? |
|---|---|---|---|
| `gateway/agent_runtime.py` | ~125 | `AgentRunner` | ✓ Yes — session model/runtime resolution and session-scoped model override application |
| `gateway/agent_runner.py` | ~370 | `AgentRunner` | ✓ Yes — per-turn runtime route construction, agent runtime config loading, model-switch intent detection, running-agent state release, cache signatures, running-agent snapshots, cached-agent turn initialization, and cache eviction/idle-sweep decisions |
| `gateway/agent_shutdown.py` | ~140 | `AgentRunner` | ✓ Yes — shutdown-time active-agent draining, shutdown interruption, finalization hooks, and agent resource cleanup |
| `gateway/agent_execution_runtime.py` | ~2,060 | `AgentRunner` | ✓ Yes — transitional owner for the `_run_agent` body: proxy delegation, agent construction, progress/status callbacks, streaming, approvals, interruption handling, queued follow-ups, and cleanup. Still must be split below the final 400-line object gate. |
| `gateway/message_agent_runtime.py` | ~1,120 | `AgentRunner` | ✓ Yes — transitional owner for the agent-turn message handler body: session creation, topic binding, context setup, transcript persistence, final response handling, and cleanup. Still must be split below the final 400-line object gate. |
| `gateway/stop_runtime.py` | ~375 | `GatewayRuntime` | ✓ Yes — stop/shutdown lifecycle sequencing, drain-time resume-pending markers, timeout interruption, adapter disconnect, cache teardown, clean-shutdown markers, and service-restart exit state |
| `gateway/restart_runtime.py` | ~250 | `GatewayRuntime` / `SessionManager` | ✓ Yes — restart failure counters, stuck-loop suspension, detached restart launch, and restart-interrupted session auto-resume scheduling |
| `gateway/session_handoff.py` | ~250 | `SessionManager` | ✓ Yes — pending CLI-to-gateway handoff polling and one-row handoff execution |
| `gateway/session_expiry.py` | ~190 | `SessionManager` | ✓ Yes — expired-session finalization, cached-agent cleanup, idle-agent sweep, and stale SessionStore pruning |
| `gateway/kanban_goal_continuation.py` | ~75 | `KanbanWatchers` | ✓ Yes — synthetic goal-continuation event detection and queue cleanup |
| `gateway/kanban_notifier.py` | ~420 | `KanbanWatchers` | ✓ Yes — Kanban terminal-event notifier, cursor advance/rewind, and subscription cleanup |
| `gateway/kanban_artifacts.py` | ~125 | `KanbanWatchers` | ✓ Yes — Kanban completion artifact uploads |
| `gateway/kanban_dispatcher.py` | ~435 | `KanbanWatchers` | ✓ Yes — embedded Kanban dispatcher loop, health telemetry, stale-task handling, and auto-decomposition |
| `gateway/agent_progress.py` | ~300 | `AgentRunner` | ✓ Yes — gateway tool-progress editable-bubble delivery, overflow rollover, dedup/reset events, stale-run draining, and cleanup message-id tracking |
| `gateway/background_task_runner.py` | ~310 | `AgentRunner` | ✓ Yes — `/background` task execution, runtime/toolset resolution, attachment vision enrichment, temporary agent cleanup, and completion delivery |
| `gateway/proxy_runner.py` | ~300 | `AgentRunner` (proxy mode) | ✓ Yes — `_run_agent_via_proxy` |
| `gateway/message_enrichment.py` | ~120 | `AgentRunner` | ✓ Yes — image input-mode routing and automatic vision enrichment of inbound image attachments |
| `gateway/message_authorization.py` | ~250 | `MessageRouter` | ✓ Yes — inbound source authorization, platform allow-all flags, allowlists, pairing approvals, bot/role bypasses, plugin auth envs, and WhatsApp alias expansion |
| `gateway/message_preparation.py` | ~255 | `MessageRouter` | ✓ Yes — inbound agent-turn text preparation, including shared sender attribution, channel context, native image buffering, voice/document notes, reply context, and `@` reference expansion |
| `gateway/process_watcher.py` | ~215 | `AgentRunner` | ✓ Yes — background process watcher notification policy, including running-output sends and agent completion injection |
| `gateway/voice_enrichment.py` | ~110 | `VoiceHandler` | ✓ Yes — STT-enabled transcription enrichment and disabled-STT voice attachment notes |
| `gateway/voice_mode_manager.py` | ~140 | `VoiceHandler` | ✓ Yes — voice-mode keying, persistence, and adapter auto-TTS state synchronization |
| `gateway/active_session_routing.py` | ~315 | `MessageRouter` | ✓ Yes — pending slash-confirm routing, unauthorized-DM behavior resolution, active-session command/follow-up policy, re-exported by `gateway/message_router.py` |
| `gateway/active_session_busy.py` | ~300 | `MessageRouter` | ✓ Yes — active-session busy follow-up auth, drain, queue/steer/interrupt, busy-ack, and onboarding policy |
| `gateway/message_dispatch_runtime.py` | ~1,105 | `MessageRouter` | ✓ Yes — transitional owner for the inbound message dispatch body: pre-dispatch hooks, auth, slash command routing, active-session queue/interrupt policy, pending sentinels, and handoff into the agent-turn runtime. Still must be split below the final 400-line object gate. |
| `gateway/message_router.py` | ~330 | `MessageRouter` | Partial — hosts the cold-command router prototype and re-exports active-session routing helpers for compatibility |
| `gateway/cold_command_router.py` | ~365 | `MessageRouter` (cold command path) | ✓ Yes — quick-command alias expansion/execution, cold dispatch metadata, command hook decisions, plugin command execution, skill/bundle invocation loading, unavailable/unknown command responses |
| `gateway/command_registry.py` | ~145 | `CommandRegistry` | ✓ Yes — cold-path explicit handler-map validation plus special `/new`, `/undo`, cold `/steer` command decisions, and the composed command-handler registry shim |
| `gateway/command_handlers/` | package | `CommandRegistry` | Transitional — slash-command handler bodies deleted from `run.py` and delegated through `GatewayCommandService`; all current command-handler modules are below the 400-line object gate |
| `gateway/command_handlers/core_*.py` | ~230-345 | `CommandRegistry` | ✓ Yes — core reset/help, status, agents, platform, restart, and stop command groups split behind `CoreGatewayCommands` |
| `gateway/command_handlers/model_switch.py` | ~370 | `CommandRegistry` | ✓ Yes — `/model` command parsing, session/global persistence, provider validation, and active-session model switch confirmation |
| `gateway/command_handlers/runtime_modes.py` | ~240 | `CommandRegistry` | ✓ Yes — Codex runtime, personality, and reasoning command handlers |
| `gateway/command_handlers/display_modes.py` | ~360 | `CommandRegistry` | ✓ Yes — fast/yolo/verbose/footer/compress display and behavior toggles |
| `gateway/command_handlers/operations_*.py` | ~235-300 | `CommandRegistry` | ✓ Yes — usage/diagnostics, MCP/skills/bundle reloads, and approval confirmation helpers split behind `OperationsGatewayCommands` |
| `gateway/command_handlers/session_*.py` | ~215 | `CommandRegistry` | ✓ Yes — retry/undo/home/rollback/title and background/resume/branch command groups split behind `SessionGatewayCommands` |
| `gateway/command_handlers/telegram_topic_*.py` | ~175-245 | `CommandRegistry` | ✓ Yes — Telegram topic setup/rename and topic command/restore groups split behind `TelegramTopicGatewayCommands` |
| `gateway/command_handlers/voice_*.py` | ~175-265 | `CommandRegistry` | ✓ Yes — voice control/channel commands and voice input/output delivery split behind `VoiceGatewayCommands` |
| `gateway/session_manager.py` | ~230 | `SessionManager` | ✓ Yes — session-key resolution, live `SessionSource` cache helpers, run-generation token helpers, session interruption/clear flow, session-boundary control-state cleanup, and cached-agent eviction |
| `gateway/session_info.py` | ~135 | `SessionManager` | ✓ Yes — current session/model info formatting for gateway commands, including provider, context length, and local endpoint display |
| `gateway/telegram_topic_manager.py` | ~350 | `DeliveryManager` | ✓ Yes — Telegram topic predicates, user guidance text, cooldown gates, binding persistence, thread recovery, title sanitization, auto-rename disable policy, root status rendering, and topic-mode disable semantics |
| `gateway/delivery_notifications.py` | ~375 | `DeliveryManager` | ✓ Yes — shutdown, post-update, restart-origin, and home-channel startup notification delivery decisions |
| `gateway/background_process_events.py` | ~135 | `DeliveryManager` | ✓ Yes — synthetic background-process event source resolution and watch-notification injection |
| `gateway/media_delivery.py` | ~170 | `DeliveryManager` | ✓ Yes — post-stream `MEDIA:`/local-file delivery, image batching, `[[as_document]]`, audio/document routing, and video dispatch |
| `gateway/platform_manager.py` | ~260 | `PlatformManager` | ✓ Yes — adapter connect/disconnect timeout helpers, runtime fatal-error handling, and failed-platform pause/resume state transitions |
| `gateway/platform_reconnect.py` | ~230 | `PlatformManager` | ✓ Yes — reconnect watcher loop and per-platform reconnect-attempt decisions, including handler rebinding, success/fatal/retry classification, backoff, and circuit-breaker pause triggers |
| `gateway/platform_factory.py` | ~260 | `PlatformManager` | ✓ Yes — adapter factory resolution, plugin-registry precedence, built-in adapter creation, and gateway-context injection |

Dead `run_helpers.py` and earlier command-handler attempts were intentionally removed. They moved code without a real ownership boundary and left `GatewayRunner` carrying the same complexity. The current `gateway/command_handlers/` package is a strangler slice because the original command bodies are deleted from `run.py` and command dispatch uses an explicit service map; current command service modules are below the final 400-line object gate.

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Upstream divergence | The spec is local-only. If upstream ever rewrites `run.py`, this becomes an artifact of local intent, not a merge plan. |
| Regression in complex flows | Each service must have an integration test harness before cutover. Start with `MessageRouter` (lowest risk, tested by every message). |
| Event bus performance | Use `asyncio.Queue` per subscriber. Benchmark with 1,000 msg/sec before accepting bus overhead. |

## Next Steps

1. **Install the event bus in the composition root** — `gateway/event_bus.py`
   exists as the foundation seam; wire it through constructed services before
   adding more cross-service imports.
2. **Pick one vertical slice** — the next slice must make one service own the
   end-to-end path it claims. Candidate: cold command routing and dispatch.
3. **Delete, don't wrap** — every migrated behavior must delete the original
   `GatewayRunner` body. Helper modules that leave ownership and dispatch on
   `GatewayRunner` do not count.
4. **Delete more GatewayRunner bodies** — `gateway/run.py` is now below the
   4,800-line gate, and command-handler modules are below the 400-line object
   rule. The next useful slice should either split the transitional
   `agent_execution_runtime.py` / `message_agent_runtime.py` /
   `message_dispatch_runtime.py` modules below the object gate or remove
   another cohesive body cluster from `gateway/run.py`.
5. **Decide** — if a decision requires broad runner state, leave it in place
   and update `GatewayRunner-method-map.md` with the coupling before extracting.

---
*Written 2026-05-24. Based on AST analysis of 207 methods across ~18,114 lines of `gateway/run.py`.*
