"""Tests for extracted gateway session-state helpers."""

from collections import OrderedDict
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource, build_session_key
from gateway.session_manager import (
    begin_session_run_generation,
    cache_session_source,
    clear_session_boundary_security_state,
    evict_cached_agent,
    get_cached_session_source,
    invalidate_session_run_generation,
    interrupt_and_clear_session,
    is_session_run_current,
    resolve_session_key_for_source,
)


def _source(
    *,
    chat_id: str = "chat",
    user_id: str = "user",
    thread_id: str | None = None,
    chat_type: str = "dm",
) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        thread_id=thread_id,
    )


def test_resolve_session_key_prefers_session_store_key():
    source = _source()
    session_store = SimpleNamespace(_generate_session_key=lambda src: f"store:{src.chat_id}")

    assert (
        resolve_session_key_for_source(source, session_store=session_store, config=None)
        == "store:chat"
    )


def test_resolve_session_key_falls_back_to_configured_build_session_key():
    source = _source(chat_id="group", user_id="alice", thread_id="thread", chat_type="group")

    class BrokenSessionStore:
        def _generate_session_key(self, src):
            raise RuntimeError("store unavailable")

    config = SimpleNamespace(group_sessions_per_user=False, thread_sessions_per_user=True)

    assert resolve_session_key_for_source(
        source,
        session_store=BrokenSessionStore(),
        config=config,
    ) == build_session_key(
        source,
        group_sessions_per_user=False,
        thread_sessions_per_user=True,
    )


def test_session_source_cache_copies_sources_and_trims_lru():
    cache = OrderedDict()
    first = _source(chat_id="chat-1", user_id="user-1")
    second = _source(chat_id="chat-2", user_id="user-2")

    cache = cache_session_source(cache, "first", first, max_size=1)
    cache = cache_session_source(cache, "second", second, max_size=1)
    first.user_name = "mutated"

    assert list(cache) == ["second"]
    assert get_cached_session_source(cache, "first") is None
    cached_second = get_cached_session_source(cache, "second")
    assert cached_second == second
    assert cached_second is not second


def test_get_cached_session_source_marks_hit_recent():
    cache = OrderedDict([
        ("first", _source(chat_id="chat-1")),
        ("second", _source(chat_id="chat-2")),
    ])

    assert get_cached_session_source(cache, "first") == _source(chat_id="chat-1")
    assert list(cache) == ["second", "first"]


def test_session_run_generation_bumps_per_session():
    generations = {}

    first = begin_session_run_generation(generations, "session-a")
    second = begin_session_run_generation(generations, "session-a")
    other = begin_session_run_generation(generations, "session-b")

    assert first == 1
    assert second == 2
    assert other == 1
    assert generations == {"session-a": 2, "session-b": 1}


def test_invalidate_session_run_generation_marks_old_token_stale():
    generations = {}

    first = begin_session_run_generation(generations, "session-a")
    invalidated = invalidate_session_run_generation(generations, "session-a")

    assert invalidated == 2
    assert is_session_run_current(generations, "session-a", first) is False
    assert is_session_run_current(generations, "session-a", invalidated) is True


def test_session_run_generation_empty_key_is_current_noop():
    generations = {}

    assert begin_session_run_generation(generations, "") == 0
    assert invalidate_session_run_generation(generations, "") == 0
    assert is_session_run_current(generations, "", 123) is True
    assert generations == {}


def test_evict_cached_agent_removes_only_target_under_lock():
    import threading

    cache = {"session-a": ("agent-a", "sig-a"), "session-b": ("agent-b", "sig-b")}

    assert evict_cached_agent(cache, threading.Lock(), "session-a") is True

    assert cache == {"session-b": ("agent-b", "sig-b")}


def test_evict_cached_agent_without_lock_is_noop():
    cache = {"session-a": ("agent-a", "sig-a")}

    assert evict_cached_agent(cache, None, "session-a") is False
    assert "session-a" in cache


