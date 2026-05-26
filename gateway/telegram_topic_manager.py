"""Telegram topic-mode helpers for gateway delivery/routing."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, MutableMapping

from gateway.config import Platform
from gateway.session import SessionSource


# Telegram's General (pinned top) topic in forum-enabled private chats.
# Bot API behavior varies: some clients omit message_thread_id for General,
# others send "1". Treat both as "root" for lobby/lane purposes.
TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})
TELEGRAM_LOBBY_REMINDER_COOLDOWN_S = 30.0
TELEGRAM_CAPABILITY_HINT_COOLDOWN_S = 300.0


def is_telegram_topic_mode_enabled(
    source: SessionSource,
    session_db: object,
    *,
    logger: logging.Logger | None = None,
) -> bool:
    """Return whether Telegram DM topic mode is active for this chat."""

    if source.platform != Platform.TELEGRAM or source.chat_type != "dm":
        return False
    if session_db is None:
        return False
    try:
        raw = session_db.is_telegram_topic_mode_enabled(
            chat_id=str(source.chat_id),
            user_id=str(source.user_id),
        )
    except Exception:
        if logger is not None:
            logger.debug("Failed to read Telegram topic mode state", exc_info=True)
        return False
    return raw is True


def is_telegram_topic_root_lobby(
    source: SessionSource,
    *,
    topic_mode_enabled: bool,
) -> bool:
    """True for the main Telegram DM/General topic when topic mode is active."""

    if source.platform != Platform.TELEGRAM or source.chat_type != "dm":
        return False
    if not topic_mode_enabled:
        return False
    tid = str(source.thread_id or "")
    return tid in TELEGRAM_GENERAL_TOPIC_IDS


def is_telegram_topic_lane(
    source: SessionSource,
    *,
    topic_mode_enabled: bool,
) -> bool:
    """True for a user-created Telegram private-chat topic lane."""

    if source.platform != Platform.TELEGRAM or source.chat_type != "dm":
        return False
    if not topic_mode_enabled:
        return False
    tid = str(source.thread_id or "")
    if not tid or tid in TELEGRAM_GENERAL_TOPIC_IDS:
        return False
    return True


def should_send_telegram_topic_notice(
    source: SessionSource,
    timestamps: MutableMapping[str, float],
    *,
    cooldown_s: float = TELEGRAM_LOBBY_REMINDER_COOLDOWN_S,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """Rate-limit a Telegram topic notice by chat ID."""

    chat_id = str(source.chat_id or "")
    if not chat_id:
        return True
    now = now_fn()
    last = timestamps.get(chat_id)
    if last is None:
        timestamps[chat_id] = now
        return True
    if now - last < cooldown_s:
        return False
    timestamps[chat_id] = now
    return True


def telegram_topic_root_lobby_message() -> str:
    return (
        "This main chat is reserved for system commands.\n\n"
        "To start a new Hermes chat, open the All Messages topic at the top "
        "of this bot interface and send any message there. Telegram will "
        "create a new topic for that message; each topic works as an "
        "independent Hermes session."
    )


def telegram_topic_root_new_message() -> str:
    return (
        "To start a new parallel Hermes chat, open the All Messages topic "
        "at the top of this bot interface and send any message there. "
        "Telegram will create a new topic for it.\n\n"
        "Each topic is an independent Hermes session. Use /new inside an "
        "existing topic only if you want to replace that topic's current session."
    )


def telegram_topic_new_header(
    source: SessionSource,
    *,
    topic_mode_enabled: bool,
) -> str | None:
    if not is_telegram_topic_lane(source, topic_mode_enabled=topic_mode_enabled):
        return None
    return (
        "Started a new Hermes session in this topic.\n\n"
        "Tip: for parallel work, open All Messages and send a message there "
        "to create a separate topic instead of using /new here. /new replaces "
        "the session attached to the current topic."
    )


def record_telegram_topic_binding(
    source: SessionSource,
    session_entry: object,
    session_db: object,
) -> bool:
    """Persist a Telegram topic-to-Hermes-session binding."""

    if session_db is None or not source.chat_id or not source.thread_id:
        return False
    session_db.bind_telegram_topic(
        chat_id=str(source.chat_id),
        thread_id=str(source.thread_id),
        user_id=str(source.user_id or ""),
        session_key=session_entry.session_key,
        session_id=session_entry.session_id,
    )
    return True


def recover_telegram_topic_thread_id(
    source: SessionSource,
    session_db: object,
    *,
    topic_mode_enabled: bool,
    logger: logging.Logger | None = None,
) -> str | None:
    """Recover Telegram topic routing to the user's most recent known topic."""

    if (
        source.platform != Platform.TELEGRAM
        or source.chat_type != "dm"
        or not source.chat_id
        or not source.user_id
        or not topic_mode_enabled
        or session_db is None
    ):
        return None
    try:
        bindings = session_db.list_telegram_topic_bindings_for_chat(
            chat_id=str(source.chat_id),
        )
    except Exception:
        if logger is not None:
            logger.debug("topic-recover: read failed", exc_info=True)
        return None
    if not bindings:
        return None
    inbound = str(source.thread_id or "")
    is_lobby = not inbound or inbound in TELEGRAM_GENERAL_TOPIC_IDS
    known = {str(binding.get("thread_id") or "") for binding in bindings}
    if not is_lobby and inbound in known:
        return None
    user_id = str(source.user_id)
    for binding in bindings:
        if str(binding.get("user_id") or "") == user_id:
            recovered = str(binding.get("thread_id") or "")
            if recovered and recovered != inbound:
                return recovered
            return None
    return None


