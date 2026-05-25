"""Agent shutdown lifecycle helpers for the gateway."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

logger = logging.getLogger(__name__)


async def drain_active_agents(
    *,
    running_agents: MutableMapping[str, Any],
    snapshot_running_agents: Callable[[], dict[str, Any]],
    running_agent_count: Callable[[], int],
    update_runtime_status: Callable[[str], None],
    timeout: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Wait for active agents to finish, returning their initial snapshot."""
    snapshot = snapshot_running_agents()
    clock = monotonic or asyncio.get_running_loop().time
    status = _DrainStatus(running_agent_count(), 0.0)

    def maybe_update_status(force: bool = False) -> None:
        active_count = running_agent_count()
        now = clock()
        if force or active_count != status.active_count or now - status.last_at >= 1.0:
            update_runtime_status("draining")
            status.active_count = active_count
            status.last_at = now

    if not running_agents:
        maybe_update_status(force=True)
        return snapshot, False

    maybe_update_status(force=True)
    if timeout <= 0:
        return snapshot, True

    deadline = clock() + timeout
    while running_agents and clock() < deadline:
        maybe_update_status()
        await sleep(0.1)
    maybe_update_status(force=True)
    return snapshot, bool(running_agents)


class _DrainStatus:
    def __init__(self, active_count: int, last_at: float) -> None:
        self.active_count = active_count
        self.last_at = last_at


def interrupt_running_agents(
    *,
    running_agents: Mapping[str, Any],
    pending_sentinel: Any,
    reason: str,
    log: logging.Logger = logger,
) -> None:
    """Best-effort interrupt for all non-pending running agents."""
    for session_key, agent in list(running_agents.items()):
        if agent is pending_sentinel:
            continue
        try:
            agent.interrupt(reason)
            log.debug("Interrupted running agent for session %s during shutdown", session_key)
        except Exception as exc:
            log.debug("Failed interrupting agent during shutdown: %s", exc)


def finalize_shutdown_agents(
    *,
    active_agents: Mapping[str, Any],
    cleanup_agent_resources: Callable[[Any], None],
    invoke_hook: Callable[..., Any],
) -> None:
    """Run final session hooks and clean each active agent."""
    for agent in active_agents.values():
        try:
            invoke_hook(
                "on_session_finalize",
                session_id=getattr(agent, "session_id", None),
                platform="gateway",
            )
        except Exception:
            pass
        cleanup_agent_resources(agent)


def cleanup_agent_resources(
    agent: Any,
    *,
    cleanup_stale_async_clients: Callable[[], None] | None = None,
) -> None:
    """Best-effort cleanup for temporary or cached agent instances."""
    if agent is None:
        return
    _shutdown_memory_provider(agent)
    _close_agent(agent)
    _cleanup_stale_auxiliary_clients(cleanup_stale_async_clients)


def _shutdown_memory_provider(agent: Any) -> None:
    try:
        if not hasattr(agent, "shutdown_memory_provider"):
            return
        session_messages = getattr(agent, "_session_messages", None)
        if isinstance(session_messages, list):
            agent.shutdown_memory_provider(session_messages)
        else:
            agent.shutdown_memory_provider()
    except Exception:
        pass


def _close_agent(agent: Any) -> None:
    try:
        if hasattr(agent, "close"):
            agent.close()
    except Exception:
        pass


def _cleanup_stale_auxiliary_clients(cleanup_stale_async_clients: Callable[[], None] | None) -> None:
    try:
        if cleanup_stale_async_clients is None:
            from agent.auxiliary_client import cleanup_stale_async_clients as cleanup
        else:
            cleanup = cleanup_stale_async_clients
        cleanup()
    except Exception:
        pass
