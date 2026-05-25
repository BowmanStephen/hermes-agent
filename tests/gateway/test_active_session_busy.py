from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.active_session_busy import handle_active_session_busy_message
from gateway.config import Platform
from gateway.platforms.base import (
    MessageEvent,
    MessageType,
    SessionSource,
    build_session_key,
)


def _event(text: str = "follow up") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.SLACK,
            chat_id="C123",
            chat_type="channel",
            user_id="U123",
            user_name="Ada",
            thread_id="T123",
        ),
        message_id="msg-1",
    )


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter._pending_messages = {}
    adapter._send_with_retry = AsyncMock()
    return adapter


def _seen_config() -> dict:
    return {"onboarding": {"seen": {"busy_input_prompt": True}}}


@pytest.mark.asyncio
async def test_queue_mode_merges_pending_message_and_sends_status_ack(tmp_path: Path) -> None:
    event = _event("add this after the tool finishes")
    session_key = build_session_key(event.source)
    adapter = _adapter()
    agent = MagicMock()
    agent.get_activity_summary.return_value = {
        "api_call_count": 2,
        "max_iterations": 5,
        "current_tool": "terminal",
    }
    busy_ack_ts: dict[str, float] = {}

    result = await handle_active_session_busy_message(
        event=event,
        session_key=session_key,
        is_user_authorized=lambda _source: True,
        adapters={Platform.SLACK: adapter},
        running_agents={session_key: agent},
        running_agents_ts={session_key: 10.0},
        busy_ack_ts=busy_ack_ts,
        busy_input_mode="queue",
        draining=False,
        pending_sentinel=object(),
        queue_during_drain_enabled=lambda: False,
        queue_or_replace_pending_event=lambda _key, _event: None,
        status_action_gerund=lambda: "restarting",
        reply_anchor_for_event=lambda _event: None,
        thread_metadata_for_source=lambda _source, _anchor: {"thread": "T123"},
        load_gateway_config=_seen_config,
        hermes_home=tmp_path,
        now=lambda: 130.0,
    )

    assert result is True
    assert adapter._pending_messages[session_key] is event
    agent.interrupt.assert_not_called()
    adapter._send_with_retry.assert_awaited_once()
    content = adapter._send_with_retry.call_args.kwargs["content"]
    assert "Queued for the next turn" in content
    assert "2 min elapsed" in content
    assert "iteration 2/5" in content
    assert "running: terminal" in content
    assert busy_ack_ts[session_key] == 130.0


@pytest.mark.asyncio
async def test_unauthorized_busy_message_is_dropped_before_queue_or_ack(tmp_path: Path) -> None:
    event = _event("ignore the operator")
    session_key = build_session_key(event.source)
    adapter = _adapter()
    agent = MagicMock()

    result = await handle_active_session_busy_message(
        event=event,
        session_key=session_key,
        is_user_authorized=lambda _source: False,
        adapters={Platform.SLACK: adapter},
        running_agents={session_key: agent},
        running_agents_ts={},
        busy_ack_ts={},
        busy_input_mode="interrupt",
        draining=False,
        pending_sentinel=object(),
        queue_during_drain_enabled=lambda: False,
        queue_or_replace_pending_event=lambda _key, _event: None,
        status_action_gerund=lambda: "restarting",
        reply_anchor_for_event=lambda _event: None,
        thread_metadata_for_source=lambda _source, _anchor: None,
        load_gateway_config=_seen_config,
        hermes_home=tmp_path,
    )

    assert result is True
    assert adapter._pending_messages == {}
    agent.interrupt.assert_not_called()
    adapter._send_with_retry.assert_not_awaited()
