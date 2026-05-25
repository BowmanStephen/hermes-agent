"""Background process watcher helpers for GatewayRunner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


logger = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]
SourceResolver = Callable[[dict], SessionSource | None]


async def run_process_watcher(
    watcher: dict,
    *,
    adapters: Mapping[Any, Any],
    notify_mode: str,
    source_resolver: SourceResolver,
    process_registry: Any = None,
    sleep_fn: SleepFn = asyncio.sleep,
    logger: logging.Logger = logger,
) -> None:
    """Periodically check a background process and push updates to the user."""

    if process_registry is None:
        from tools.process_registry import process_registry as process_registry

    session_id = watcher["session_id"]
    interval = watcher["check_interval"]
    session_key = watcher.get("session_key", "")
    platform_name = watcher.get("platform", "")
    chat_id = watcher.get("chat_id", "")
    thread_id = watcher.get("thread_id", "")
    user_id = watcher.get("user_id", "")
    user_name = watcher.get("user_name", "")
    message_id = str(watcher.get("message_id") or "").strip() or None
    agent_notify = watcher.get("notify_on_complete", False)

    logger.debug(
        "Process watcher started: %s (every %ss, notify=%s, agent_notify=%s)",
        session_id,
        interval,
        notify_mode,
        agent_notify,
    )

    if notify_mode == "off" and not agent_notify:
        while True:
            await sleep_fn(interval)
            session = process_registry.get(session_id)
            if session is None or session.exited:
                break
        logger.debug("Process watcher ended (silent): %s", session_id)
        return

    last_output_len = 0
    while True:
        await sleep_fn(interval)

        session = process_registry.get(session_id)
        if session is None:
            break

        current_output_len = len(session.output_buffer)
        has_new_output = current_output_len > last_output_len
        last_output_len = current_output_len

        if session.exited:
            if agent_notify and not process_registry.is_completion_consumed(session_id):
                await _inject_agent_completion_notification(
                    watcher={
                        "session_id": session_id,
                        "session_key": session_key,
                        "platform": platform_name,
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "user_name": user_name,
                    },
                    session=session,
                    adapters=adapters,
                    source_resolver=source_resolver,
                    message_id=message_id,
                    logger=logger,
                )
                break

            should_notify = (
                notify_mode in {"all", "result"}
                or (notify_mode == "error" and session.exit_code not in {0, None})
            )
            if should_notify:
                new_output = session.output_buffer[-1000:] if session.output_buffer else ""
                message_text = (
                    f"[Background process {session_id} finished with exit code {session.exit_code}~ "
                    f"Here's the final output:\n{new_output}]"
                )
                await _send_watcher_message(
                    adapters=adapters,
                    platform_name=platform_name,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    message_text=message_text,
                    logger=logger,
                )
            break

        if has_new_output and notify_mode == "all" and not agent_notify:
            new_output = session.output_buffer[-500:] if session.output_buffer else ""
            message_text = (
                f"[Background process {session_id} is still running~ "
                f"New output:\n{new_output}]"
            )
            await _send_watcher_message(
                adapters=adapters,
                platform_name=platform_name,
                chat_id=chat_id,
                thread_id=thread_id,
                message_text=message_text,
                logger=logger,
            )

    logger.debug("Process watcher ended: %s", session_id)


async def _send_watcher_message(
    *,
    adapters: Mapping[Any, Any],
    platform_name: str,
    chat_id: str,
    thread_id: str,
    message_text: str,
    logger: logging.Logger,
) -> None:
    adapter = None
    for p, a in adapters.items():
        if getattr(p, "value", str(p)) == platform_name:
            adapter = a
            break
    if adapter and chat_id:
        try:
            send_meta = {"thread_id": thread_id} if thread_id else None
            await adapter.send(chat_id, message_text, metadata=send_meta)
        except Exception as e:
            logger.error("Watcher delivery error: %s", e)


async def _inject_agent_completion_notification(
    *,
    watcher: dict,
    session: Any,
    adapters: Mapping[Any, Any],
    source_resolver: SourceResolver,
    message_id: str | None,
    logger: logging.Logger,
) -> None:
    from tools.ansi_strip import strip_ansi

    session_id = watcher["session_id"]
    session_key = watcher.get("session_key", "")
    raw = strip_ansi(session.output_buffer) if session.output_buffer else ""
    limit = 2000
    if len(raw) > limit:
        tail = raw[-limit:]
        nl = tail.find("\n")
        tail = tail[nl + 1:] if nl != -1 else tail
        out = f"[… output truncated — showing last {len(tail)} chars]\n{tail}"
    else:
        out = raw
    synth_text = (
        f"[IMPORTANT: Background process {session_id} completed "
        f"(exit code {session.exit_code}).\n"
        f"Command: {session.command}\n"
        f"Output:\n{out}]"
    )
    source = source_resolver(watcher)
    if not source:
        logger.warning(
            "Dropping completion notification with no routing metadata for process %s",
            session_id,
        )
        return

    adapter = None
    for p, a in adapters.items():
        if p == source.platform:
            adapter = a
            break
    if adapter and source.chat_id:
        try:
            synth_event = MessageEvent(
                text=synth_text,
                message_type=MessageType.TEXT,
                source=source,
                internal=True,
                message_id=message_id,
            )
            logger.info(
                "Process %s finished — injecting agent notification for session %s chat=%s thread=%s",
                session_id,
                session_key,
                source.chat_id,
                source.thread_id,
            )
            await adapter.handle_message(synth_event)
        except Exception as e:
            logger.error("Agent notify injection error: %s", e)
