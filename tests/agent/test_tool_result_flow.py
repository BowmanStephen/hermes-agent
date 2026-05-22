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
