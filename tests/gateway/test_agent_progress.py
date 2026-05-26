import asyncio
import queue

import pytest

from gateway.agent_progress import send_agent_progress_messages
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.session import SessionSource


class ProgressAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.TELEGRAM)
        self.sent = []
        self.edits = []
        self.typing = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id=f"m-{len(self.sent)}")

    async def edit_message(self, chat_id, message_id, content, metadata=None) -> SendResult:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id=message_id)

    async def send_typing(self, chat_id, metadata=None) -> None:
        self.typing.append({"chat_id": chat_id, "metadata": metadata})

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        thread_id="topic-1",
    )


async def _run_pump_once(progress_queue, **kwargs) -> None:
    task = asyncio.create_task(
        send_agent_progress_messages(
            progress_queue,
            source=_source(),
            adapter=kwargs.pop("adapter"),
            progress_metadata={"thread_id": "topic-1"},
            progress_reply_to=None,
            cleanup_progress=kwargs.pop("cleanup_progress", False),
            cleanup_message_ids=kwargs.pop("cleanup_message_ids", []),
            run_still_current=kwargs.pop("run_still_current", lambda: True),
            agent_holder=kwargs.pop("agent_holder", [None]),
            last_progress_msg=kwargs.pop("last_progress_msg", [None]),
            repeat_count=kwargs.pop("repeat_count", [0]),
            progress_edit_interval=0.01,
            idle_sleep=0.01,
            **kwargs,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_send_agent_progress_messages_sends_and_edits_progress_bubble() -> None:
    progress_queue = queue.Queue()
    progress_queue.put("terminal: pwd")
    progress_queue.put("browser: open")
    adapter = ProgressAdapter()

    await _run_pump_once(progress_queue, adapter=adapter)

    assert adapter.sent[0]["content"] == "terminal: pwd"
    assert adapter.sent[0]["metadata"] == {"thread_id": "topic-1"}
    assert adapter.edits[-1]["content"] == "terminal: pwd\nbrowser: open"
    assert adapter.edits[-1]["metadata"] == {"thread_id": "topic-1"}


@pytest.mark.asyncio
async def test_send_agent_progress_messages_tracks_cleanup_message_ids() -> None:
    progress_queue = queue.Queue()
    progress_queue.put("terminal: pwd")
    adapter = ProgressAdapter()
    cleanup_ids: list[str] = []

    await _run_pump_once(
        progress_queue,
        adapter=adapter,
        cleanup_progress=True,
        cleanup_message_ids=cleanup_ids,
    )

    assert cleanup_ids == ["m-1"]


@pytest.mark.asyncio
async def test_send_agent_progress_messages_drops_queue_when_run_is_stale() -> None:
    progress_queue = queue.Queue()
    progress_queue.put("terminal: pwd")
    adapter = ProgressAdapter()

    await _run_pump_once(
        progress_queue,
        adapter=adapter,
        run_still_current=lambda: False,
    )

    assert adapter.sent == []
    assert progress_queue.empty()
