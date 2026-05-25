from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.process_watcher import run_process_watcher
from gateway.session import SessionSource


class _FakeRegistry:
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def get(self, _session_id):
        if self._sessions:
            return self._sessions.pop(0)
        return None

    def is_completion_consumed(self, _session_id):
        return False


async def _instant_sleep(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_run_process_watcher_sends_running_update_in_all_mode():
    adapter = SimpleNamespace(send=AsyncMock(), handle_message=AsyncMock())
    registry = _FakeRegistry(
        [
            SimpleNamespace(output_buffer="building...\n", exited=False, exit_code=None),
            None,
        ]
    )

    await run_process_watcher(
        {"session_id": "proc-1", "check_interval": 0, "platform": "telegram", "chat_id": "123"},
        adapters={Platform.TELEGRAM: adapter},
        notify_mode="all",
        source_resolver=lambda _evt: None,
        process_registry=registry,
        sleep_fn=_instant_sleep,
    )

    adapter.send.assert_awaited_once()
    assert "is still running" in adapter.send.await_args.args[1]
    assert "building" in adapter.send.await_args.args[1]


@pytest.mark.asyncio
async def test_run_process_watcher_injects_agent_completion_with_message_anchor():
    adapter = SimpleNamespace(send=AsyncMock(), handle_message=AsyncMock())
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_type="dm",
        thread_id="topic-7",
    )
    registry = _FakeRegistry(
        [
            SimpleNamespace(
                output_buffer="finished\n",
                exited=True,
                exit_code=0,
                command="sleep 1",
            )
        ]
    )

    await run_process_watcher(
        {
            "session_id": "proc-2",
            "check_interval": 0,
            "platform": "telegram",
            "chat_id": "123",
            "thread_id": "topic-7",
            "message_id": "msg-42",
            "notify_on_complete": True,
        },
        adapters={Platform.TELEGRAM: adapter},
        notify_mode="all",
        source_resolver=lambda _evt: source,
        process_registry=registry,
        sleep_fn=_instant_sleep,
    )

    adapter.send.assert_not_called()
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.internal is True
    assert event.message_id == "msg-42"
    assert event.source is source
    assert "Background process proc-2 completed" in event.text
