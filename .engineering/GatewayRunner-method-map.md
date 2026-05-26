# GatewayRunner Method Map

Generated from the current `gateway/run.py` after the proxy-runner, router,
agent-runner, command-registry, session-manager, Telegram-topic, voice-mode,
platform-lifecycle/fatal-error, reconnect-watcher, adapter-factory,
source-authorization, inbound-message-preparation, active-session-busy,
agent-shutdown, stop-runtime, agent-progress, background-task, and session-info
extractions.
This is the Phase A decomposition checklist: every `GatewayRunner` method has a
target owner before further extraction work starts.

## Target Owners

| Owner | Responsibility |
|---|---|
| `GatewayRuntime` | Composition root, startup/shutdown, process/runtime status, config helpers that do not belong to a narrower service. |
| `MessageRouter` | Inbound dispatch, auth gating, busy/queue decisions, command-vs-agent routing. |
| `SessionManager` | Session keys, persisted session state, source cache, generation guards, agent cache lifecycle. |
| `AgentRunner` | Agent construction/execution, proxy runner, model/reasoning runtime config, process watchers, executor calls. |
| `PlatformManager` | Adapter creation, connect/reconnect/disconnect, fatal platform state. |
| `DeliveryManager` | Platform notices, message delivery metadata, Telegram topics, media, startup/update/restart notifications. |
| `CommandRegistry` | Slash commands and slash-command confirmation/access control. |
| `VoiceHandler` | Voice mode, voice channel input/output, transcription/TTS routing. |
| `KanbanWatchers` | Goal/subgoal commands, goal continuations, kanban dispatch/notifier flows. |

## Extracted Slices

