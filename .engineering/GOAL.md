# Goal: GatewayRunner Composition Refactor

## Problem
`gateway/run.py` contains an 18,000-line `GatewayRunner` class with 209 methods.
It is not testable, not modular, and not maintainable. Phase 2 (mixin extraction) was abandoned — the class is a single imperative flowchart with shared mutable state.

## Target Architecture
Decompose GatewayRunner into composed domain objects (NOT mixins):

1. `MessageRouter` — dispatch, auth, command parsing, prefix matching
2. `SessionManager` — session lifecycle, storage, expiry, metrics
3. `AgentRunner` — the `_run_agent` beast + proxy lifecycle + interruption handling
4. `PlatformManager` — adapter lifecycle, connect/reconnect, fatal errors, discovery
5. `DeliveryManager` — progress streaming, media delivery, notifications, reply anchors
6. `CommandRegistry` — all slash/prefix commands as registered callables
7. `VoiceHandler` — voice mode state machine, tts, speech-to-text routing
8. `KanbanWatchers` — goal monitors, task state polling, cron coordination

## Deliverables
- Phase A (spec): decomposition map linking every current method to new home
- Phase B (test): ring-fence GatewayRunner + write characterization tests
- Phase C (extract): one object at a time, preserve behavior
- Phase D (wire): GatewayRunner becomes thin composition root
- Phase E (delete): remove old methods once all callers migrated

## Constraints
- No breaking changes to Telegram/Discord API surface
- All existing commands must continue working
- Preserve proxy_runner integration (Codex, Anthropic, local agent)
- Don't touch message sending format or reply thread logic

## Success Criteria
- GatewayRunner < 500 lines
- Each composed object < 400 lines
- No method with > 20 line body (refactor or extract)
- Unit test coverage for each manager/router (at least characterization)
- `hermes gateway` starts and passes smoke test

## Hard Gates
- Next decomposition slice must reduce `gateway/run.py` below 12,000 lines.
- Future slices must delete migrated bodies from `GatewayRunner`; helper modules
  that leave ownership and dispatch on `GatewayRunner` do not count.
- Current structural test keeps `gateway/run.py` below 8,750 lines.
- Final cutover target remains `GatewayRunner` < 500 lines.

## Checkpoint: Phase A Ready
Phase A is ready when the decomposition spec and method map are both present:

- `GatewayRunner-decomposition-spec.md` defines the target services and migration rules.
- `GatewayRunner-method-map.md` assigns every current `GatewayRunner` method to a target owner.

## Checkpoint: Phase B Started
The first composition slices are in place:

