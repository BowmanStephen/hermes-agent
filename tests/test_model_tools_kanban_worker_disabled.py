"""Dispatcher-spawned kanban workers must keep their lifecycle tools even when
the assignee profile's ``agent.disabled_toolsets`` lists ``kanban``.

Observed failure: bot profiles carried ``disabled_toolsets: [kanban]`` (to
hide board tools from chat sessions). The disabled-subtraction step ran after
the worker's kanban append, stripping ``kanban_complete``/``kanban_block``
from every worker. Workers could then never close their task: the stop-guard
nudged them in a loop until the iteration budget exhausted (recorded as
``timed_out``/``crashed``) and tasks landed in ``blocked``.

Chat sessions are unaffected: without ``HERMES_KANBAN_TASK`` the disable
still strips kanban tools.
"""

import pytest

import model_tools
from tools import registry


@pytest.fixture(autouse=True)
def _clear_caches():
    """Isolate from other tests' memoized tool defs and check_fn verdicts.

    Both caches key on process-global state (env flags, registry generation);
    a stale entry from a test that ran without ``HERMES_KANBAN_TASK`` would
    otherwise mask the worker-append behavior under test.
    """
    model_tools._tool_defs_cache.clear()
    with registry._check_fn_cache_lock:
        registry._check_fn_cache.clear()
    yield
    model_tools._tool_defs_cache.clear()
    with registry._check_fn_cache_lock:
        registry._check_fn_cache.clear()


def _names(tools):
    return {t["function"]["name"] for t in tools}


PINNED = ["clarify", "file", "memory", "todo", "web"]


def test_worker_keeps_lifecycle_tools_despite_profile_disable(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_test123")
    names = _names(
        model_tools.get_tool_definitions(
            enabled_toolsets=list(PINNED),
            disabled_toolsets=["kanban", "x_search"],
            quiet_mode=True,
        )
    )
    # The full required worker lifecycle trio (task audit: kanban_complete,
    # kanban_block, kanban_show) must survive a profile-level kanban disable.
    assert "kanban_complete" in names
    assert "kanban_block" in names
    assert "kanban_show" in names
    assert "kanban_heartbeat" in names


def test_non_worker_still_loses_kanban_tools_when_disabled(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    names = _names(
        model_tools.get_tool_definitions(
            enabled_toolsets=list(PINNED) + ["kanban"],
            disabled_toolsets=["kanban"],
            quiet_mode=True,
        )
    )
    assert "kanban_complete" not in names
    assert "kanban_block" not in names
    assert "kanban_show" not in names


def test_worker_disable_of_other_toolsets_still_applies(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_test123")
    names = _names(
        model_tools.get_tool_definitions(
            enabled_toolsets=list(PINNED),
            disabled_toolsets=["kanban", "web"],
            quiet_mode=True,
        )
    )
    assert "kanban_complete" in names
    assert "web_search" not in names