| Extracted symbol | Module | Target owner | Notes |
|---|---|---|---|
| `resolve_session_agent_runtime` | `gateway/agent_runtime.py` | `AgentRunner` | Resolves session-scoped model/runtime selection, fast-path complete provider overrides, runtime-supplied model overrides, and provider default model fallback. |
| `resolve_turn_agent_config` | `gateway/agent_runner.py` | `AgentRunner` | Builds the per-turn runtime route, cache signature, and optional priority-processing request overrides. |
| `load_service_tier` | `gateway/agent_runner.py` | `AgentRunner` | Loads and normalizes Priority Processing config from `agent.service_tier`. |
| `load_provider_routing` | `gateway/agent_runner.py` | `AgentRunner` | Loads OpenRouter provider routing preferences. |
| `load_fallback_model` | `gateway/agent_runner.py` | `AgentRunner` | Loads fallback provider chain config. |
| `apply_session_model_override` | `gateway/agent_runtime.py` | `AgentRunner` | Applies session-scoped `/model` overrides to model/runtime kwargs while preserving `None` defaults. |
| `extract_cache_busting_config` | `gateway/agent_runner.py` | `AgentRunner` | Extracts config values and tool registry generation that should invalidate cached agents. |
| `compute_agent_config_signature` | `gateway/agent_runner.py` | `AgentRunner` | Computes the stable cached-agent signature using full credential fingerprints and cache-busting keys. |
| `snapshot_running_agents` | `gateway/agent_runner.py` | `AgentRunner` | Builds a running-agent snapshot excluding setup-pending sentinels. |
| `init_cached_agent_for_turn` | `gateway/agent_runner.py` | `AgentRunner` | Resets cached-agent per-turn activity state while preserving interrupt-recursive idle markers. |
| `is_intentional_model_switch` | `gateway/agent_runner.py` | `AgentRunner` | Detects whether an observed agent model matches a session-scoped `/model` override. |
| `release_running_agent_state` | `gateway/agent_runner.py` | `AgentRunner` | Clears per-running-agent dictionaries with optional run-generation ownership guard. |
| `release_evicted_agent_soft` | `gateway/agent_runner.py` | `AgentRunner` | Soft-cleans cache-evicted agents without tearing down resumable session tool state. |
| `enforce_agent_cache_cap` | `gateway/agent_runner.py` | `AgentRunner` | Evicts excess LRU cached agents while skipping active mid-turn agents. |
| `sweep_idle_cached_agents` | `gateway/agent_runner.py` | `AgentRunner` | Evicts idle cached agents past TTL while skipping active mid-turn agents. |
| `drain_active_agents` | `gateway/agent_shutdown.py` | `AgentRunner` | Waits for active agents to finish during shutdown while updating draining runtime status and returning the initial active-agent snapshot. |
| `interrupt_running_agents` | `gateway/agent_shutdown.py` | `AgentRunner` | Best-effort interrupts non-pending running agents during shutdown without aborting the shutdown path. |
| `finalize_shutdown_agents` | `gateway/agent_shutdown.py` | `AgentRunner` | Emits final session hooks and cleans active agents at shutdown. |
| `cleanup_agent_resources` | `gateway/agent_shutdown.py` | `AgentRunner` | Shuts down memory providers with transcript context, closes agent resources, and reaps stale auxiliary async clients. |
| `GatewayStopRuntime.stop` | `gateway/stop_runtime.py` | `GatewayRuntime` | Owns the gateway stop/shutdown lifecycle, including restart flag capture, drain timeout interruption, adapter disconnect, cache teardown, clean-shutdown markers, and service-restart exit state. |
| `increment_restart_failure_counts` | `gateway/restart_runtime.py` | `GatewayRuntime` | Persists restart-loop failure counters with the existing atomic-write seam. |
| `suspend_stuck_loop_sessions` | `gateway/restart_runtime.py` | `SessionManager` | Suspends sessions that were active across too many restarts and clears the failure counter file. |
| `clear_restart_failure_count` | `gateway/restart_runtime.py` | `GatewayRuntime` | Clears the persisted restart-loop counter for a session after successful completion. |
| `launch_detached_restart_command` | `gateway/restart_runtime.py` | `GatewayRuntime` | Launches detached gateway restart watchers for POSIX and Windows while preserving existing process-spawn behavior. |
| `schedule_resume_pending_sessions` | `gateway/restart_runtime.py` | `SessionManager` | Synthesizes empty internal turns for fresh restart-interrupted sessions after adapters reconnect. |
| `SessionHandoffWatcher._handoff_watcher` | `gateway/session_handoff.py` | `SessionManager` | Polls pending CLI-to-gateway handoff rows and marks each claimed row completed or failed. |
| `SessionHandoffWatcher._process_handoff` | `gateway/session_handoff.py` | `SessionManager` | Resolves the destination home channel/thread, rebinds the SessionStore key, evicts stale agents, dispatches the synthetic handoff turn, and sends the result. |
| `SessionExpiryWatcher._session_expiry_watcher` | `gateway/session_expiry.py` | `SessionManager` | Finalizes expired sessions, invokes finalization hooks, cleans cached agents, sweeps idle agents, and prunes stale SessionStore entries. |
| `KanbanGoalContinuation._is_goal_continuation_event` | `gateway/kanban_goal_continuation.py` | `KanbanWatchers` | Detects synthetic queued `/goal` continuation turns. |
| `KanbanGoalContinuation._clear_goal_pending_continuations` | `gateway/kanban_goal_continuation.py` | `KanbanWatchers` | Removes synthetic goal continuation events from the pending slot and queue overflow while preserving user queued events. |
| `KanbanGoalContinuation._goal_still_active_for_session` | `gateway/kanban_goal_continuation.py` | `KanbanWatchers` | Performs a fresh GoalManager active-state check before running a queued continuation. |
| `KanbanNotifierWatcher._kanban_notifier_watcher` | `gateway/kanban_notifier.py` | `KanbanWatchers` | Polls Kanban terminal events across boards and delivers chat notifications without blocking the gateway loop. |
| `KanbanNotifierWatcher._kanban_advance`, `_kanban_unsub`, `_kanban_rewind` | `gateway/kanban_notifier.py` | `KanbanWatchers` | Own notifier cursor advance, subscription removal, and claim rewind helpers. |
| `KanbanArtifactDelivery._deliver_kanban_artifacts` | `gateway/kanban_artifacts.py` | `KanbanWatchers` | Uploads local files referenced by Kanban completion summaries as native images, videos, or documents. |
| `KanbanDispatcherWatcher._kanban_dispatcher_watcher` | `gateway/kanban_dispatcher.py` | `KanbanWatchers` | Hosts the embedded Kanban dispatcher loop, auto-decomposition, spawn health telemetry, and corrupt-board handling. |
| `send_agent_progress_messages` | `gateway/agent_progress.py` | `AgentRunner` | Drains gateway tool-progress events into editable platform messages with overflow rollover, dedup/reset handling, stale-run draining, and cleanup tracking. |
| `resolve_image_input_mode` | `gateway/message_enrichment.py` | `AgentRunner` | Resolves whether inbound images should attach natively or be pre-analyzed as text for the active model. |
| `enrich_message_with_vision` | `gateway/message_enrichment.py` | `AgentRunner` | Runs vision analysis for inbound image attachments and prepends sanitized descriptions plus re-examination paths. |
| `run_process_watcher` | `gateway/process_watcher.py` | `AgentRunner` | Watches background process output, sends user-facing progress/final notifications, and injects agent completion events. |
| `run_background_task` | `gateway/background_task_runner.py` | `AgentRunner` | Executes `/background` tasks with explicit dependencies, temporary agent lifecycle, attachment enrichment, and completion delivery. |
| `enrich_message_with_transcription` | `gateway/voice_enrichment.py` | `VoiceHandler` | Builds inbound voice transcript notes or disabled-STT voice attachment context for the agent turn. |
| `voice_mode_key` | `gateway/voice_mode_manager.py` | `VoiceHandler` | Builds platform-namespaced persisted voice-mode keys. |
| `load_voice_modes` | `gateway/voice_mode_manager.py` | `VoiceHandler` | Loads persisted voice modes while filtering invalid values and legacy unprefixed keys. |
| `save_voice_modes` | `gateway/voice_mode_manager.py` | `VoiceHandler` | Persists voice-mode state to the profile gateway voice-mode file. |
| `set_adapter_auto_tts_disabled` | `gateway/voice_mode_manager.py` | `VoiceHandler` | Updates adapter auto-TTS suppression state and clears conflicting opt-ins. |
| `set_adapter_auto_tts_enabled` | `gateway/voice_mode_manager.py` | `VoiceHandler` | Updates adapter explicit auto-TTS opt-ins and clears conflicting disables. |
| `sync_voice_mode_state_to_adapter` | `gateway/voice_mode_manager.py` | `VoiceHandler` | Restores persisted voice-mode state and global auto-TTS defaults onto live adapters. |
| `adapter_disconnect_timeout_secs` | `gateway/platform_manager.py` | `PlatformManager` | Loads the defensive adapter disconnect timeout from environment with fallback defaults. |
| `platform_connect_timeout_secs` | `gateway/platform_manager.py` | `PlatformManager` | Loads the per-platform connect timeout from environment with fallback defaults. |
| `safe_adapter_disconnect` | `gateway/platform_manager.py` | `PlatformManager` | Disconnects partially initialized adapters defensively without propagating cleanup errors. |
| `connect_adapter_with_timeout` | `gateway/platform_manager.py` | `PlatformManager` | Bounds adapter connect calls and raises platform-specific timeout errors. |
| `handle_adapter_fatal_error` | `gateway/platform_manager.py` | `PlatformManager` | Handles runtime adapter fatal errors, reconnect queueing, delivery-router adapter updates, and stop decisions. |
| `pause_failed_platform` | `gateway/platform_manager.py` | `PlatformManager` | Marks queued retry platforms paused, updates runtime status, and preserves the retry entry. |
| `resume_paused_platform` | `gateway/platform_manager.py` | `PlatformManager` | Clears paused state, resets attempts, and schedules an immediate reconnect retry. |
| `run_platform_reconnect_watcher` | `gateway/platform_reconnect.py` | `PlatformManager` | Owns the reconnect watcher loop, initial delay, idle delay, active delay, and retry snapshot iteration. |
| `retry_failed_platform` | `gateway/platform_reconnect.py` | `PlatformManager` | Performs a due per-platform reconnect attempt, including adapter handler binding, success/fatal/retry classification, backoff, and circuit-breaker pause triggers. |
| `create_platform_adapter` | `gateway/platform_factory.py` | `PlatformManager` | Resolves plugin-registered adapters before built-ins, applies gateway session defaults, and injects gateway context into adapters that require it. |
| `resolve_source_authorization` | `gateway/message_authorization.py` | `MessageRouter` | Owns inbound source authorization across platform allowlists, allow-all flags, pairing approvals, bot/role bypasses, plugin auth envs, and WhatsApp aliases. |
| `prepare_inbound_message_text` | `gateway/message_preparation.py` | `MessageRouter` | Prepares agent-turn text with sender attribution, channel context, media/voice/document notes, native image buffering, reply context, and `@` reference expansion. |
| `route_pending_slash_confirm_reply` | `gateway/active_session_routing.py` | `MessageRouter` | Handles pending slash-confirm replies before normal slash command dispatch while preserving tool-approval precedence; re-exported by `gateway.message_router`. |
| `resolve_unauthorized_dm_behavior` | `gateway/active_session_routing.py` | `MessageRouter` | Resolves explicit config, allowlist-aware defaults, and pairing fallback for unauthorized direct messages. |
| `resolve_active_session_command_decision` | `gateway/active_session_routing.py` | `MessageRouter` | Classifies slash commands received while an agent is already running before the runner dispatches concrete handlers. |
| `should_queue_telegram_followup` | `gateway/active_session_routing.py` | `MessageRouter` | Owns the Telegram text follow-up grace predicate for active sessions. |
| `handle_active_session_busy_message` | `gateway/active_session_busy.py` | `MessageRouter` | Owns active-session busy follow-up auth, restart drain behavior, queue/steer/interrupt side effects, status-rich busy acknowledgments, debounce, and onboarding hints. |
| `record_telegram_topic_binding` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Persists Telegram topic-to-Hermes-session bindings for topic lanes. |
| `recover_telegram_topic_thread_id` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Rewrites missing, General, or unknown Telegram DM topic IDs to the user's latest known topic. |
| `sanitize_telegram_topic_title` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Normalizes generated session titles into Bot API-safe Telegram topic names. |
| `telegram_topic_auto_rename_disabled` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Resolves the per-platform config flag that disables automatic topic title renames. |
| `telegram_topic_help_text` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Renders `/topic help` usage text for Telegram topic mode. |
| `telegram_topic_root_status_message` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Renders root lobby status and unlinked-session restore guidance. |
| `disable_telegram_topic_mode_for_chat` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Disables topic mode for a chat and clears local per-chat cooldown state. |
| `notify_active_sessions_of_shutdown` | `gateway/delivery_notifications.py` | `DeliveryManager` | Sends deduplicated shutdown/restart notifications to active session origins and configured home channels. |
| `send_update_notification` | `gateway/delivery_notifications.py` | `DeliveryManager` | Delivers the legacy post-update notification from marker files, deferring while the update is still running. |
| `send_restart_notification` | `gateway/delivery_notifications.py` | `DeliveryManager` | Delivers the persisted restart-origin notification and clears the marker file. |
| `send_home_channel_startup_notifications` | `gateway/delivery_notifications.py` | `DeliveryManager` | Sends deduplicated gateway-online notifications to configured platform home channels. |
| `build_process_event_source` | `gateway/background_process_events.py` | `DeliveryManager` | Resolves synthetic background-process event routing from session-store origin, live source cache, or event metadata. |
| `inject_watch_notification` | `gateway/background_process_events.py` | `DeliveryManager` | Injects watch-pattern notifications as internal `MessageEvent` objects on the correct platform adapter. |
| `deliver_media_from_response` | `gateway/media_delivery.py` | `DeliveryManager` | Extracts post-stream `MEDIA:` tags and local file paths, batches images, preserves `[[as_document]]`, and routes audio/video/document sends with thread metadata. |
| `resolve_active_session_followup_decision` | `gateway/active_session_routing.py` | `MessageRouter` | Classifies non-command active-session follow-ups into queue, steer, drain, pending, or interrupt actions. |
| `resolve_builtin_precedence_quick_alias` | `gateway/cold_command_router.py` | `MessageRouter` | Expands quick-command aliases before built-in command dispatch while preserving built-in precedence. |
| `resolve_cold_command_dispatch` | `gateway/cold_command_router.py` | `MessageRouter` | Resolves quick-command, plugin, skill, and bundle dispatch metadata for the cold command path. |
| `unknown_slash_command_response` | `gateway/cold_command_router.py` | `MessageRouter` | Owns user-facing unknown slash command guidance. |
| `resolve_command_hook_decision` | `gateway/cold_command_router.py` | `MessageRouter` | Interprets command hook deny, handled, rewrite, and allow decisions. |
| `execute_plugin_command` | `gateway/cold_command_router.py` | `MessageRouter` | Executes plugin slash command handlers and normalizes sync/async responses. |
| `execute_quick_command` | `gateway/cold_command_router.py` | `MessageRouter` | Executes configured quick commands with sanitized environment, timeout handling, and output redaction. |
| `build_bundle_invocation` | `gateway/cold_command_router.py` | `MessageRouter` | Builds agent-facing messages for skill bundle slash commands. |
| `begin_session_run_generation` | `gateway/session_manager.py` | `SessionManager` | Claims a new monotonically increasing run token for a session. |
| `invalidate_session_run_generation` | `gateway/session_manager.py` | `SessionManager` | Bumps a session's run token to stale any in-flight worker. |
| `is_session_run_current` | `gateway/session_manager.py` | `SessionManager` | Checks whether a worker still owns the current session run token. |
| `interrupt_and_clear_session` | `gateway/session_manager.py` | `SessionManager` | Interrupts an active run, invalidates the run token, clears adapter and pending-message state, and optionally releases the running-agent slot. |
| `clear_session_boundary_security_state` | `gateway/session_manager.py` | `SessionManager` | Clears session-scoped approvals, slash-confirm, reload notes, and update prompts at session boundaries. |
| `evict_cached_agent` | `gateway/session_manager.py` | `SessionManager` | Removes a cached agent for a session under the cache lock. |
| `build_skill_invocation_decision` | `gateway/cold_command_router.py` | `MessageRouter` | Builds agent-facing skill command messages or disabled/unavailable/unknown responses. |
| `GATEWAY_HANDLER_METHODS` | `gateway/command_registry.py` | `CommandRegistry` | Documents cold-path gateway command names and their current runner method owners without binding runner state at import time. |
| `CommandHandlerRegistry` | `gateway/command_registry.py` | `CommandRegistry` | Provides the composed gateway router with an explicit command-handler table instead of ad-hoc runner attribute lookup. |
| `get_gateway_command_handler` | `gateway/command_registry.py` | `CommandRegistry` | Resolves a canonical cold-path command from an explicit handler map used by `_handle_message`. |
| `resolve_special_cold_command` | `gateway/command_registry.py` | `CommandRegistry` | Resolves special cold-path `/new`, `/undo`, and `/steer` behavior before generic command or agent dispatch. |
| `GatewayCommandService` | `gateway/command_handlers/__init__.py` | `CommandRegistry` | Transitional service map that owns concrete slash-command handler bodies while keeping `GatewayRunner` compatibility wrappers thin. |
| `CoreGatewayCommands`, `SessionGatewayCommands`, `OperationsGatewayCommands`, `TelegramTopicGatewayCommands`, `VoiceGatewayCommands` | `gateway/command_handlers/` | `CommandRegistry` | Compatibility aggregates for split command-body groups; current command modules are under the 400-line object gate. |
| `CoreSessionGatewayCommands`, `CoreStatusGatewayCommands`, `CorePlatformGatewayCommands` | `gateway/command_handlers/core_session.py`, `gateway/command_handlers/core_status.py`, `gateway/command_handlers/core_platform.py` | `CommandRegistry` | Split reset/help, status/agents/stop, and platform/restart command groups behind `CoreGatewayCommands`. |
| `OperationsUsageGatewayCommands`, `OperationsReloadGatewayCommands`, `OperationsApprovalGatewayCommands` | `gateway/command_handlers/operations_usage.py`, `gateway/command_handlers/operations_reload.py`, `gateway/command_handlers/operations_approval.py` | `CommandRegistry` | Split usage/diagnostics, reload/bundles, and destructive-command approval helpers behind `OperationsGatewayCommands`. |
| `SessionHistoryGatewayCommands`, `SessionRuntimeGatewayCommands` | `gateway/command_handlers/session_history.py`, `gateway/command_handlers/session_runtime.py` | `CommandRegistry` | Split retry/undo/home/rollback/title commands from background/resume/branch runtime commands. |
| `TelegramTopicSetupGatewayCommands`, `TelegramTopicCommandGatewayCommands` | `gateway/command_handlers/telegram_topic_setup.py`, `gateway/command_handlers/telegram_topic_commands.py` | `CommandRegistry` | Split Telegram topic capability/setup/rename helpers from `/topic` command and restore flows. |
| `VoiceControlGatewayCommands`, `VoiceIOGatewayCommands` | `gateway/command_handlers/voice_control.py`, `gateway/command_handlers/voice_io.py` | `CommandRegistry` | Split voice command/channel control from voice transcript/reply/media delivery helpers. |
| `ModelSwitchGatewayCommands`, `RuntimeModeGatewayCommands`, `DisplayModeGatewayCommands` | `gateway/command_handlers/model_switch.py`, `gateway/command_handlers/runtime_modes.py`, `gateway/command_handlers/display_modes.py` | `CommandRegistry` | Split model/mode command groups below the 400-line object gate while preserving the `ModelModeGatewayCommands` aggregate. |
| `resolve_session_key_for_source` | `gateway/session_manager.py` | `SessionManager` | Resolves session keys through `SessionStore` when available, with config-aware fallback to `build_session_key`. |
| `cache_session_source` | `gateway/session_manager.py` | `SessionManager` | Copies live `SessionSource` values into the bounded LRU source cache. |
| `get_cached_session_source` | `gateway/session_manager.py` | `SessionManager` | Reads cached `SessionSource` values and updates LRU recency. |
| `format_session_info` | `gateway/session_info.py` | `SessionManager` | Formats current model, provider, context-length source, and local/custom endpoint details for gateway command responses. |
| `is_telegram_topic_mode_enabled` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Resolves whether Telegram DM topic mode is active while preserving SessionDB error handling. |
| `is_telegram_topic_root_lobby` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Classifies Telegram root/General topic messages as system-lobby traffic. |
| `is_telegram_topic_lane` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Classifies user-created Telegram DM topics as independent session lanes. |
| `should_send_telegram_topic_notice` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Rate-limits Telegram topic guidance notices per chat. |
| `telegram_topic_root_lobby_message` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Holds root-DM lobby guidance copy. |
| `telegram_topic_root_new_message` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Holds `/new` root-DM topic-mode guidance copy. |
| `telegram_topic_new_header` | `gateway/telegram_topic_manager.py` | `DeliveryManager` | Builds topic-lane `/new` header guidance when applicable. |

