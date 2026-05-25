import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.delivery_notifications import (
    notify_active_sessions_of_shutdown,
    send_home_channel_startup_notifications,
    send_restart_notification,
    send_update_notification,
)
from gateway.platforms.base import SendResult
from gateway.session import SessionSource


class _SessionStore:
    def __init__(self, entries):
        self._entries = entries
        self.loaded = False

    def _ensure_loaded(self):
        self.loaded = True


@pytest.mark.asyncio
async def test_send_restart_notification_delivers_thread_target_and_cleans_marker(tmp_path):
    notify_path = tmp_path / ".restart_notify.json"
    notify_path.write_text(
        json.dumps(
            {
                "platform": "telegram",
                "chat_id": "parent-42",
                "thread_id": "topic-7",
            }
        )
    )
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="restart"))
    )
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )

    delivered = await send_restart_notification(
        tmp_path,
        adapters={Platform.TELEGRAM: adapter},
        config=config,
    )

    assert delivered == ("telegram", "parent-42", "topic-7")
    adapter.send.assert_called_once_with(
        "parent-42",
        "♻ Gateway restarted successfully. Your session continues.",
        metadata={"thread_id": "topic-7"},
    )
    assert not notify_path.exists()


@pytest.mark.asyncio
async def test_send_home_channel_startup_notifications_sends_each_home_once():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="home"))
    )
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
        thread_id="topic-7",
    )

    delivered = await send_home_channel_startup_notifications(
        adapters={Platform.TELEGRAM: adapter},
        config=config,
        skip_targets={("telegram", "other-chat", None)},
    )

    assert delivered == {("telegram", "home-42", "topic-7")}
    adapter.send.assert_called_once_with(
        "home-42",
        "♻️ Gateway online — Hermes is back and ready.",
        metadata={"thread_id": "topic-7"},
    )


@pytest.mark.asyncio
async def test_send_update_notification_defers_while_update_still_running(tmp_path):
    pending_path = tmp_path / ".update_pending.json"
    pending_path.write_text(
        json.dumps(
            {
                "platform": "telegram",
                "chat_id": "home-42",
            }
        )
    )
    adapter = SimpleNamespace(send=AsyncMock())

    result = await send_update_notification(
        tmp_path,
        adapters={Platform.TELEGRAM: adapter},
    )

    assert result is False
    adapter.send.assert_not_called()
    assert pending_path.exists()
    assert not (tmp_path / ".update_pending.claimed.json").exists()


@pytest.mark.asyncio
async def test_send_update_notification_sends_final_threaded_output_and_cleans_files(tmp_path):
    pending_path = tmp_path / ".update_pending.json"
    output_path = tmp_path / ".update_output.txt"
    exit_code_path = tmp_path / ".update_exit_code"
    pending_path.write_text(
        json.dumps(
            {
                "platform": "telegram",
                "chat_id": "home-42",
                "thread_id": "topic-7",
            }
        )
    )
    output_path.write_text("\x1b[32mUpdate complete\x1b[0m")
    exit_code_path.write_text("0")
    adapter = SimpleNamespace(send=AsyncMock())

    result = await send_update_notification(
        tmp_path,
        adapters={Platform.TELEGRAM: adapter},
    )

    assert result is True
    adapter.send.assert_called_once()
    chat_id, message = adapter.send.call_args.args
    assert chat_id == "home-42"
    assert "Update complete" in message
    assert "\x1b[" not in message
    assert adapter.send.call_args.kwargs["metadata"] == {"thread_id": "topic-7"}
    assert not pending_path.exists()
    assert not output_path.exists()
    assert not exit_code_path.exists()


@pytest.mark.asyncio
async def test_notify_active_sessions_of_shutdown_uses_origin_and_dedupes_home_channel():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="parent-42",
        chat_type="group",
        thread_id="topic-7",
    )
    session_key = "agent:main:telegram:group:parent-42:topic-7"
    session_store = _SessionStore({session_key: SimpleNamespace(origin=source)})
    adapter = SimpleNamespace(send=AsyncMock(return_value=SendResult(success=True)))
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="parent-42",
        name="Ops Topic",
        thread_id="topic-7",
    )

    notified = await notify_active_sessions_of_shutdown(
        active_session_keys=[session_key],
        adapters={Platform.TELEGRAM: adapter},
        config=config,
        restart_requested=False,
        session_store=session_store,
        get_cached_session_source=lambda _key: None,
    )

    assert notified == {("telegram", "parent-42", "topic-7")}
    assert session_store.loaded is True
    adapter.send.assert_awaited_once_with(
        "parent-42",
        "⚠️ Gateway shutting down — Your current task will be interrupted.",
        metadata={"thread_id": "topic-7"},
    )


@pytest.mark.asyncio
async def test_notify_active_sessions_of_shutdown_honors_platform_suppression():
    adapter = SimpleNamespace(send=AsyncMock())
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    config.platforms[Platform.TELEGRAM].gateway_restart_notification = False

    notified = await notify_active_sessions_of_shutdown(
        active_session_keys=["agent:main:telegram:dm:123"],
        adapters={Platform.TELEGRAM: adapter},
        config=config,
        restart_requested=True,
        session_store=None,
        get_cached_session_source=lambda _key: None,
    )

    assert notified == set()
    adapter.send.assert_not_called()