def sanitize_telegram_topic_title(title: str) -> str:
    """Return a Bot API-safe forum topic name from a generated session title."""

    cleaned = re.sub(r"\s+", " ", str(title or "")).strip()
    if not cleaned:
        return "Hermes Chat"
    if len(cleaned) > 120:
        cleaned = cleaned[:117].rstrip() + "..."
    return cleaned


def telegram_topic_auto_rename_disabled(
    config: object,
    source: SessionSource,
) -> bool:
    """Return True when operator config disables per-topic auto-rename."""

    platform_cfg = (
        config.platforms.get(source.platform)
        if config is not None and getattr(config, "platforms", None)
        else None
    )
    if platform_cfg is None:
        return False
    extra = getattr(platform_cfg, "extra", None) or {}
    value = extra.get("disable_topic_auto_rename")
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def telegram_topic_help_text() -> str:
    return (
        "/topic — enable multi-session DM mode (one bot, many parallel chats)\n"
        "\n"
        "Usage:\n"
        "  /topic             Enable topic mode, or show status if already on\n"
        "  /topic help        Show this message\n"
        "  /topic off         Disable topic mode and clear topic bindings\n"
        "  /topic <id>        Inside a topic: restore a previous session by ID\n"
        "\n"
        "How it works:\n"
        "1. Run /topic once in this DM — Hermes checks BotFather Threads\n"
        "   Settings are enabled and flips on multi-session mode.\n"
        "2. Tap All Messages at the top of the bot and send any message.\n"
        "   Telegram creates a new topic for that message; each topic is\n"
        "   an independent Hermes session (fresh history, fresh context).\n"
        "3. The root DM becomes a system lobby — send /topic, /status,\n"
        "   /help, /usage there. Normal prompts go in a topic.\n"
        "4. /new inside a topic resets just that topic's session.\n"
        "5. /topic <id> inside a topic restores an old session into it."
    )


def telegram_topic_root_status_message(
    source: SessionSource,
    session_db: object,
    *,
    logger: logging.Logger | None = None,
) -> str:
    """Render root-DM topic mode status, including restore candidates."""

    lines = [
        "Telegram multi-session topics are enabled.",
        "",
        "To create a new Hermes chat, open All Messages at the top of this "
        "bot interface and send any message there. Telegram will create a "
        "new topic for it.",
        "",
    ]
    try:
        sessions = session_db.list_unlinked_telegram_sessions_for_user(
            chat_id=str(source.chat_id),
            user_id=str(source.user_id),
            limit=10,
        )
    except Exception:
        if logger is not None:
            logger.debug("Failed to list unlinked Telegram sessions", exc_info=True)
        sessions = []

    if sessions:
        lines.append("Previous unlinked sessions:")
        for session in sessions:
            session_id = str(session.get("id") or "")
            title = str(session.get("title") or "Untitled session")
            preview = str(session.get("preview") or "").strip()
            line = f"- {title} — `{session_id}`"
            if preview:
                line += f" — {preview}"
            lines.append(line)
        lines.extend([
            "",
            "To restore one:",
            "1. Create or open a topic. To create a new one, open All Messages and send any message there.",
            "2. Send /topic <session-id> inside that topic.",
            f"Example: Send /topic {sessions[0].get('id')} inside a topic.",
        ])
    else:
        lines.extend([
            "No previous unlinked Telegram sessions found.",
            "",
            "To restore a previous session later:",
            "1. Create or open a topic. To create a new one, open All Messages and send any message there.",
            "2. Send /topic <session-id> inside that topic.",
        ])
    return "\n".join(lines)


def disable_telegram_topic_mode_for_chat(
    source: SessionSource,
    session_db: object,
    *,
    lobby_reminder_ts: MutableMapping[str, float] | None = None,
    capability_hint_ts: MutableMapping[str, float] | None = None,
    unavailable_message: str,
    logger: logging.Logger | None = None,
) -> str:
    """Disable Telegram topic mode for one chat and clear local cooldowns."""

    if not session_db:
        return unavailable_message
    chat_id = str(source.chat_id or "")
    if not chat_id:
        return "Could not determine chat ID."
    try:
        currently_enabled = session_db.is_telegram_topic_mode_enabled(
            chat_id=chat_id,
            user_id=str(source.user_id or ""),
        )
    except Exception:
        currently_enabled = False
    if not currently_enabled:
        return "Multi-session topic mode is not currently enabled for this chat."
    try:
        session_db.disable_telegram_topic_mode(chat_id=chat_id)
    except Exception as exc:
        if logger is not None:
            logger.exception("Failed to disable Telegram topic mode")
        return f"Failed to disable topic mode: {exc}"
    for store in (lobby_reminder_ts, capability_hint_ts):
        if isinstance(store, dict):
            store.pop(chat_id, None)
    return (
        "Multi-session topic mode is now OFF for this chat.\n\n"
        "Existing topics in Telegram aren't removed — they'll just stop "
        "being gated as independent sessions. The root DM works as a "
        "normal Hermes chat again. Run /topic to re-enable later."
    )
