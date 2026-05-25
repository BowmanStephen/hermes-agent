"""Voice-mode persistence and adapter sync helpers for the gateway."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from gateway.config import Platform

logger = logging.getLogger(__name__)

VALID_VOICE_MODES = {"off", "voice_only", "all"}


def voice_mode_key(platform: Platform, chat_id: str) -> str:
    """Return a platform-namespaced key for voice mode state."""

    return f"{platform.value}:{chat_id}"


def load_voice_modes(path: Path, *, logger: logging.Logger = logger) -> dict[str, str]:
    """Load persisted voice modes, skipping legacy or invalid entries."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}
    for chat_id, mode in data.items():
        if mode not in VALID_VOICE_MODES:
            continue
        key = str(chat_id)
        if ":" not in key:
            logger.warning(
                "Skipping legacy unprefixed voice mode key %r during migration. "
                "Re-enable voice mode on that chat to rebuild the prefixed key.",
                key,
            )
            continue
        result[key] = mode
    return result


def save_voice_modes(
    path: Path,
    voice_modes: Mapping[str, str],
    *,
    logger: logging.Logger = logger,
) -> None:
    """Persist voice modes to disk."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(voice_modes), indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to save voice modes: %s", exc)


def set_adapter_auto_tts_disabled(adapter: Any, chat_id: str, disabled: bool) -> None:
    """Update an adapter's in-memory auto-TTS suppression set if present."""

    disabled_chats = getattr(adapter, "_auto_tts_disabled_chats", None)
    if not isinstance(disabled_chats, set):
        return
    if disabled:
        disabled_chats.add(chat_id)
        enabled_chats = getattr(adapter, "_auto_tts_enabled_chats", None)
        if isinstance(enabled_chats, set):
            enabled_chats.discard(chat_id)
    else:
        disabled_chats.discard(chat_id)


def set_adapter_auto_tts_enabled(adapter: Any, chat_id: str, enabled: bool) -> None:
    """Update an adapter's per-chat auto-TTS opt-in set if present."""

    enabled_chats = getattr(adapter, "_auto_tts_enabled_chats", None)
    if not isinstance(enabled_chats, set):
        return
    if enabled:
        enabled_chats.add(chat_id)
        disabled_chats = getattr(adapter, "_auto_tts_disabled_chats", None)
        if isinstance(disabled_chats, set):
            disabled_chats.discard(chat_id)
    else:
        enabled_chats.discard(chat_id)


def sync_voice_mode_state_to_adapter(
    adapter: Any,
    voice_modes: Mapping[str, str],
    *,
    load_config: Callable[[], Mapping[str, Any]] | None = None,
) -> None:
    """Restore persisted /voice state into a live platform adapter."""

    platform = getattr(adapter, "platform", None)
    if not isinstance(platform, Platform):
        return

    disabled_chats = getattr(adapter, "_auto_tts_disabled_chats", None)
    enabled_chats = getattr(adapter, "_auto_tts_enabled_chats", None)
    if not isinstance(disabled_chats, set) and not isinstance(enabled_chats, set):
        return

    if load_config is None:
        from hermes_cli.config import load_config as load_config

    try:
        full_cfg = load_config()
        auto_tts_default = bool((full_cfg.get("voice") or {}).get("auto_tts", False))
    except Exception:
        auto_tts_default = False
    if hasattr(adapter, "_auto_tts_default"):
        adapter._auto_tts_default = auto_tts_default

    prefix = f"{platform.value}:"
    if isinstance(disabled_chats, set):
        disabled_chats.clear()
        disabled_chats.update(
            key[len(prefix) :]
            for key, mode in voice_modes.items()
            if mode == "off" and key.startswith(prefix)
        )
    if isinstance(enabled_chats, set):
        enabled_chats.clear()
        enabled_chats.update(
            key[len(prefix) :]
            for key, mode in voice_modes.items()
            if mode in {"voice_only", "all"} and key.startswith(prefix)
        )