- `tests/gateway/test_message_router_characterization.py` pins pending slash-confirm precedence.
- `gateway/agent_runtime.py` owns session model/runtime resolution and session-scoped model override application.
- `gateway/agent_runner.py` owns per-turn agent runtime route construction, agent runtime config loading, model-switch intent detection, running-agent state release, agent cache signatures, running-agent snapshots, cached-agent turn initialization, and cache eviction/idle-sweep decisions.
- `gateway/agent_shutdown.py` owns shutdown-time active-agent draining, shutdown interruption, finalization hooks, and agent resource cleanup.
- `gateway/stop_runtime.py` owns gateway stop/shutdown lifecycle sequencing, restart flags, drain timeout interruption, adapter disconnect, cache teardown, clean-shutdown markers, and service-restart exit state.
- `gateway/restart_runtime.py` owns restart failure-count persistence, stuck-loop suspension, detached restart command launching, and restart-interrupted session auto-resume scheduling.
- `gateway/session_handoff.py` owns pending CLI-to-gateway handoff polling and single-row handoff execution.
- `gateway/session_expiry.py` owns expired-session finalization, cached-agent cleanup, idle cache sweeping, and stale SessionStore pruning.
- `gateway/kanban_goal_continuation.py` owns synthetic goal-continuation queue detection and cleanup.
- `gateway/kanban_notifier.py` owns Kanban terminal-event notification polling, cursor advance/rewind, and subscription cleanup.
- `gateway/kanban_artifacts.py` owns Kanban completion artifact upload routing.
- `gateway/kanban_dispatcher.py` owns the embedded Kanban dispatcher watcher loop.
- `gateway/agent_progress.py` owns gateway tool-progress message delivery, including editable progress bubbles, overflow rollover, dedup/reset events, stale-run draining, and cleanup message-id tracking.
- `gateway/background_task_runner.py` owns `/background` task execution, including runtime/tool resolution, image enrichment, temporary agent construction, executor handoff, cleanup, and result/media delivery.
- `gateway/message_enrichment.py` owns image input-mode routing and automatic vision enrichment of inbound image attachments.
- `gateway/message_authorization.py` owns inbound source authorization, including platform allow-all flags, allowlists, pairing approvals, bot/role bypasses, plugin auth envs, and WhatsApp alias expansion.
- `gateway/message_preparation.py` owns inbound agent-turn text preparation, including shared sender attribution, channel context, native image buffering, voice/document notes, reply context, and `@` reference expansion.
- `gateway/process_watcher.py` owns background process watcher notification policy, including running-output sends and agent completion injection.
- `gateway/voice_enrichment.py` owns STT-enabled transcription enrichment and disabled-STT voice attachment notes.
- `gateway/voice_mode_manager.py` owns voice-mode keying, persistence, and adapter auto-TTS state synchronization.
- `gateway/active_session_routing.py` owns pending slash-confirm reply routing, unauthorized-DM behavior resolution, active-session command policy, and active-session follow-up policy; `gateway/message_router.py` re-exports that compatibility surface while hosting the cold-command router prototype.
- `gateway/active_session_busy.py` owns active-session busy follow-up handling, including auth drop, restart drain, queue/steer/interrupt side effects, busy-ack status text, debounce, thread reply metadata, and first-touch onboarding.
- `gateway/cold_command_router.py` owns quick-command alias expansion/execution, cold command dispatch metadata, command hook decisions, plugin command execution, skill/bundle invocation loading, and unavailable/unknown command responses.
- `gateway/command_registry.py` owns the cold-path gateway command handler table, explicit handler-map validation, and special `/new`, `/undo`, and cold `/steer` command decisions.
- `gateway/command_handlers/` is the current strangler package for slash-command handler bodies. `GatewayRunner` keeps compatibility wrappers and delegates through `GatewayCommandService`; all current command-handler modules are split below the 400-line composed-object gate.
- `gateway/session_manager.py` owns session-key resolution, live `SessionSource` cache helpers, run-generation token helpers, session interruption/clear flow, session-boundary control-state cleanup, and cached-agent eviction.
- `gateway/session_info.py` owns current session/model info formatting for gateway commands, including provider, context length, and local endpoint display.
- `gateway/telegram_topic_manager.py` owns Telegram topic-mode predicates, user guidance text, cooldown gates, topic binding persistence, topic-thread recovery, title sanitization, auto-rename disable policy, root status rendering, and topic-mode disable semantics.
- `gateway/delivery_notifications.py` owns shutdown, post-update, restart-origin, and home-channel startup notification delivery decisions.
- `gateway/background_process_events.py` owns synthetic background-process event source resolution and watch-notification injection.
- `gateway/media_delivery.py` owns post-stream `MEDIA:`/local-file attachment routing, including image batching, `[[as_document]]` preservation, audio-vs-document routing, and video delivery.
- `gateway/platform_manager.py` owns adapter connect/disconnect timeout helpers, runtime fatal-error handling, and failed-platform pause/resume state transitions.
- `gateway/platform_reconnect.py` owns the reconnect watcher loop and per-platform reconnect-attempt decisions, including handler rebinding, success/fatal/retry classification, backoff, and circuit-breaker pause triggers.
- `gateway/platform_factory.py` owns adapter factory resolution, plugin-registry precedence, built-in adapter creation, and adapter gateway-context injection.
- `GatewayRunner._handle_message` delegates the slash-confirm decision and no longer carries the body inline.
- `GatewayRunner._get_unauthorized_dm_behavior` is now a compatibility wrapper around the MessageRouter resolver.
- `GatewayRunner._is_user_authorized` is now a compatibility wrapper around the MessageRouter authorization helper.
- `GatewayRunner._prepare_inbound_message_text` is now a compatibility wrapper around the MessageRouter preparation helper.
- `GatewayRunner._session_key_for_source`, `_cache_session_source`, `_get_cached_session_source`, `_begin_session_run_generation`, `_invalidate_session_run_generation`, `_is_session_run_current`, `_interrupt_and_clear_session`, `_clear_session_boundary_security_state`, and `_evict_cached_agent` are compatibility wrappers around the SessionManager helpers.
- `GatewayRunner._format_session_info` is now a compatibility wrapper around the SessionManager session-info formatter.
- `GatewayRunner._resolve_session_agent_runtime`, `_resolve_turn_agent_config`, `_load_service_tier`, `_load_provider_routing`, `_load_fallback_model`, `_apply_session_model_override`, `_is_intentional_model_switch`, `_release_running_agent_state`, `_extract_cache_busting_config`, `_agent_config_signature`, `_snapshot_running_agents`, `_init_cached_agent_for_turn`, `_release_evicted_agent_soft`, `_enforce_agent_cache_cap`, and `_sweep_idle_cached_agents` are compatibility wrappers around AgentRunner helpers.
- `GatewayRunner._drain_active_agents`, `_interrupt_running_agents`, `_finalize_shutdown_agents`, and `_cleanup_agent_resources` are compatibility wrappers around AgentRunner shutdown helpers.
- `GatewayRunner.stop` is now a compatibility wrapper around the GatewayRuntime stop service.
- `GatewayRunner._run_agent` delegates gateway tool-progress delivery to the AgentRunner progress helper.
- `GatewayRunner._run_background_task` is a compatibility wrapper around the AgentRunner background-task helper.
- `GatewayRunner._decide_image_input_mode` and `_enrich_message_with_vision` are compatibility wrappers around AgentRunner message-enrichment helpers.
- `GatewayRunner._run_process_watcher` is a compatibility wrapper around the AgentRunner process-watcher helper.
- `GatewayRunner._enrich_message_with_transcription` is a compatibility wrapper around the VoiceHandler transcription-enrichment helper.
- `GatewayRunner._voice_key`, `_load_voice_modes`, `_save_voice_modes`, `_set_adapter_auto_tts_disabled`, `_set_adapter_auto_tts_enabled`, and `_sync_voice_mode_state_to_adapter` are compatibility wrappers around VoiceHandler voice-mode helpers.
- GatewayRunner's Telegram topic-mode lobby/lane helpers, reminder gates, binding persistence, thread recovery, title sanitization, auto-rename disable checks, topic help text, root status rendering, and topic-mode disable path are compatibility wrappers around the DeliveryManager topic helpers.
- `GatewayRunner._notify_active_sessions_of_shutdown`, `_send_update_notification`, `_send_restart_notification`, and `_send_home_channel_startup_notifications` are compatibility wrappers around DeliveryManager notification helpers.
- `GatewayRunner._build_process_event_source` and `_inject_watch_notification` are compatibility wrappers around DeliveryManager background-process event helpers.
- `GatewayRunner._deliver_media_from_response` is a compatibility wrapper around the DeliveryManager media-delivery helper.
- `GatewayRunner._safe_adapter_disconnect`, `_adapter_disconnect_timeout_secs`, `_platform_connect_timeout_secs`, `_connect_adapter_with_timeout`, `_handle_adapter_fatal_error`, `_pause_failed_platform`, and `_resume_paused_platform` are compatibility wrappers around PlatformManager lifecycle helpers.
- `GatewayRunner._platform_reconnect_watcher` is a compatibility wrapper around PlatformManager reconnect watcher helpers.
- `GatewayRunner._create_adapter` is a compatibility wrapper around the PlatformManager adapter factory helper.
- `GatewayRunner._handle_message` delegates running-agent slash command and follow-up classification before dispatching handlers or applying queue/steer/interrupt side effects.
- `GatewayRunner._handle_active_session_busy_message` is now a compatibility wrapper around the MessageRouter busy-follow-up helper.
- `GatewayRunner._handle_message` delegates cold-path quick-command expansion/execution, dispatch setup, command hook interpretation, plugin command execution, and skill/bundle invocation loading.
- `GatewayRunner._handle_message` delegates cold-path special command decisions and gateway command handler lookup to `gateway.command_registry`.
- `GatewayRunner._handle_message` now resolves command dispatch from `GatewayCommandService.handler_map()`, and the concrete slash-command bodies have been removed from `gateway/run.py`.
- The current `gateway/run.py` line count is below 8,750 lines; the final target remains below 500 lines.

Next task: move the next GatewayRunner body cluster out of `gateway/run.py` so the composition root keeps shrinking toward the final 500-line target.
