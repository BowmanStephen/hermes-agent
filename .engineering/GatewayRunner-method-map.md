# GatewayRunner Method Map

Generated from the current `gateway/run.py` after the proxy-runner, router,
agent-runner, command-registry, session-manager, Telegram-topic, voice-mode,
platform-lifecycle/fatal-error, reconnect-watcher, adapter-factory,
source-authorization, inbound-message-preparation, active-session-busy,
agent-shutdown, background-task, and session-info
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
| `get_gateway_command_handler` | `gateway/command_registry.py` | `CommandRegistry` | Resolves a canonical cold-path command from an explicit handler map used by `_handle_message`. |
| `resolve_special_cold_command` | `gateway/command_registry.py` | `CommandRegistry` | Resolves special cold-path `/new`, `/undo`, and `/steer` behavior before generic command or agent dispatch. |
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
| `__init__` | 1308 | `GatewayRuntime` |
| `_wire_teams_pipeline_runtime` | 1501 | `AgentRunner` |
| `_warn_if_docker_media_delivery_is_risky` | 1532 | `DeliveryManager` |
| `_has_setup_skill` | 1583 | `GatewayRuntime` |
| `_voice_key` | 1673 | `VoiceHandler` |
| `_load_voice_modes` | 1677 | `VoiceHandler` |
| `_save_voice_modes` | 1680 | `VoiceHandler` |
| `_set_adapter_auto_tts_disabled` | 1683 | `VoiceHandler` |
| `_set_adapter_auto_tts_enabled` | 1687 | `VoiceHandler` |
| `_sync_voice_mode_state_to_adapter` | 1695 | `VoiceHandler` |
| `_safe_adapter_disconnect` | 1716 | `PlatformManager` |
| `_adapter_disconnect_timeout_secs` | 1734 | `PlatformManager` |
| `_platform_connect_timeout_secs` | 1738 | `PlatformManager` |
| `_connect_adapter_with_timeout` | 1742 | `PlatformManager` |
| `should_exit_cleanly` | 1783 | `GatewayRuntime` |
| `should_exit_with_failure` | 1787 | `GatewayRuntime` |
| `exit_reason` | 1791 | `GatewayRuntime` |
| `exit_code` | 1795 | `GatewayRuntime` |
| `_session_key_for_source` | 1798 | `SessionManager` |
| `_telegram_topic_mode_enabled` | 1814 | `DeliveryManager` |
| `_is_telegram_topic_root_lobby` | 1839 | `DeliveryManager` |
| `_is_telegram_topic_lane` | 1848 | `DeliveryManager` |
| `_should_send_telegram_lobby_reminder` | 1861 | `DeliveryManager` |
| `_telegram_topic_root_lobby_message` | 1881 | `DeliveryManager` |
| `_telegram_topic_root_new_message` | 1890 | `DeliveryManager` |
| `_telegram_topic_new_header` | 1899 | `DeliveryManager` |
| `_record_telegram_topic_binding` | 1909 | `DeliveryManager` |
| `_recover_telegram_topic_thread_id` | 1926 | `DeliveryManager` |
| `_resolve_session_agent_runtime` | 1852 | `AgentRunner` |
| `_resolve_turn_agent_config` | 1871 | `AgentRunner` |
| `_handle_adapter_fatal_error` | 1952 | `PlatformManager` |
| `_request_clean_exit` | 1971 | `GatewayRuntime` |
| `_running_agent_count` | 1976 | `AgentRunner` |
| `_status_action_label` | 1979 | `GatewayRuntime` |
| `_status_action_gerund` | 1982 | `GatewayRuntime` |
| `_queue_during_drain_enabled` | 1985 | `MessageRouter` |
| `_enqueue_fifo` | 2002 | `MessageRouter` |
| `_promote_queued_event` | 2018 | `MessageRouter` |
| `_queue_depth` | 2053 | `MessageRouter` |
| `_is_goal_continuation_event` | 2062 | `KanbanWatchers` |
| `_clear_goal_pending_continuations` | 2072 | `KanbanWatchers` |
| `_goal_still_active_for_session` | 2103 | `KanbanWatchers` |
| `_update_runtime_status` | 2114 | `GatewayRuntime` |
| `_update_platform_runtime_status` | 2129 | `PlatformManager` |
| `_pause_failed_platform` | 2153 | `PlatformManager` |
| `_resume_paused_platform` | 2170 | `PlatformManager` |
| `_load_prefill_messages` | 2409 | `MessageRouter` |
| `_load_ephemeral_system_prompt` | 2447 | `MessageRouter` |
| `_load_reasoning_config` | 2468 | `AgentRunner` |
| `_parse_reasoning_command_args` | 2492 | `CommandRegistry` |
| `_resolve_session_reasoning_config` | 2517 | `SessionManager` |
| `_set_session_reasoning_override` | 2536 | `SessionManager` |
| `_load_service_tier` | 2552 | `AgentRunner` |
| `_load_show_reasoning` | 2579 | `AgentRunner` |
| `_load_busy_input_mode` | 2596 | `MessageRouter` |
| `_load_restart_drain_timeout` | 2616 | `GatewayRuntime` |
| `_load_background_notifications_mode` | 2642 | `DeliveryManager` |
| `_load_provider_routing` | 2677 | `AgentRunner` |
| `_load_fallback_model` | 2691 | `AgentRunner` |
| `_snapshot_running_agents` | 2711 | `AgentRunner` |
| `_queue_or_replace_pending_event` | 2718 | `MessageRouter` |
| `_handle_active_session_busy_message` | 2394 | `MessageRouter` |
| `_drain_active_agents` | 2417 | `AgentRunner` |
| `_interrupt_running_agents` | 2426 | `AgentRunner` |
| `_notify_active_sessions_of_shutdown` | 2877 | `DeliveryManager` |
| `_finalize_shutdown_agents` | 2451 | `AgentRunner` |
| `_cleanup_agent_resources` | 2462 | `AgentRunner` |
| `_increment_restart_failure_counts` | 3165 | `GatewayRuntime` |
| `_suspend_stuck_loop_sessions` | 3192 | `SessionManager` |
| `_clear_restart_failure_count` | 3241 | `GatewayRuntime` |
| `_launch_detached_restart_command` | 3262 | `GatewayRuntime` |
| `request_restart` | 3361 | `GatewayRuntime` |
| `_schedule_resume_pending_sessions` | 3387 | `SessionManager` |
| `start` | 3455 | `GatewayRuntime` |
| `_handoff_watcher` | 3992 | `SessionManager` |
| `_process_handoff` | 4043 | `SessionManager` |
| `_session_expiry_watcher` | 4217 | `SessionManager` |
| `_active_profile_name` | 4379 | `GatewayRuntime` |
| `_kanban_notifier_watcher` | 4387 | `KanbanWatchers` |
| `_kanban_advance` | 4723 | `KanbanWatchers` |
| `_kanban_unsub` | 4745 | `KanbanWatchers` |
| `_kanban_rewind` | 4759 | `KanbanWatchers` |
| `_deliver_kanban_artifacts` | 4782 | `KanbanWatchers` |
| `_kanban_dispatcher_watcher` | 4886 | `KanbanWatchers` |
| `_platform_reconnect_watcher` | 4900 | `PlatformManager` |
| `stop` | 5045 | `GatewayRuntime` |
| `wait_for_shutdown` | 5277 | `GatewayRuntime` |
| `_create_adapter` | 5281 | `PlatformManager` |
| `_is_user_authorized` | 5288 | `MessageRouter` |
| `_get_unauthorized_dm_behavior` | 5304 | `MessageRouter` |
| `_deliver_platform_notice` | 6278 | `DeliveryManager` |
| `_handle_message` | 6309 | `MessageRouter` |
| `_prepare_inbound_message_text` | 6380 | `MessageRouter` |
| `_consume_pending_native_image_paths` | 7714 | `MessageRouter` |
| `_cache_session_source` | 7720 | `SessionManager` |
| `_get_cached_session_source` | 7741 | `SessionManager` |
| `_handle_message_with_agent` | 7755 | `AgentRunner` |
| `_format_session_info` | 7496 | `SessionManager` |
| `_handle_reset_command` | 8930 | `CommandRegistry` |
| `_handle_profile_command` | 9078 | `CommandRegistry` |
| `_check_slash_access` | 9094 | `CommandRegistry` |
| `_handle_whoami_command` | 9137 | `CommandRegistry` |
| `_handle_kanban_command` | 9189 | `KanbanWatchers` |
| `_handle_status_command` | 9287 | `CommandRegistry` |
| `_handle_agents_command` | 9368 | `CommandRegistry` |
| `_handle_stop_command` | 9458 | `CommandRegistry` |
| `_handle_platform_command` | 9497 | `CommandRegistry` |
| `_handle_restart_command` | 9590 | `CommandRegistry` |
| `_is_stale_restart_redelivery` | 9672 | `GatewayRuntime` |
| `_handle_help_command` | 9722 | `CommandRegistry` |
| `_handle_commands_command` | 9747 | `CommandRegistry` |
| `_handle_model_command` | 9802 | `CommandRegistry` |
| `_handle_codex_runtime_command` | 10162 | `CommandRegistry` |
| `_handle_personality_command` | 10207 | `CommandRegistry` |
| `_handle_retry_command` | 10276 | `CommandRegistry` |
| `_goal_max_turns_from_config` | 10315 | `KanbanWatchers` |
| `_get_goal_manager_for_event` | 10336 | `KanbanWatchers` |
| `_handle_goal_command` | 10358 | `KanbanWatchers` |
| `_handle_subgoal_command` | 10435 | `KanbanWatchers` |
| `_send_goal_status_notice` | 10486 | `KanbanWatchers` |
| `_defer_goal_status_notice_after_delivery` | 10505 | `KanbanWatchers` |
| `_post_turn_goal_continuation` | 10548 | `KanbanWatchers` |
| `_handle_undo_command` | 10617 | `CommandRegistry` |
| `_handle_set_home_command` | 10642 | `CommandRegistry` |
| `_get_guild_id` | 10680 | `VoiceHandler` |
| `_handle_voice_command` | 10693 | `VoiceHandler` |
| `_handle_voice_channel_join` | 10763 | `VoiceHandler` |
| `_handle_voice_channel_leave` | 10814 | `VoiceHandler` |
| `_handle_voice_timeout_cleanup` | 10837 | `VoiceHandler` |
| `_is_duplicate_voice_transcript` | 10847 | `VoiceHandler` |
| `_handle_voice_channel_input` | 10888 | `VoiceHandler` |
| `_should_send_voice_reply` | 10956 | `VoiceHandler` |
| `_send_voice_reply` | 11010 | `VoiceHandler` |
| `_deliver_media_from_response` | 9402 | `DeliveryManager` |
| `_handle_rollback_command` | 9424 | `CommandRegistry` |
| `_handle_background_command` | 9483 | `CommandRegistry` |
| `_run_background_task` | 9520 | `AgentRunner` |
| `_handle_reasoning_command` | 9558 | `CommandRegistry` |
| `_handle_fast_command` | 9673 | `CommandRegistry` |
| `_handle_yolo_command` | 11633 | `CommandRegistry` |
| `_handle_verbose_command` | 11650 | `CommandRegistry` |
| `_handle_footer_command` | 11712 | `CommandRegistry` |
| `_handle_compress_command` | 11797 | `CommandRegistry` |
| `_get_telegram_topic_capabilities` | 11933 | `DeliveryManager` |
| `_ensure_telegram_system_topic` | 11961 | `DeliveryManager` |
| `_send_telegram_topic_setup_image` | 12002 | `DeliveryManager` |
| `_sanitize_telegram_topic_title` | 12020 | `DeliveryManager` |
| `_rename_telegram_topic_for_session_title` | 12031 | `DeliveryManager` |
| `_telegram_topic_auto_rename_disabled` | 12116 | `DeliveryManager` |
| `_schedule_telegram_topic_title_rename` | 12139 | `DeliveryManager` |
| `_should_send_telegram_capability_hint` | 12178 | `DeliveryManager` |
| `_telegram_topic_help_text` | 12197 | `DeliveryManager` |
| `_disable_telegram_topic_mode_for_chat` | 12219 | `DeliveryManager` |
| `_handle_topic_command` | 12255 | `CommandRegistry` |
| `_telegram_topic_root_status_message` | 12344 | `DeliveryManager` |
| `_restore_telegram_topic_session` | 12390 | `SessionManager` |
| `_handle_title_command` | 12444 | `CommandRegistry` |
| `_handle_resume_command` | 12493 | `CommandRegistry` |
| `_handle_branch_command` | 12572 | `CommandRegistry` |
| `_handle_usage_command` | 12621 | `CommandRegistry` |
| `_handle_insights_command` | 12758 | `CommandRegistry` |
| `_handle_reload_mcp_command` | 12807 | `CommandRegistry` |
| `_execute_mcp_reload` | 12870 | `CommandRegistry` |
| `_handle_reload_skills_command` | 12942 | `CommandRegistry` |
| `_handle_bundles_command` | 13042 | `CommandRegistry` |
| `_maybe_confirm_destructive_slash` | 13093 | `CommandRegistry` |
| `_request_slash_confirm` | 13180 | `CommandRegistry` |
| `_read_user_config` | 13248 | `GatewayRuntime` |
| `_thread_metadata_for_source` | 13261 | `DeliveryManager` |
| `_reply_anchor_for_event` | 13288 | `DeliveryManager` |
| `_handle_approve_command` | 13299 | `CommandRegistry` |
| `_handle_deny_command` | 13357 | `CommandRegistry` |
| `_handle_debug_command` | 13404 | `CommandRegistry` |
| `_handle_update_command` | 13448 | `CommandRegistry` |
| `_schedule_update_notification_watch` | 13186 | `DeliveryManager` |
| `_watch_update_progress` | 13199 | `DeliveryManager` |
| `_send_update_notification` | 13410 | `DeliveryManager` |
| `_send_restart_notification` | 13426 | `DeliveryManager` |
| `_send_home_channel_startup_notifications` | 13435 | `DeliveryManager` |
| `_set_session_env` | 13453 | `SessionManager` |
| `_clear_session_env` | 13544 | `SessionManager` |
| `_run_in_executor_with_context` | 13486 | `AgentRunner` |
| `_decide_image_input_mode` | 13492 | `AgentRunner` |
| `_enrich_message_with_vision` | 13504 | `AgentRunner` |
| `_enrich_message_with_transcription` | 13532 | `VoiceHandler` |
| `_build_process_event_source` | 13557 | `DeliveryManager` |
| `_inject_watch_notification` | 13571 | `DeliveryManager` |
| `_run_process_watcher` | 13586 | `AgentRunner` |
| `_extract_cache_busting_config` | 13620 | `AgentRunner` |
| `_agent_config_signature` | 13650 | `AgentRunner` |
| `_apply_session_model_override` | 14629 | `SessionManager` |
| `_is_intentional_model_switch` | 14650 | `AgentRunner` |
| `_release_running_agent_state` | 14655 | `AgentRunner` |
| `_clear_session_boundary_security_state` | 14696 | `SessionManager` |
| `_begin_session_run_generation` | 14743 | `SessionManager` |
| `_invalidate_session_run_generation` | 14761 | `SessionManager` |
| `_is_session_run_current` | 14773 | `SessionManager` |
| `_bind_adapter_run_generation` | 14780 | `PlatformManager` |
| `_interrupt_and_clear_session` | 14796 | `SessionManager` |
| `_evict_cached_agent` | 14821 | `SessionManager` |
| `_init_cached_agent_for_turn` | 14829 | `AgentRunner` |
| `_release_evicted_agent_soft` | 14847 | `AgentRunner` |
| `_enforce_agent_cache_cap` | 14869 | `AgentRunner` |
| `_sweep_idle_cached_agents` | 14945 | `AgentRunner` |
| `_get_proxy_url` | 14998 | `AgentRunner` |
| `_run_agent` | 15013 | `AgentRunner` |

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
