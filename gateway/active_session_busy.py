"""Busy active-session follow-up handling for the gateway."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path
from typing import Any

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, SessionSource, merge_pending_message_event

logger = logging.getLogger(__name__)

_BUSY_ACK_COOLDOWN = 30


async def handle_active_session_busy_message(
    *,
    event: MessageEvent,
    session_key: str,
    is_user_authorized: Callable[[SessionSource], bool],
    adapters: Mapping[Platform, Any],
    running_agents: Mapping[str, Any],
    running_agents_ts: Mapping[str, float],
    busy_ack_ts: MutableMapping[str, float],
    busy_input_mode: str,
    draining: bool,
    pending_sentinel: Any,
    queue_during_drain_enabled: Callable[[], bool],
    queue_or_replace_pending_event: Callable[[str, MessageEvent], None],
    status_action_gerund: Callable[[], str],
    reply_anchor_for_event: Callable[[MessageEvent], Any],
    thread_metadata_for_source: Callable[[SessionSource, Any], Any],
    load_gateway_config: Callable[[], dict],
    hermes_home: Path,
    now: Callable[[], float] = time.time,
    log: logging.Logger = logger,
    merge_pending_message_event_fn: Callable[[Any, str, MessageEvent], None] = merge_pending_message_event,
) -> bool:
    if not is_user_authorized(event.source):
        _log_unauthorized_drop(event, session_key, log)
        return True

    adapter = adapters.get(event.source.platform)
    if draining:
        return await _handle_draining_message(
            adapter, event, session_key, queue_during_drain_enabled,
            queue_or_replace_pending_event, status_action_gerund,
            reply_anchor_for_event, thread_metadata_for_source,
        )
    if not adapter:
        return False
    return await _handle_running_message(
        adapter=adapter, event=event, session_key=session_key,
        running_agent=running_agents.get(session_key),
        running_agents_ts=running_agents_ts, busy_ack_ts=busy_ack_ts,
        busy_input_mode=busy_input_mode, pending_sentinel=pending_sentinel,
        reply_anchor_for_event=reply_anchor_for_event,
        thread_metadata_for_source=thread_metadata_for_source,
        load_gateway_config=load_gateway_config, hermes_home=hermes_home,
        now=now, log=log, merge_pending_message_event_fn=merge_pending_message_event_fn,
    )


def _log_unauthorized_drop(event: MessageEvent, session_key: str, log: logging.Logger) -> None:
    log.warning(
        "Dropping message from unauthorized user in active session: "
        "user=%s (%s), platform=%s, session=%s",
        event.source.user_id,
        event.source.user_name,
        event.source.platform.value if event.source.platform else "unknown",
        session_key,
    )


async def _handle_draining_message(
    adapter: Any,
    event: MessageEvent,
    session_key: str,
    queue_during_drain_enabled: Callable[[], bool],
    queue_or_replace_pending_event: Callable[[str, MessageEvent], None],
    status_action_gerund: Callable[[], str],
    reply_anchor_for_event: Callable[[MessageEvent], Any],
    thread_metadata_for_source: Callable[[SessionSource, Any], Any],
) -> bool:
    if not adapter:
        return True
    if queue_during_drain_enabled():
        queue_or_replace_pending_event(session_key, event)
        message = f"⏳ Gateway {status_action_gerund()} — queued for the next turn after it comes back."
    else:
        message = f"⏳ Gateway is {status_action_gerund()} and is not accepting another turn right now."
    await _send_busy_reply(
        adapter, event, message, reply_anchor_for_event,
        thread_metadata_for_source,
    )
    return True


async def _handle_running_message(
    *,
    adapter: Any,
    event: MessageEvent,
    session_key: str,
    running_agent: Any,
    running_agents_ts: Mapping[str, float],
    busy_ack_ts: MutableMapping[str, float],
    busy_input_mode: str,
    pending_sentinel: Any,
    reply_anchor_for_event: Callable[[MessageEvent], Any],
    thread_metadata_for_source: Callable[[SessionSource, Any], Any],
    load_gateway_config: Callable[[], dict],
    hermes_home: Path,
    now: Callable[[], float],
    log: logging.Logger,
    merge_pending_message_event_fn: Callable[[Any, str, MessageEvent], None],
) -> bool:
    effective_mode, steered = _resolve_steer_mode(
        busy_input_mode, event, running_agent, pending_sentinel, session_key, log,
    )
    if not steered:
        merge_pending_message_event_fn(adapter._pending_messages, session_key, event)
    if effective_mode == "interrupt":
        _interrupt_running_agent(running_agent, event, pending_sentinel)
    if not _busy_ack_enabled():
        log.debug("Busy ack suppressed for session %s", session_key)
        return True
    now_ts = now()
    if _busy_ack_debounced(session_key, busy_ack_ts, now_ts):
        return True
    busy_ack_ts[session_key] = now_ts
    message = _build_busy_ack_message(
        effective_mode, running_agent, pending_sentinel,
        running_agents_ts.get(session_key, 0), now_ts,
    )
    message = _with_onboarding_hint(
        message, effective_mode, load_gateway_config, hermes_home, log,
    )
    await _try_send_busy_reply(adapter, event, message, reply_anchor_for_event, thread_metadata_for_source, log)
    return True


def _resolve_steer_mode(
    busy_input_mode: str,
    event: MessageEvent,
    running_agent: Any,
    pending_sentinel: Any,
    session_key: str,
    log: logging.Logger,
) -> tuple[str, bool]:
    if busy_input_mode != "steer":
        return busy_input_mode, False
    steer_text = (event.text or "").strip()
    can_steer = (
        steer_text and running_agent is not None
        and running_agent is not pending_sentinel
        and hasattr(running_agent, "steer")
    )
    if not can_steer:
        return "queue", False
    try:
        return ("steer", True) if running_agent.steer(steer_text) else ("queue", False)
    except Exception as exc:
        log.warning("Gateway steer failed for session %s: %s", session_key, exc)
        return "queue", False


def _interrupt_running_agent(running_agent: Any, event: MessageEvent, pending_sentinel: Any) -> None:
    if not running_agent or running_agent is pending_sentinel:
        return
    try:
        running_agent.interrupt(event.text)
    except Exception:
        pass


def _busy_ack_enabled() -> bool:
    return os.environ.get("HERMES_GATEWAY_BUSY_ACK_ENABLED", "true").lower() == "true"


def _busy_ack_debounced(
    session_key: str,
    busy_ack_ts: MutableMapping[str, float],
    now_ts: float,
) -> bool:
    return now_ts - busy_ack_ts.get(session_key, 0) < _BUSY_ACK_COOLDOWN


def _build_busy_ack_message(
    effective_mode: str,
    running_agent: Any,
    pending_sentinel: Any,
    started_at: float,
    now_ts: float,
) -> str:
    status_detail = _busy_status_detail(running_agent, pending_sentinel, started_at, now_ts)
    if effective_mode == "steer":
        return f"⏩ Steered into current run{status_detail}. Your message arrives after the next tool call."
    if effective_mode == "queue":
        return f"⏳ Queued for the next turn{status_detail}. I'll respond once the current task finishes."
    return f"⚡ Interrupting current task{status_detail}. I'll respond to your message shortly."


def _busy_status_detail(
    running_agent: Any,
    pending_sentinel: Any,
    started_at: float,
    now_ts: float,
) -> str:
    if not running_agent or running_agent is pending_sentinel:
        return ""
    try:
        parts = _busy_status_parts(running_agent, started_at, now_ts)
    except Exception:
        parts = []
    return f" ({', '.join(parts)})" if parts else ""


def _busy_status_parts(running_agent: Any, started_at: float, now_ts: float) -> list[str]:
    summary = running_agent.get_activity_summary()
    parts: list[str] = []
    if started_at:
        elapsed_min = int((now_ts - started_at) / 60)
        if elapsed_min > 0:
            parts.append(f"{elapsed_min} min elapsed")
    if summary.get("max_iterations", 0):
        parts.append(f"iteration {summary.get('api_call_count', 0)}/{summary.get('max_iterations', 0)}")
    if summary.get("current_tool"):
        parts.append(f"running: {summary['current_tool']}")
    return parts


def _with_onboarding_hint(
    message: str,
    effective_mode: str,
    load_gateway_config: Callable[[], dict],
    hermes_home: Path,
    log: logging.Logger,
) -> str:
    try:
        from agent.onboarding import (
            BUSY_INPUT_FLAG,
            busy_input_hint_gateway,
            is_seen,
            mark_seen,
        )
        user_cfg = load_gateway_config()
        if is_seen(user_cfg, BUSY_INPUT_FLAG):
            return message
        hint_mode = effective_mode if effective_mode in {"steer", "queue"} else "interrupt"
        mark_seen(hermes_home / "config.yaml", BUSY_INPUT_FLAG)
        return f"{message}\n\n{busy_input_hint_gateway(hint_mode)}"
    except Exception as onb_err:
        log.debug("Failed to apply busy-input onboarding hint: %s", onb_err)
        return message


async def _try_send_busy_reply(
    adapter: Any,
    event: MessageEvent,
    message: str,
    reply_anchor_for_event: Callable[[MessageEvent], Any],
    thread_metadata_for_source: Callable[[SessionSource, Any], Any],
    log: logging.Logger,
) -> None:
    try:
        await _send_busy_reply(
            adapter, event, message, reply_anchor_for_event,
            thread_metadata_for_source,
        )
    except Exception as exc:
        log.debug("Failed to send busy-ack: %s", exc)


async def _send_busy_reply(
    adapter: Any,
    event: MessageEvent,
    message: str,
    reply_anchor_for_event: Callable[[MessageEvent], Any],
    thread_metadata_for_source: Callable[[SessionSource, Any], Any],
) -> None:
    reply_anchor = reply_anchor_for_event(event)
    await adapter._send_with_retry(
        chat_id=event.source.chat_id,
        content=message,
        reply_to=_reply_to_for_busy_ack(event, reply_anchor),
        metadata=thread_metadata_for_source(event.source, reply_anchor),
    )


def _reply_to_for_busy_ack(event: MessageEvent, reply_anchor: Any) -> Any:
    if event.source.platform == Platform.TELEGRAM and event.source.chat_type == "dm" and event.source.thread_id:
        return reply_anchor
    if event.source.platform == Platform.TELEGRAM and event.source.thread_id:
        return None
    return event.message_id