def test_clear_session_boundary_security_state_clears_targeted_state_only():
    pending_notes = {"session-a": "reload", "session-b": "other"}
    pending_approvals = {"session-a": {"cmd": "rm"}, "session-b": {"cmd": "echo"}}
    update_prompt_pending = {"session-a": True, "session-b": True}
    slash_cleared = []
    approval_cleared = []

    assert clear_session_boundary_security_state(
        "session-a",
        pending_skills_reload_notes=pending_notes,
        pending_approvals=pending_approvals,
        update_prompt_pending=update_prompt_pending,
        slash_confirm_clear=slash_cleared.append,
        approval_clear_session=approval_cleared.append,
    ) is True

    assert pending_notes == {"session-b": "other"}
    assert pending_approvals == {"session-b": {"cmd": "echo"}}
    assert update_prompt_pending == {"session-b": True}
    assert slash_cleared == ["session-a"]
    assert approval_cleared == ["session-a"]


def test_clear_session_boundary_security_state_empty_key_is_noop():
    pending_notes = {"": "keep"}

    assert clear_session_boundary_security_state(
        "",
        pending_skills_reload_notes=pending_notes,
    ) is False
    assert pending_notes == {"": "keep"}


@pytest.mark.asyncio
async def test_interrupt_and_clear_session_interrupts_and_clears_state():
    class Agent:
        def __init__(self):
            self.interrupts = []

        def interrupt(self, reason):
            self.interrupts.append(reason)

    class Adapter:
        def __init__(self):
            self.interrupts = []
            self.pending_consumed = []

        async def interrupt_session_activity(self, session_key, chat_id):
            self.interrupts.append((session_key, chat_id))

        def get_pending_message(self, session_key):
            self.pending_consumed.append(session_key)
            return "pending"

    agent = Agent()
    adapter = Adapter()
    pending_messages = {"session-a": "queued", "session-b": "other"}
    invalidations = []
    releases = []

    result = await interrupt_and_clear_session(
        "session-a",
        _source(),
        running_agents={"session-a": agent},
        pending_sentinel=object(),
        adapters={Platform.TELEGRAM: adapter},
        pending_messages=pending_messages,
        invalidate_session=lambda key, reason: invalidations.append((key, reason)),
        release_running_state=lambda key: releases.append(key),
        interrupt_reason="stop",
        invalidation_reason="session_reset",
    )

    assert result is True
    assert agent.interrupts == ["stop"]
    assert invalidations == [("session-a", "session_reset")]
    assert adapter.interrupts == [("session-a", "chat")]
    assert adapter.pending_consumed == ["session-a"]
    assert pending_messages == {"session-b": "other"}
    assert releases == ["session-a"]


@pytest.mark.asyncio
async def test_interrupt_and_clear_session_respects_pending_sentinel_and_release_flag():
    sentinel = object()
    invalidations = []
    releases = []
    pending_messages = {"session-a": "queued"}

    result = await interrupt_and_clear_session(
        "session-a",
        _source(),
        running_agents={"session-a": sentinel},
        pending_sentinel=sentinel,
        adapters={},
        pending_messages=pending_messages,
        invalidate_session=lambda key, reason: invalidations.append((key, reason)),
        release_running_state=lambda key: releases.append(key),
        interrupt_reason="stop",
        invalidation_reason="manual_stop",
        release_running_state_enabled=False,
    )

    assert result is True
    assert invalidations == [("session-a", "manual_stop")]
    assert pending_messages == {}
    assert releases == []


@pytest.mark.asyncio
async def test_interrupt_and_clear_session_empty_key_is_noop():
    invalidations = []

    result = await interrupt_and_clear_session(
        "",
        _source(),
        running_agents={},
        pending_sentinel=object(),
        adapters={},
        pending_messages={},
        invalidate_session=lambda key, reason: invalidations.append((key, reason)),
        release_running_state=lambda key: None,
        interrupt_reason="stop",
        invalidation_reason="manual_stop",
    )

    assert result is False
    assert invalidations == []
