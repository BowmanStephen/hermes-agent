"""SessionManager helper seams for GatewayRunner."""

from __future__ import annotations

import dataclasses
import logging
from collections import OrderedDict
from collections.abc import MutableMapping
from typing import Any, Optional, TypeVar

from gateway.session import SessionSource, build_session_key


_SourceT = TypeVar("_SourceT")


def resolve_session_key_for_source(
    source: SessionSource,
    *,
    session_store: object = None,
    config: object = None,
) -> str:
    """Resolve a session key using SessionStore when available, then config fallback."""

    if session_store is not None:
        try:
            session_key = session_store._generate_session_key(source)
            if isinstance(session_key, str) and session_key:
                return session_key
        except Exception:
            pass
    return build_session_key(
        source,
        group_sessions_per_user=getattr(config, "group_sessions_per_user", True),
        thread_sessions_per_user=getattr(config, "thread_sessions_per_user", False),
    )


def cache_session_source(
    cached_sources: Optional[MutableMapping[str, _SourceT]],
    session_key: str,
    source: _SourceT,
    *,
    max_size: int = 512,
    logger: logging.Logger | None = None,
) -> MutableMapping[str, _SourceT] | None:
    """Cache a live session source by key, preserving LRU order."""

    if not session_key or source is None:
        return cached_sources
    if cached_sources is None:
        cached_sources = OrderedDict()
    try:
        cached_sources[session_key] = dataclasses.replace(source)
    except Exception:
        if logger is not None:
            logger.debug("Failed to cache live session source for %s", session_key, exc_info=True)
        return cached_sources
    try:
        cached_sources.move_to_end(session_key)
        while len(cached_sources) > max_size:
            cached_sources.popitem(last=False)
    except Exception:
        pass
    return cached_sources


def get_cached_session_source(
    cached_sources: Optional[MutableMapping[str, _SourceT]],
    session_key: str,
) -> _SourceT | None:
    """Return a cached session source and mark it as recently used."""

    if not session_key or not cached_sources:
        return None
    source = cached_sources.get(session_key)
    if source is not None:
        try:
            cached_sources.move_to_end(session_key)
        except Exception:
            pass
    return source


def begin_session_run_generation(
    generations: MutableMapping[str, int],
    session_key: str,
) -> int:
    """Claim a fresh run generation token for one session."""

    if not session_key:
        return 0
    next_generation = int(generations.get(session_key, 0)) + 1
    generations[session_key] = next_generation
    return next_generation


def invalidate_session_run_generation(
    generations: MutableMapping[str, int],
    session_key: str,
) -> int:
    """Invalidate any in-flight run generation for one session."""

    return begin_session_run_generation(generations, session_key)


def is_session_run_current(
    generations: MutableMapping[str, int] | None,
    session_key: str,
    generation: int,
) -> bool:
    """Return True when generation is still current for one session."""

    if not session_key:
        return True
    generations = generations or {}
    return int(generations.get(session_key, 0)) == int(generation)


def clear_session_boundary_security_state(
    session_key: str,
    *,
    pending_skills_reload_notes: MutableMapping[str, Any] | None = None,
    pending_approvals: MutableMapping[str, Any] | None = None,
    update_prompt_pending: MutableMapping[str, Any] | None = None,
    slash_confirm_clear: Any | None = None,
    approval_clear_session: Any | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    """Clear per-session control state that must not survive a session boundary."""

    if not session_key:
        return False
    for store in (
        pending_skills_reload_notes,
        pending_approvals,
        update_prompt_pending,
    ):
        if isinstance(store, dict):
            store.pop(session_key, None)

    if slash_confirm_clear is None:
        try:
            from tools import slash_confirm as slash_confirm_mod

            slash_confirm_clear = slash_confirm_mod.clear
        except Exception:
            slash_confirm_clear = None
    if slash_confirm_clear is not None:
        try:
            slash_confirm_clear(session_key)
        except Exception as exc:
            if logger is not None:
                logger.debug(
                    "Failed to clear slash-confirm state for session boundary %s: %s",
                    session_key,
                    exc,
                )

    if approval_clear_session is None:
        try:
            from tools.approval import clear_session as approval_clear_session
        except Exception:
            approval_clear_session = None
    if approval_clear_session is not None:
        try:
            approval_clear_session(session_key)
        except Exception as exc:
            if logger is not None:
                logger.debug(
                    "Failed to clear approval state for session boundary %s: %s",
                    session_key,
                    exc,
                )
    return True


def evict_cached_agent(
    agent_cache: MutableMapping[str, Any] | None,
    agent_cache_lock: Any | None,
    session_key: str,
) -> bool:
    """Remove a cached agent entry for a session when cache locking is available."""

    if not agent_cache_lock or not session_key or agent_cache is None:
        return False
    with agent_cache_lock:
        agent_cache.pop(session_key, None)
    return True


async def interrupt_and_clear_session(
    session_key: str,
    source: SessionSource,
    *,
    running_agents: MutableMapping[str, Any],
    pending_sentinel: object,
    adapters: MutableMapping[Any, Any],
    pending_messages: MutableMapping[str, Any],
    invalidate_session: Any,
    release_running_state: Any,
    interrupt_reason: str,
    invalidation_reason: str,
    release_running_state_enabled: bool = True,
) -> bool:
    """Interrupt a running session and clear queued per-session state."""

    if not session_key:
        return False
    running_agent = running_agents.get(session_key)
    if running_agent and running_agent is not pending_sentinel:
        running_agent.interrupt(interrupt_reason)
    invalidate_session(session_key, reason=invalidation_reason)
    adapter = adapters.get(source.platform)
    if adapter and hasattr(adapter, "interrupt_session_activity"):
        await adapter.interrupt_session_activity(session_key, source.chat_id)
    if adapter and hasattr(adapter, "get_pending_message"):
        adapter.get_pending_message(session_key)
    pending_messages.pop(session_key, None)
    if release_running_state_enabled:
        release_running_state(session_key)
    return True
