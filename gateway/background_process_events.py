"""Background process event routing helpers for GatewayRunner."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from gateway.config import Platform, _BUILTIN_PLATFORM_VALUES
from gateway.config_loader import _parse_session_key
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


logger = logging.getLogger(__name__)


def build_process_event_source(
    evt: dict,
    *,
    session_store: Any = None,
    get_cached_session_source: Callable[[str], SessionSource | None] | None = None,
    logger: logging.Logger = logger,
) -> SessionSource | None:
    """Resolve the canonical source for a synthetic background-process event."""

    session_key = str(evt.get("session_key") or "").strip()
    derived_platform = ""
    derived_chat_type = ""
    derived_chat_id = ""

    if session_key:
        try:
            session_store._ensure_loaded()
            entry = session_store._entries.get(session_key)
            if entry and getattr(entry, "origin", None):
                return entry.origin
        except Exception as exc:
            logger.debug(
                "Synthetic process-event session-store lookup failed for %s: %s",
                session_key,
                exc,
            )

        if get_cached_session_source is not None:
            cached_source = get_cached_session_source(session_key)
            if cached_source is not None:
                return cached_source

        parsed = _parse_session_key(session_key)
        if parsed:
            derived_platform = parsed["platform"]
            derived_chat_type = parsed["chat_type"]
            derived_chat_id = parsed["chat_id"]

    platform_name = str(evt.get("platform") or derived_platform or "").strip().lower()
    chat_type = str(evt.get("chat_type") or derived_chat_type or "").strip().lower()
    chat_id = str(evt.get("chat_id") or derived_chat_id or "").strip()
    if not platform_name or not chat_type or not chat_id:
        return None

    try:
        platform = Platform(platform_name)
        # Reject arbitrary strings that create dynamic pseudo-members.
        # Built-in platforms are always valid; plugin platforms must be
        # registered in the platform registry.
        if platform.value not in _BUILTIN_PLATFORM_VALUES:
            try:
                from gateway.platform_registry import platform_registry

                if not platform_registry.is_registered(platform.value):
                    raise ValueError(platform_name)
            except Exception:
                raise ValueError(platform_name)
    except Exception:
        logger.warning(
            "Synthetic process event has invalid platform metadata: %r",
            platform_name,
        )
        return None

    return SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_type=chat_type,
        thread_id=str(evt.get("thread_id") or "").strip() or None,
        user_id=str(evt.get("user_id") or "").strip() or None,
        user_name=str(evt.get("user_name") or "").strip() or None,
    )


async def inject_watch_notification(
    synth_text: str,
    evt: dict,
    *,
    source_resolver: Callable[[dict], SessionSource | None],
    adapters: Mapping[Any, Any],
    logger: logging.Logger = logger,
) -> None:
    """Inject a watch-pattern notification as a synthetic message event."""

    source = source_resolver(evt)
    if not source:
        logger.warning(
            "Dropping watch notification with no routing metadata for process %s",
            evt.get("session_id", "unknown"),
        )
        return
    platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
    adapter = None
    for p, a in adapters.items():
        if getattr(p, "value", str(p)) == platform_name:
            adapter = a
            break
    if not adapter:
        return
    try:
        synth_event = MessageEvent(
            text=synth_text,
            message_type=MessageType.TEXT,
            source=source,
            internal=True,
            message_id=str(evt.get("message_id") or "").strip() or None,
        )
        logger.info(
            "Watch pattern notification — injecting for %s chat=%s thread=%s",
            platform_name,
            source.chat_id,
            source.thread_id,
        )
        await adapter.handle_message(synth_event)
    except Exception as e:
        logger.error("Watch notification injection error: %s", e)