## Inventory

| Method | Current line | Target owner |
|---|---:|---|
| `__init__` | 1403 | `GatewayRuntime` |
| `_wire_teams_pipeline_runtime` | 1596 | `AgentRunner` |
| `_warn_if_docker_media_delivery_is_risky` | 1627 | `DeliveryManager` |
| `_has_setup_skill` | 1678 | `GatewayRuntime` |
| `_voice_key` | 1690 | `VoiceHandler` |
| `_load_voice_modes` | 1694 | `VoiceHandler` |
| `_save_voice_modes` | 1697 | `VoiceHandler` |
| `_set_adapter_auto_tts_disabled` | 1700 | `VoiceHandler` |
| `_set_adapter_auto_tts_enabled` | 1704 | `VoiceHandler` |
| `_sync_voice_mode_state_to_adapter` | 1712 | `VoiceHandler` |
| `_safe_adapter_disconnect` | 1722 | `PlatformManager` |
| `_adapter_disconnect_timeout_secs` | 1740 | `PlatformManager` |
| `_platform_connect_timeout_secs` | 1744 | `PlatformManager` |
| `_connect_adapter_with_timeout` | 1748 | `PlatformManager` |
| `should_exit_cleanly` | 1757 | `GatewayRuntime` |
| `should_exit_with_failure` | 1761 | `GatewayRuntime` |
| `exit_reason` | 1765 | `GatewayRuntime` |
| `exit_code` | 1769 | `GatewayRuntime` |
| `_session_key_for_source` | 1772 | `SessionManager` |
| `_telegram_topic_mode_enabled` | 1780 | `DeliveryManager` |
| `_is_telegram_topic_root_lobby` | 1788 | `DeliveryManager` |
| `_is_telegram_topic_lane` | 1795 | `DeliveryManager` |
| `_should_send_telegram_lobby_reminder` | 1802 | `DeliveryManager` |
| `_telegram_topic_root_lobby_message` | 1817 | `DeliveryManager` |
| `_telegram_topic_root_new_message` | 1820 | `DeliveryManager` |
| `_telegram_topic_new_header` | 1823 | `DeliveryManager` |
| `_record_telegram_topic_binding` | 1829 | `DeliveryManager` |
| `_recover_telegram_topic_thread_id` | 1841 | `DeliveryManager` |
| `_resolve_session_agent_runtime` | 1862 | `AgentRunner` |
| `_resolve_turn_agent_config` | 1881 | `AgentRunner` |
| `_handle_adapter_fatal_error` | 1895 | `PlatformManager` |
| `_request_clean_exit` | 1917 | `GatewayRuntime` |
| `_running_agent_count` | 1922 | `AgentRunner` |
| `_status_action_label` | 1925 | `GatewayRuntime` |
| `_status_action_gerund` | 1928 | `GatewayRuntime` |
| `_queue_during_drain_enabled` | 1931 | `MessageRouter` |
| `_enqueue_fifo` | 1948 | `MessageRouter` |
| `_promote_queued_event` | 1964 | `MessageRouter` |
| `_queue_depth` | 1999 | `MessageRouter` |
| `_is_goal_continuation_event` | 2008 | `KanbanWatchers` |
| `_clear_goal_pending_continuations` | 2018 | `KanbanWatchers` |
| `_goal_still_active_for_session` | 2049 | `KanbanWatchers` |
| `_update_runtime_status` | 2060 | `GatewayRuntime` |
| `_update_platform_runtime_status` | 2072 | `PlatformManager` |
| `_pause_failed_platform` | 2096 | `PlatformManager` |
| `_resume_paused_platform` | 2113 | `PlatformManager` |
| `_load_prefill_messages` | 2126 | `MessageRouter` |
| `_load_ephemeral_system_prompt` | 2164 | `MessageRouter` |
| `_load_reasoning_config` | 2185 | `AgentRunner` |
| `_parse_reasoning_command_args` | 2209 | `CommandRegistry` |
| `_resolve_session_reasoning_config` | 2234 | `SessionManager` |
| `_set_session_reasoning_override` | 2253 | `SessionManager` |
| `_load_service_tier` | 2269 | `AgentRunner` |
| `_load_show_reasoning` | 2279 | `AgentRunner` |
| `_load_busy_input_mode` | 2296 | `MessageRouter` |
| `_load_restart_drain_timeout` | 2316 | `GatewayRuntime` |
| `_load_background_notifications_mode` | 2342 | `DeliveryManager` |
| `_load_provider_routing` | 2377 | `AgentRunner` |
| `_load_fallback_model` | 2382 | `AgentRunner` |
| `_snapshot_running_agents` | 2391 | `AgentRunner` |
| `_queue_or_replace_pending_event` | 2397 | `MessageRouter` |
| `_handle_active_session_busy_message` | 2403 | `MessageRouter` |
| `_drain_active_agents` | 2420 | `AgentRunner` |
| `_interrupt_running_agents` | 2429 | `AgentRunner` |
| `_notify_active_sessions_of_shutdown` | 2437 | `DeliveryManager` |
| `_finalize_shutdown_agents` | 2454 | `AgentRunner` |
| `_cleanup_agent_resources` | 2465 | `AgentRunner` |
| `_increment_restart_failure_counts` | 2471 | `GatewayRuntime` |
| `_suspend_stuck_loop_sessions` | 2498 | `SessionManager` |
| `_clear_restart_failure_count` | 2547 | `GatewayRuntime` |
| `_launch_detached_restart_command` | 2568 | `GatewayRuntime` |
| `request_restart` | 2667 | `GatewayRuntime` |
| `_schedule_resume_pending_sessions` | 2693 | `SessionManager` |
| `start` | 2761 | `GatewayRuntime` |
| `_handoff_watcher` | 3298 | `SessionManager` |
| `_process_handoff` | 3349 | `SessionManager` |
| `_session_expiry_watcher` | 3523 | `SessionManager` |
| `_active_profile_name` | 3685 | `GatewayRuntime` |
| `_kanban_notifier_watcher` | 3693 | `KanbanWatchers` |
| `_kanban_advance` | 4029 | `KanbanWatchers` |
| `_kanban_unsub` | 4051 | `KanbanWatchers` |
| `_kanban_rewind` | 4065 | `KanbanWatchers` |
| `_deliver_kanban_artifacts` | 4088 | `KanbanWatchers` |
| `_kanban_dispatcher_watcher` | 4192 | `KanbanWatchers` |
| `_platform_reconnect_watcher` | 4600 | `PlatformManager` |
| `stop` | 3155 | `GatewayRuntime` |
| `wait_for_shutdown` | 4976 | `GatewayRuntime` |
| `_create_adapter` | 4980 | `PlatformManager` |
| `_is_user_authorized` | 4994 | `MessageRouter` |
| `_get_unauthorized_dm_behavior` | 5010 | `MessageRouter` |
| `_deliver_platform_notice` | 5031 | `DeliveryManager` |
| `_handle_message` | 5062 | `MessageRouter` |
| `_prepare_inbound_message_text` | 6089 | `MessageRouter` |
| `_consume_pending_native_image_paths` | 6128 | `MessageRouter` |
| `_cache_session_source` | 6134 | `SessionManager` |
| `_get_cached_session_source` | 6145 | `SessionManager` |
| `_handle_message_with_agent` | 6148 | `AgentRunner` |
| `_format_session_info` | 7204 | `SessionManager` |
| `_handle_reset_command` | 7222 | `CommandRegistry` |
| `_handle_profile_command` | 7225 | `CommandRegistry` |
| `_check_slash_access` | 7228 | `CommandRegistry` |
| `_handle_whoami_command` | 7231 | `CommandRegistry` |
| `_handle_kanban_command` | 7234 | `KanbanWatchers` |
| `_handle_status_command` | 7237 | `CommandRegistry` |
| `_handle_agents_command` | 7240 | `CommandRegistry` |
| `_handle_stop_command` | 7243 | `CommandRegistry` |
| `_handle_platform_command` | 7246 | `CommandRegistry` |
| `_handle_restart_command` | 7249 | `CommandRegistry` |
| `_is_stale_restart_redelivery` | 7252 | `GatewayRuntime` |
| `_handle_help_command` | 7255 | `CommandRegistry` |
| `_handle_commands_command` | 7258 | `CommandRegistry` |
| `_handle_model_command` | 7261 | `CommandRegistry` |
| `_handle_codex_runtime_command` | 7264 | `CommandRegistry` |
| `_handle_personality_command` | 7267 | `CommandRegistry` |
| `_handle_retry_command` | 7270 | `CommandRegistry` |
| `_goal_max_turns_from_config` | 7273 | `KanbanWatchers` |
| `_get_goal_manager_for_event` | 7276 | `KanbanWatchers` |
| `_handle_goal_command` | 7279 | `KanbanWatchers` |
| `_handle_subgoal_command` | 7282 | `KanbanWatchers` |
| `_send_goal_status_notice` | 7285 | `KanbanWatchers` |
| `_defer_goal_status_notice_after_delivery` | 7288 | `KanbanWatchers` |
| `_post_turn_goal_continuation` | 7291 | `KanbanWatchers` |
| `_handle_undo_command` | 7294 | `CommandRegistry` |
| `_handle_set_home_command` | 7297 | `CommandRegistry` |
| `_get_guild_id` | 7300 | `VoiceHandler` |
| `_handle_voice_command` | 7303 | `VoiceHandler` |
| `_handle_voice_channel_join` | 7306 | `VoiceHandler` |
| `_handle_voice_channel_leave` | 7309 | `VoiceHandler` |
| `_handle_voice_timeout_cleanup` | 7312 | `VoiceHandler` |
| `_is_duplicate_voice_transcript` | 7315 | `VoiceHandler` |
| `_handle_voice_channel_input` | 7318 | `VoiceHandler` |
| `_should_send_voice_reply` | 7321 | `VoiceHandler` |
| `_send_voice_reply` | 7324 | `VoiceHandler` |
| `_deliver_media_from_response` | 7327 | `DeliveryManager` |
| `_handle_rollback_command` | 7337 | `CommandRegistry` |
| `_handle_background_command` | 7340 | `CommandRegistry` |
| `_run_background_task` | 7343 | `AgentRunner` |
| `_handle_reasoning_command` | 7346 | `CommandRegistry` |
| `_handle_fast_command` | 7349 | `CommandRegistry` |
| `_handle_yolo_command` | 7352 | `CommandRegistry` |
| `_handle_verbose_command` | 7355 | `CommandRegistry` |
| `_handle_footer_command` | 7358 | `CommandRegistry` |
| `_handle_compress_command` | 7361 | `CommandRegistry` |
| `_get_telegram_topic_capabilities` | 7364 | `DeliveryManager` |
| `_ensure_telegram_system_topic` | 7367 | `DeliveryManager` |
| `_send_telegram_topic_setup_image` | 7370 | `DeliveryManager` |
| `_sanitize_telegram_topic_title` | 7373 | `DeliveryManager` |
| `_rename_telegram_topic_for_session_title` | 7376 | `DeliveryManager` |
| `_telegram_topic_auto_rename_disabled` | 7379 | `DeliveryManager` |
| `_schedule_telegram_topic_title_rename` | 7382 | `DeliveryManager` |
| `_should_send_telegram_capability_hint` | 7385 | `DeliveryManager` |
| `_telegram_topic_help_text` | 7388 | `DeliveryManager` |
| `_disable_telegram_topic_mode_for_chat` | 7391 | `DeliveryManager` |
| `_handle_topic_command` | 7394 | `CommandRegistry` |
| `_telegram_topic_root_status_message` | 7397 | `DeliveryManager` |
| `_restore_telegram_topic_session` | 7400 | `SessionManager` |
| `_handle_title_command` | 7403 | `CommandRegistry` |
| `_handle_resume_command` | 7406 | `CommandRegistry` |
| `_handle_branch_command` | 7409 | `CommandRegistry` |
| `_handle_usage_command` | 7412 | `CommandRegistry` |
| `_handle_insights_command` | 7415 | `CommandRegistry` |
| `_handle_reload_mcp_command` | 7418 | `CommandRegistry` |
| `_execute_mcp_reload` | 7421 | `CommandRegistry` |
| `_handle_reload_skills_command` | 7424 | `CommandRegistry` |
| `_handle_bundles_command` | 7427 | `CommandRegistry` |
| `_maybe_confirm_destructive_slash` | 7430 | `CommandRegistry` |
| `_request_slash_confirm` | 7433 | `CommandRegistry` |
| `_read_user_config` | 7436 | `GatewayRuntime` |
| `_thread_metadata_for_source` | 7439 | `DeliveryManager` |
| `_reply_anchor_for_event` | 7442 | `DeliveryManager` |
| `_handle_approve_command` | 7445 | `CommandRegistry` |
| `_handle_deny_command` | 7448 | `CommandRegistry` |
| `_handle_debug_command` | 7451 | `CommandRegistry` |
| `_handle_update_command` | 7454 | `CommandRegistry` |
| `_schedule_update_notification_watch` | 7457 | `DeliveryManager` |
| `_watch_update_progress` | 7460 | `DeliveryManager` |
| `_send_update_notification` | 7463 | `DeliveryManager` |
| `_send_restart_notification` | 7479 | `DeliveryManager` |
| `_send_home_channel_startup_notifications` | 7488 | `DeliveryManager` |
| `_set_session_env` | 7506 | `SessionManager` |
| `_clear_session_env` | 7527 | `SessionManager` |
| `_run_in_executor_with_context` | 7532 | `AgentRunner` |
| `_decide_image_input_mode` | 7538 | `AgentRunner` |
| `_enrich_message_with_vision` | 7550 | `AgentRunner` |
| `_enrich_message_with_transcription` | 7577 | `VoiceHandler` |
| `_build_process_event_source` | 7602 | `DeliveryManager` |
| `_inject_watch_notification` | 7616 | `DeliveryManager` |
| `_run_process_watcher` | 7630 | `AgentRunner` |
| `_extract_cache_busting_config` | 7664 | `AgentRunner` |
| `_agent_config_signature` | 7680 | `AgentRunner` |
| `_apply_session_model_override` | 7708 | `SessionManager` |
| `_is_intentional_model_switch` | 7726 | `AgentRunner` |
| `_release_running_agent_state` | 7734 | `AgentRunner` |
| `_clear_session_boundary_security_state` | 7772 | `SessionManager` |
| `_begin_session_run_generation` | 7782 | `SessionManager` |
| `_invalidate_session_run_generation` | 7796 | `SessionManager` |
| `_is_session_run_current` | 7812 | `SessionManager` |
| `_bind_adapter_run_generation` | 7820 | `PlatformManager` |
| `_interrupt_and_clear_session` | 7836 | `SessionManager` |
| `_evict_cached_agent` | 7860 | `SessionManager` |
| `_init_cached_agent_for_turn` | 7869 | `AgentRunner` |
| `_release_evicted_agent_soft` | 7888 | `AgentRunner` |
| `_enforce_agent_cache_cap` | 7903 | `AgentRunner` |
| `_sweep_idle_cached_agents` | 7935 | `AgentRunner` |
| `_get_proxy_url` | 7968 | `AgentRunner` |
| `_run_agent` | 7983 | `AgentRunner` |

## Phase B Characterization Starting Points

1. `AgentRunner`: keep the current `gateway.proxy_runner` tests as the first
   extracted seam and add characterization around `_resolve_turn_agent_config`
   before moving more agent execution logic.
2. `MessageRouter`: characterize `_handle_message` dispatch decisions without
   extracting command bodies.
3. `VoiceHandler`: extract the `voice mode` persistence/sync cluster first;
   the glossary already names this `VoiceModeManager`.
4. `DeliveryManager`: isolate Telegram topic helpers only after topic-mode
   tests cover lobby/lane routing and auto-rename behavior.
