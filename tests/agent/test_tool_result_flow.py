"""Tests for shared post-tool result finalization."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _agent():
    agent = SimpleNamespace()
    agent._append_guardrail_observation = lambda name, args, result, failed=False: result + "|guarded"
    agent._record_file_mutation_result = MagicMock()
    agent.tool_progress_callback = MagicMock()
    agent.tool_complete_callback = MagicMock()
    agent._current_tool = "write_file"
    agent._touch_activity = MagicMock()
    agent.verbose_logging = False
    agent._subdirectory_hints = SimpleNamespace(check_tool_call=lambda name, args: "|hint")
    agent._tool_result_content_for_active_model = lambda name, result: f"content:{result}"
    agent._apply_pending_steer_to_tool_results = MagicMock()
    return agent


def test_finalize_tool_result_records_callbacks_and_appends_message():
    from agent.tool_result_flow import ToolResultContext, finalize_tool_result

    agent = _agent()
    messages = []

    with patch("agent.tool_result_flow.maybe_persist_tool_result", lambda **kw: kw["content"] + "|persisted"):
        result = finalize_tool_result(
            agent,
            ToolResultContext(
                name="write_file",
                args={"path": "x.py"},
                content='{"ok": true}',
                duration=1.25,
                call_id="call-1",
                task_id="task-1",
                messages=messages,
                blocked=False,
                is_error=False,
            ),
        )

    assert result.content == '{"ok": true}|guarded|persisted|hint'
    agent._record_file_mutation_result.assert_called_once_with(
        "write_file", {"path": "x.py"}, '{"ok": true}|guarded', False
    )
    agent.tool_progress_callback.assert_called_once_with(
        "tool.completed", "write_file", None, None, duration=1.25, is_error=False
    )
    agent.tool_complete_callback.assert_called_once_with(
        "call-1", "write_file", {"path": "x.py"}, '{"ok": true}|guarded'
    )
    agent._touch_activity.assert_called_once_with("tool completed: write_file (1.2s)")
    assert agent._current_tool is None
    assert messages == [
        {
            "role": "tool",
            "name": "write_file",
            "tool_name": "write_file",
            "content": 'content:{"ok": true}|guarded|persisted|hint',
            "tool_call_id": "call-1",
        }
    ]
    agent._apply_pending_steer_to_tool_results.assert_not_called()


def test_finalize_tool_result_skips_execution_callbacks_when_blocked():
    from agent.tool_result_flow import ToolResultContext, finalize_tool_result

    agent = _agent()
    messages = []

    result = finalize_tool_result(
        agent,
        ToolResultContext(
            name="write_file",
            args={"path": "x.py"},
            content='{"error": "blocked"}',
            duration=0.0,
            call_id="call-1",
            task_id="task-1",
            messages=messages,
            blocked=True,
            is_error=True,
        ),
    )

    assert result.content == '{"error": "blocked"}|hint'
    agent._record_file_mutation_result.assert_not_called()
    agent.tool_progress_callback.assert_not_called()
    agent.tool_complete_callback.assert_not_called()
    assert messages[0]["content"] == 'content:{"error": "blocked"}|hint'


def test_finalize_tool_results_enforces_budget_before_steer():
    from agent.tool_result_flow import ToolResultContext, finalize_tool_results

    agent = _agent()
    messages = []
    order = []
    agent._apply_pending_steer_to_tool_results = lambda *_args, **_kwargs: order.append("steer")

    with (
        patch("agent.tool_result_flow.maybe_persist_tool_result", lambda **kw: kw["content"]),
        patch("agent.tool_result_flow.enforce_turn_budget", lambda *_args, **_kwargs: order.append("budget")),
    ):
        finalized = finalize_tool_results(
            agent,
            [
                ToolResultContext(
                    name="web_search",
                    args={"query": "x"},
                    content='{"ok": true}',
                    duration=0.5,
                    call_id="call-1",
                    task_id="task-1",
                    messages=messages,
                    blocked=False,
                    is_error=False,
                )
            ],
        )

    assert len(finalized) == 1
    assert order == ["budget", "steer"]
    assert len(messages) == 1


def test_finalize_tool_result_batch_enforces_budget_before_steer():
    from agent.tool_result_flow import finalize_tool_result_batch

    agent = _agent()
    messages = [{"role": "tool", "content": "one"}, {"role": "tool", "content": "two"}]
    order = []
    agent._apply_pending_steer_to_tool_results = lambda *_args, **_kwargs: order.append("steer")

    with patch("agent.tool_result_flow.enforce_turn_budget", lambda *_args, **_kwargs: order.append("budget")):
        finalize_tool_result_batch(agent, messages, 2, "task-1")

    assert order == ["budget", "steer"]


def test_skip_message_helpers_build_tool_messages():
    from agent.tool_result_flow import (
        make_cancelled_tool_result_message,
        make_not_started_tool_result_message,
    )

    cancelled = make_cancelled_tool_result_message("web_search", "call-1")
    not_started = make_not_started_tool_result_message("read_file", "call-2")

    assert cancelled == {
        "role": "tool",
        "name": "web_search",
        "tool_name": "web_search",
        "content": "[Tool execution cancelled — web_search was skipped due to user interrupt]",
        "tool_call_id": "call-1",
    }
    assert not_started["name"] == "read_file"
    assert not_started["tool_call_id"] == "call-2"
    assert not_started["content"] == (
        "[Tool execution skipped — read_file was not started. User sent a new message]"
    )
