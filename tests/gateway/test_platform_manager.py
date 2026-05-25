"""Direct tests for gateway platform lifecycle helpers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platform_manager import (
    adapter_disconnect_timeout_secs,
    connect_adapter_with_timeout,
    handle_adapter_fatal_error,
    pause_failed_platform,
    platform_connect_timeout_secs,
    resume_paused_platform,
    safe_adapter_disconnect,
)
from gateway.platform_reconnect import (
    retry_failed_platform,
    run_platform_reconnect_watcher,
)


def test_timeout_helpers_parse_env_and_ignore_invalid_values(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_ADAPTER_DISCONNECT_TIMEOUT", "0.25")
    monkeypatch.setenv("HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT", "bad")

    assert adapter_disconnect_timeout_secs() == 0.25
    assert platform_connect_timeout_secs(default=7.0) == 7.0


@pytest.mark.asyncio
async def test_connect_adapter_with_timeout_raises_platform_specific_timeout():
    async def hang():
        await asyncio.sleep(60)
        return True

    adapter = SimpleNamespace(connect=hang)

    with pytest.raises(TimeoutError, match="telegram connect timed out after 0.001s"):
        await connect_adapter_with_timeout(
            adapter,
            Platform.TELEGRAM,
            timeout=0.001,
        )


@pytest.mark.asyncio
async def test_safe_adapter_disconnect_swallows_errors_and_none_platform():
    calls = []

    async def fail_disconnect():
        calls.append("disconnect")
        raise RuntimeError("partial init")

    adapter = SimpleNamespace(disconnect=fail_disconnect)

    await safe_adapter_disconnect(adapter, None, timeout=0)

    assert calls == ["disconnect"]


def test_pause_failed_platform_is_idempotent_and_updates_runtime_status():
    failed_platforms = {
        Platform.TELEGRAM: {
            "config": PlatformConfig(enabled=True, token="t"),
            "attempts": 3,
            "next_retry": 30,
        }
    }
    statuses = []

    pause_failed_platform(
        failed_platforms,
        Platform.TELEGRAM,
        reason="manual",
        update_platform_runtime_status=lambda *args, **kwargs: statuses.append(
            (args, kwargs)
        ),
    )
    pause_failed_platform(
        failed_platforms,
        Platform.TELEGRAM,
        reason="second reason",
    )

    info = failed_platforms[Platform.TELEGRAM]
    assert info["paused"] is True
    assert info["pause_reason"] == "manual"
    assert info["next_retry"] == float("inf")
    assert statuses[0][1]["platform_state"] == "paused"


def test_resume_paused_platform_resets_attempts_and_schedules_retry():
    failed_platforms = {
        Platform.TELEGRAM: {
            "config": PlatformConfig(enabled=True, token="t"),
            "attempts": 10,
            "next_retry": float("inf"),
            "paused": True,
            "pause_reason": "auto-paused",
        }
    }
    statuses = []

    assert resume_paused_platform(
        failed_platforms,
        Platform.TELEGRAM,
        now=lambda: 123.0,
        update_platform_runtime_status=lambda *args, **kwargs: statuses.append(
            (args, kwargs)
        ),
    ) is True

    info = failed_platforms[Platform.TELEGRAM]
    assert info["paused"] is False
    assert info["attempts"] == 0
    assert info["next_retry"] == 123.0
    assert "pause_reason" not in info
    assert statuses[0][1]["platform_state"] == "retrying"


@pytest.mark.asyncio
async def test_handle_adapter_fatal_error_queues_retryable_and_keeps_gateway_alive():
    adapter = _fatal_adapter(retryable=True)
    adapters = {Platform.TELEGRAM: adapter}
    delivery_router = SimpleNamespace(adapters=adapters)
    failed_platforms = {}
    stop_gateway = AsyncMock()
    exit_states = []

    await handle_adapter_fatal_error(
        adapter,
        config=SimpleNamespace(
            platforms={
                Platform.TELEGRAM: PlatformConfig(enabled=True, token="t"),
            }
        ),
        adapters=adapters,
        delivery_router=delivery_router,
        failed_platforms=failed_platforms,
        update_platform_runtime_status=lambda *_args, **_kwargs: None,
        set_exit_state=lambda reason, exit_with_failure: exit_states.append(
            (reason, exit_with_failure)
        ),
        stop_gateway=stop_gateway,
        now=lambda: 100.0,
    )

    assert adapter.disconnect_calls == 1
    assert Platform.TELEGRAM not in adapters
    assert delivery_router.adapters is adapters
    assert failed_platforms[Platform.TELEGRAM]["attempts"] == 0
    assert failed_platforms[Platform.TELEGRAM]["next_retry"] == 130.0
    stop_gateway.assert_not_awaited()
    assert exit_states == []


@pytest.mark.asyncio
async def test_handle_adapter_fatal_error_stops_when_nonretryable_leaves_no_adapter():
    adapter = _fatal_adapter(retryable=False)
    adapters = {Platform.TELEGRAM: adapter}
    failed_platforms = {}
    stop_gateway = AsyncMock()
    exit_states = []

    await handle_adapter_fatal_error(
        adapter,
        config=SimpleNamespace(platforms={}),
        adapters=adapters,
        delivery_router=SimpleNamespace(adapters=adapters),
        failed_platforms=failed_platforms,
        update_platform_runtime_status=lambda *_args, **_kwargs: None,
        set_exit_state=lambda reason, exit_with_failure: exit_states.append(
            (reason, exit_with_failure)
        ),
        stop_gateway=stop_gateway,
    )

    assert Platform.TELEGRAM not in adapters
    assert failed_platforms == {}
    assert exit_states == [("fatal error", False)]
    stop_gateway.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_failed_platform_connects_and_removes_retry_entry():
    adapter = _reconnect_adapter(connect_result=True)
    failed_platforms = {
        Platform.TELEGRAM: {
            "config": PlatformConfig(enabled=True, token="t"),
            "attempts": 1,
            "next_retry": 100.0,
        }
    }
    adapters = {}
    delivery_router = SimpleNamespace(adapters=adapters)
    statuses = []
    rebuilt = []

    attempted = await retry_failed_platform(
        Platform.TELEGRAM,
        failed_platforms=failed_platforms,
        adapters=adapters,
        delivery_router=delivery_router,
        create_adapter=lambda platform, config: adapter,
        connect_adapter=lambda adapter, platform: adapter.connect(),
        handle_message=lambda *_args: None,
        handle_adapter_fatal_error=lambda *_args: None,
        session_store=object(),
        handle_active_session_busy_message=lambda *_args: None,
        sync_voice_mode_state_to_adapter=lambda adapter: setattr(
            adapter, "voice_synced", True
        ),
        update_platform_runtime_status=lambda *args, **kwargs: statuses.append(
            (args, kwargs)
        ),
        rebuild_channel_directory=lambda adapters: rebuilt.append(dict(adapters)),
        now=lambda: 100.0,
    )

    assert attempted is True
    assert adapters[Platform.TELEGRAM] is adapter
    assert delivery_router.adapters is adapters
    assert Platform.TELEGRAM not in failed_platforms
    assert adapter.handlers_set == [
        "message",
        "fatal",
        "session_store",
        "busy",
    ]
    assert adapter.voice_synced is True
    assert statuses[0][1]["platform_state"] == "connected"
    assert rebuilt == [{Platform.TELEGRAM: adapter}]


@pytest.mark.asyncio
async def test_retry_failed_platform_failure_updates_backoff_and_pauses_at_threshold():
    adapter = _reconnect_adapter(connect_result=False)
    failed_platforms = {
        Platform.TELEGRAM: {
            "config": PlatformConfig(enabled=True, token="t"),
            "attempts": 9,
            "next_retry": 100.0,
        }
    }

    attempted = await retry_failed_platform(
        Platform.TELEGRAM,
        failed_platforms=failed_platforms,
        adapters={},
        delivery_router=SimpleNamespace(adapters={}),
        create_adapter=lambda platform, config: adapter,
        connect_adapter=lambda adapter, platform: adapter.connect(),
        handle_message=lambda *_args: None,
        handle_adapter_fatal_error=lambda *_args: None,
        session_store=object(),
        handle_active_session_busy_message=lambda *_args: None,
        sync_voice_mode_state_to_adapter=lambda adapter: None,
        update_platform_runtime_status=lambda *_args, **_kwargs: None,
        now=lambda: 100.0,
    )

    info = failed_platforms[Platform.TELEGRAM]
    assert attempted is True
    assert info["attempts"] == 10
    assert info["paused"] is True
    assert info["pause_reason"] == "failed to reconnect"
    assert info["next_retry"] == float("inf")


@pytest.mark.asyncio
async def test_run_platform_reconnect_watcher_idles_without_failed_platforms():
    running = {"value": True}
    sleeps = []
    retries = []

    async def sleep(delay):
        sleeps.append(delay)
        if delay == 1:
            running["value"] = False

    await run_platform_reconnect_watcher(
        is_running=lambda: running["value"],
        failed_platforms={},
        retry_platform=lambda platform: retries.append(platform),
        sleep=sleep,
        initial_delay=0,
        idle_sleep_ticks=30,
        active_sleep_ticks=10,
    )

    assert sleeps == [0, 1]
    assert retries == []


@pytest.mark.asyncio
async def test_run_platform_reconnect_watcher_retries_snapshot_then_waits():
    running = {"value": True}
    sleeps = []
    retries = []
    failed_platforms = {
        Platform.TELEGRAM: {"attempts": 1},
        Platform.DISCORD: {"attempts": 1},
    }

    async def sleep(delay):
        sleeps.append(delay)
        if delay == 1 and retries:
            running["value"] = False

    async def retry_platform(platform):
        retries.append(platform)

    await run_platform_reconnect_watcher(
        is_running=lambda: running["value"],
        failed_platforms=failed_platforms,
        retry_platform=retry_platform,
        sleep=sleep,
        initial_delay=0,
        idle_sleep_ticks=30,
        active_sleep_ticks=10,
    )

    assert retries == [Platform.TELEGRAM, Platform.DISCORD]
    assert sleeps == [0, 1]


def _fatal_adapter(*, retryable: bool):
    async def disconnect():
        adapter.disconnect_calls += 1

    adapter = SimpleNamespace(
        platform=Platform.TELEGRAM,
        fatal_error_code="network_error" if retryable else "auth_error",
        fatal_error_message="network down" if retryable else "fatal error",
        fatal_error_retryable=retryable,
        disconnect=disconnect,
        disconnect_calls=0,
    )
    return adapter


def _reconnect_adapter(*, connect_result: bool):
    async def connect():
        return connect_result

    adapter = SimpleNamespace(
        connect=connect,
        has_fatal_error=False,
        fatal_error_retryable=True,
        fatal_error_code=None,
        fatal_error_message=None,
        handlers_set=[],
        set_message_handler=lambda handler: adapter.handlers_set.append("message"),
        set_fatal_error_handler=lambda handler: adapter.handlers_set.append("fatal"),
        set_session_store=lambda store: adapter.handlers_set.append("session_store"),
        set_busy_session_handler=lambda handler: adapter.handlers_set.append("busy"),
        voice_synced=False,
    )
    return adapter
