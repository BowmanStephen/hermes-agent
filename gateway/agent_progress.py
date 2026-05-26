"""Agent progress-message delivery helpers for gateway runs."""

from __future__ import annotations

import asyncio
import inspect
import logging
import queue
import time
from typing import Any, Callable, Optional

from gateway.platforms.base import BasePlatformAdapter
from gateway.session import SessionSource


async def send_agent_progress_messages(
    progress_queue: queue.Queue,
    *,
    source: SessionSource,
    adapter: Any,
    progress_metadata: Optional[dict[str, Any]],
    progress_reply_to: Optional[str],
    cleanup_progress: bool,
    cleanup_message_ids: list[str],
    run_still_current: Callable[[], bool],
    agent_holder: list[Any],
    last_progress_msg: list[Any],
    repeat_count: list[int],
    log: logging.Logger | None = None,
    progress_edit_interval: float = 1.5,
    idle_sleep: float = 0.3,
) -> None:
    """Drain gateway tool-progress events into editable platform messages."""
    logger = log or logging.getLogger(__name__)
    if not progress_queue or not adapter:
        return

    if type(adapter).edit_message is BasePlatformAdapter.edit_message:
        _drain_queue(progress_queue)
        return

    progress_lines: list[Any] = []
    progress_msg_id = None
    can_edit = True
    last_edit_ts = 0.0

    progress_len_fn = (
        adapter.message_len_fn
        if isinstance(adapter, BasePlatformAdapter)
        else len
    )
    try:
        raw_progress_limit = int(getattr(adapter, "MAX_MESSAGE_LENGTH", 4000) or 4000)
    except Exception:
        raw_progress_limit = 4000
    progress_text_limit = max(
        1,
        raw_progress_limit - (64 if raw_progress_limit > 128 else 0),
    )

    edit_accepts_metadata = _edit_accepts_metadata(adapter, progress_metadata)

    async def edit_progress_message(message_id: str, content: str):
        kwargs = {
            "chat_id": source.chat_id,
            "message_id": message_id,
            "content": content,
        }
        if edit_accepts_metadata:
            kwargs["metadata"] = progress_metadata
        return await adapter.edit_message(**kwargs)

    def progress_text(lines: list) -> str:
        return "\n".join(str(line) for line in lines)

    def split_progress_groups(lines: list) -> list[list]:
        groups: list[list] = []
        current: list = []
        for line in lines:
            candidate = current + [line]
            if current and progress_len_fn(progress_text(candidate)) > progress_text_limit:
                groups.append(current)
                current = [line]
            else:
                current = candidate
        if current:
            groups.append(current)
        return groups

    def track_progress_result(result) -> None:
        if (
            cleanup_progress
            and getattr(result, "success", False)
            and getattr(result, "message_id", None)
        ):
            cleanup_message_ids.append(str(result.message_id))

    async def send_progress_text(text: str):
        result = await adapter.send(
            chat_id=source.chat_id,
            content=text,
            reply_to=progress_reply_to,
            metadata=progress_metadata,
        )
        track_progress_result(result)
        return result

    async def roll_progress_overflow_if_needed() -> bool:
        nonlocal progress_msg_id, progress_lines, can_edit
        if not progress_lines or not can_edit:
            return False
        groups = split_progress_groups(progress_lines)
        if len(groups) <= 1:
            return False

        first_text = progress_text(groups[0])
        if progress_msg_id is not None:
            result = await edit_progress_message(progress_msg_id, first_text)
            if not result.success:
                can_edit = False
                return False
        else:
            result = await send_progress_text(first_text)
            if result.success and result.message_id:
                progress_msg_id = result.message_id

        for group in groups[1:]:
            result = await send_progress_text(progress_text(group))
            if result.success and result.message_id:
                progress_msg_id = result.message_id

        progress_lines = groups[-1]
        return True

    async def drain_cancelled_progress() -> None:
        nonlocal progress_msg_id, progress_lines
        while not progress_queue.empty():
            try:
                raw = progress_queue.get_nowait()
                if isinstance(raw, tuple) and len(raw) == 3 and raw[0] == "__dedup__":
                    _, base_msg, count = raw
                    if progress_lines:
                        progress_lines[-1] = f"{base_msg} (×{count + 1})"
                        await roll_progress_overflow_if_needed()
                elif isinstance(raw, tuple) and len(raw) >= 1 and raw[0] == "__reset__":
                    await roll_progress_overflow_if_needed()
                    if can_edit and progress_lines and progress_msg_id:
                        try:
                            await edit_progress_message(
                                progress_msg_id,
                                progress_text(progress_lines),
                            )
                        except Exception:
                            pass
                    progress_msg_id = None
                    progress_lines = []
                    last_progress_msg[0] = None
                    repeat_count[0] = 0
                else:
                    progress_lines.append(raw)
                    await roll_progress_overflow_if_needed()
            except Exception:
                break
        if can_edit and progress_lines and progress_msg_id:
            await roll_progress_overflow_if_needed()
        if can_edit and progress_lines and progress_msg_id:
            try:
                await edit_progress_message(progress_msg_id, progress_text(progress_lines))
            except Exception:
                pass

    while True:
        try:
            if not run_still_current():
                _drain_queue(progress_queue)
                return

            raw = progress_queue.get_nowait()

            try:
                agent_for_interrupt = agent_holder[0] if agent_holder else None
                if agent_for_interrupt is not None and getattr(
                    agent_for_interrupt, "is_interrupted", False
                ):
                    await asyncio.sleep(0)
                    continue
            except Exception:
                pass

            if isinstance(raw, tuple) and len(raw) == 3 and raw[0] == "__dedup__":
                _, base_msg, count = raw
                if progress_lines:
                    progress_lines[-1] = f"{base_msg} (×{count + 1})"
                msg = progress_lines[-1] if progress_lines else base_msg
            elif isinstance(raw, tuple) and len(raw) >= 1 and raw[0] == "__reset__":
                progress_msg_id = None
                progress_lines = []
                last_progress_msg[0] = None
                repeat_count[0] = 0
                continue
            else:
                msg = raw
                progress_lines.append(msg)

            if await roll_progress_overflow_if_needed():
                last_edit_ts = time.monotonic()
                await asyncio.sleep(idle_sleep)
                if run_still_current():
                    await adapter.send_typing(source.chat_id, metadata=progress_metadata)
                continue

            now = time.monotonic()
            remaining = progress_edit_interval - (now - last_edit_ts)
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue

            if not run_still_current():
                return

            if can_edit and progress_msg_id is not None:
                result = await edit_progress_message(
                    progress_msg_id,
                    "\n".join(progress_lines),
                )
                if not result.success:
                    if getattr(result, "retryable", False):
                        logger.debug(
                            "[%s] Transient edit failure -- keeping can_edit=True",
                            adapter.name,
                        )
                        continue
                    err = (getattr(result, "error", "") or "").lower()
                    if "flood" in err or "retry after" in err:
                        logger.info(
                            "[%s] Progress edit flood control, backing off",
                            adapter.name,
                        )
                        last_edit_ts = time.monotonic()
                    else:
                        can_edit = False
                    flood_result = await adapter.send(
                        chat_id=source.chat_id,
                        content=msg,
                        reply_to=progress_reply_to,
                        metadata=progress_metadata,
                    )
                    track_progress_result(flood_result)
            else:
                content = "\n".join(progress_lines) if can_edit else msg
                result = await adapter.send(
                    chat_id=source.chat_id,
                    content=content,
                    reply_to=progress_reply_to,
                    metadata=progress_metadata,
                )
                if result.success and result.message_id:
                    progress_msg_id = result.message_id
                    if cleanup_progress:
                        cleanup_message_ids.append(str(result.message_id))

            last_edit_ts = time.monotonic()
            await asyncio.sleep(idle_sleep)
            if run_still_current():
                await adapter.send_typing(source.chat_id, metadata=progress_metadata)

        except queue.Empty:
            await asyncio.sleep(idle_sleep)
        except asyncio.CancelledError:
            await drain_cancelled_progress()
            return
        except Exception as exc:
            logger.error("Progress message error: %s", exc)
            await asyncio.sleep(1)


def _drain_queue(progress_queue: queue.Queue) -> None:
    while not progress_queue.empty():
        try:
            progress_queue.get_nowait()
        except Exception:
            break


def _edit_accepts_metadata(adapter: Any, progress_metadata: Optional[dict[str, Any]]) -> bool:
    if not progress_metadata:
        return False
    try:
        edit_params = inspect.signature(adapter.edit_message).parameters
        return (
            "metadata" in edit_params
            or any(
                param.kind is inspect.Parameter.VAR_KEYWORD
                for param in edit_params.values()
            )
        )
    except (TypeError, ValueError):
        return False
