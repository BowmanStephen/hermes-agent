"""Runtime-synced gateway.run globals for extracted command handlers."""
from __future__ import annotations

from gateway import run as _gateway_run

_NAMES = ('Any', 'Dict', 'EphemeralReply', 'HomeChannel', 'List', 'MessageEvent', 'MessageType', 'Optional', 'Path', 'Platform', 'PlatformConfig', 'SessionSource', 'TELEGRAM_CAPABILITY_HINT_COOLDOWN_S', 'Union', '_AGENT_PENDING_SENTINEL', '_INTERRUPT_REASON_STOP', '_hermes_home', '_home_target_env_var', '_home_thread_env_var', '_load_gateway_config', '_platform_config_key', '_reply_anchor_for_event', '_resolve_gateway_model', '_resolve_hermes_bin', '_telegramize_command_mentions', 'asyncio', 'atomic_json_write', 'atomic_yaml_write', 'base_url_host_matches', 'cfg_get', 'dataclasses', 'datetime', 'deliver_media_from_response', 'disable_telegram_topic_mode_for_chat', 'fetch_account_usage', 'inspect', 'is_truthy_value', 'json', 'logger', 'os', 're', 'render_account_usage_lines', 'run_background_task', 'safe_schedule_threadsafe', 'sanitize_telegram_topic_title', 'shlex', 'should_send_telegram_topic_notice', 'sys', 't', 'telegram_topic_auto_rename_disabled', 'telegram_topic_help_text', 'telegram_topic_root_status_message', 'tempfile', 'time')

def sync_run_globals(target_globals: dict) -> None:
    for name in _NAMES:
        if hasattr(_gateway_run, name):
            target_globals[name] = getattr(_gateway_run, name)

sync_run_globals(globals())

__all__ = [*_NAMES, "sync_run_globals"]
