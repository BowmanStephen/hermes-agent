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
| `gateway/message_router.py` | ~330 | `MessageRouter` | Partial — hosts the cold-command router prototype and re-exports active-session routing helpers for compatibility |
| `gateway/cold_command_router.py` | ~365 | `MessageRouter` (cold command path) | ✓ Yes — quick-command alias expansion/execution, cold dispatch metadata, command hook decisions, plugin command execution, skill/bundle invocation loading, unavailable/unknown command responses |
| `gateway/command_registry.py` | ~125 | `CommandRegistry` | ✓ Yes — cold-path explicit handler-map validation plus special `/new`, `/undo`, and cold `/steer` command decisions |
| `gateway/session_manager.py` | ~230 | `SessionManager` | ✓ Yes — session-key resolution, live `SessionSource` cache helpers, run-generation token helpers, session interruption/clear flow, session-boundary control-state cleanup, and cached-agent eviction |
| `gateway/session_info.py` | ~135 | `SessionManager` | ✓ Yes — current session/model info formatting for gateway commands, including provider, context length, and local endpoint display |
| `gateway/telegram_topic_manager.py` | ~350 | `DeliveryManager` | ✓ Yes — Telegram topic predicates, user guidance text, cooldown gates, binding persistence, thread recovery, title sanitization, auto-rename disable policy, root status rendering, and topic-mode disable semantics |
| `gateway/delivery_notifications.py` | ~375 | `DeliveryManager` | ✓ Yes — shutdown, post-update, restart-origin, and home-channel startup notification delivery decisions |
| `gateway/background_process_events.py` | ~135 | `DeliveryManager` | ✓ Yes — synthetic background-process event source resolution and watch-notification injection |
| `gateway/media_delivery.py` | ~170 | `DeliveryManager` | ✓ Yes — post-stream `MEDIA:`/local-file delivery, image batching, `[[as_document]]`, audio/document routing, and video dispatch |
| `gateway/platform_manager.py` | ~260 | `PlatformManager` | ✓ Yes — adapter connect/disconnect timeout helpers, runtime fatal-error handling, and failed-platform pause/resume state transitions |
| `gateway/platform_reconnect.py` | ~230 | `PlatformManager` | ✓ Yes — reconnect watcher loop and per-platform reconnect-attempt decisions, including handler rebinding, success/fatal/retry classification, backoff, and circuit-breaker pause triggers |
| `gateway/platform_factory.py` | ~260 | `PlatformManager` | ✓ Yes — adapter factory resolution, plugin-registry precedence, built-in adapter creation, and gateway-context injection |

Dead `run_helpers.py` and `command_handlers.py` extractions were intentionally removed. They moved code without a real ownership boundary and left `GatewayRunner` carrying the same complexity. Future extractions must delete the original method body from `run.py`, provide an explicit dependency surface, and include characterization tests at the new seam.

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
4. **Meet the line-count gate** — the next slice must reduce
   `gateway/run.py` below 12,000 lines. Final cutover target remains
   `GatewayRunner` < 500 lines.
5. **Decide** — if a decision requires broad runner state, leave it in place
   and update `GatewayRunner-method-map.md` with the coupling before extracting.

---
*Written 2026-05-24. Based on AST analysis of 207 methods across ~18,114 lines of `gateway/run.py`.*
