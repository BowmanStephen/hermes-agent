import asyncio
from unittest.mock import MagicMock

import pytest

from gateway.agent_shutdown import (
    cleanup_agent_resources,
    drain_active_agents,
    finalize_shutdown_agents,
    interrupt_running_agents,
)


@pytest.mark.asyncio
async def test_drain_active_agents_reports_timeout_and_updates_status() -> None:
    running_agents = {"s1": object()}
    status_updates: list[str] = []

    snapshot, timed_out = await drain_active_agents(
        running_agents=running_agents,
        snapshot_running_agents=lambda: dict(running_agents),
        running_agent_count=lambda: len(running_agents),
        update_runtime_status=status_updates.append,
        timeout=0,
    )

    assert snapshot == running_agents
    assert timed_out is True
    assert status_updates == ["draining"]


@pytest.mark.asyncio
async def test_drain_active_agents_waits_until_empty() -> None:
    running_agents = {"s1": object()}
    sleeps = 0

    async def _sleep(_delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        running_agents.clear()
    times = iter([0.0, 0.0, 0.1, 0.2, 0.3])

    snapshot, timed_out = await drain_active_agents(
        running_agents=running_agents,
        snapshot_running_agents=lambda: {"s1": "agent"},
        running_agent_count=lambda: len(running_agents),
        update_runtime_status=lambda _status: None,
        timeout=1,
        sleep=_sleep,
        monotonic=times.__next__,
    )

    assert snapshot == {"s1": "agent"}
    assert timed_out is False
    assert sleeps == 1


def test_interrupt_running_agents_skips_pending_sentinel_and_continues() -> None:
    sentinel = object()
    live = MagicMock()
    bad = MagicMock()
    bad.interrupt.side_effect = RuntimeError("boom")

    interrupt_running_agents(
        running_agents={"pending": sentinel, "live": live, "bad": bad},
        pending_sentinel=sentinel,
        reason="restart",
    )

    live.interrupt.assert_called_once_with("restart")
    bad.interrupt.assert_called_once_with("restart")


def test_finalize_shutdown_agents_invokes_hook_and_cleanup() -> None:
    first = MagicMock(session_id="one")
    second = MagicMock(session_id="two")
    hooks: list[tuple[str, str | None, str]] = []
    cleaned: list[object] = []

    finalize_shutdown_agents(
        active_agents={"one": first, "two": second},
        cleanup_agent_resources=cleaned.append,
        invoke_hook=lambda name, **kwargs: hooks.append(
            (name, kwargs.get("session_id"), kwargs.get("platform"))
        ),
    )

    assert hooks == [
        ("on_session_finalize", "one", "gateway"),
        ("on_session_finalize", "two", "gateway"),
    ]
    assert cleaned == [first, second]


def test_cleanup_agent_resources_forwards_session_messages_and_closes() -> None:
    transcript = [{"role": "user", "content": "hello"}]
    agent = MagicMock()
    agent._session_messages = transcript
    stale_cleanup = MagicMock()

    cleanup_agent_resources(agent, cleanup_stale_async_clients=stale_cleanup)

    agent.shutdown_memory_provider.assert_called_once_with(transcript)
    agent.close.assert_called_once()
    stale_cleanup.assert_called_once()


def test_cleanup_agent_resources_keeps_closing_after_shutdown_error() -> None:
    agent = MagicMock()
    agent.shutdown_memory_provider.side_effect = RuntimeError("boom")
    stale_cleanup = MagicMock()

    cleanup_agent_resources(agent, cleanup_stale_async_clients=stale_cleanup)

    agent.close.assert_called_once()
    stale_cleanup.assert_called_once()
