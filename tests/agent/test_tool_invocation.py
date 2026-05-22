"""Tests for the agent tool invocation module."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.tool_guardrails import ToolGuardrailDecision


def _agent(**overrides):
    agent = SimpleNamespace(
        session_id="session-1",
        valid_tool_names={"web_search"},
        _todo_store=object(),
        _memory_store=object(),
        _memory_manager=None,
        _context_engine_tool_names=set(),
        context_compressor=None,
        clarify_callback=None,
        _checkpoint_mgr=SimpleNamespace(enabled=False),
        _tool_guardrails=SimpleNamespace(
            before_call=lambda name, args: ToolGuardrailDecision()
        ),
        _guardrail_block_result=lambda decision: json.dumps({"error": decision.message}),
        _get_session_db_for_recall=lambda: None,
        _dispatch_delegate_task=lambda args: json.dumps({"delegated": args}),
        _build_memory_write_metadata=lambda **kw: kw,
    )
    for name, value in overrides.items():
        setattr(agent, name, value)
    return agent


def test_registry_tool_invocation_owns_pre_hook_and_skips_registry_when_blocked(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: "blocked by policy",
    )

    with patch("run_agent.handle_function_call", side_effect=AssertionError("should not dispatch")):
        result = invoke_tool(
            _agent(),
            ToolInvocation("web_search", {"q": "x"}, task_id="task-1", call_id="call-1"),
        )

    assert json.loads(result.content) == {"error": "blocked by policy"}
    assert result.blocked_by == "plugin"
    assert result.adapter_name == "blocked"
    assert result.duration_ms == 0


def test_guardrail_block_runs_after_pre_hook_and_before_adapter(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )
    decision = ToolGuardrailDecision(action="block", code="test", message="nope")
    guardrails = SimpleNamespace(before_call=MagicMock(return_value=decision))
    agent = _agent(_tool_guardrails=guardrails)

    with patch("run_agent.handle_function_call", side_effect=AssertionError("should not dispatch")):
        result = invoke_tool(
            agent,
            ToolInvocation("web_search", {"q": "x"}, task_id="task-1", call_id="call-1"),
        )

    guardrails.before_call.assert_called_once_with("web_search", {"q": "x"})
    assert json.loads(result.content) == {"error": "nope"}
    assert result.blocked_by == "guardrail"
    assert result.adapter_name == "blocked"


def test_regular_registry_tool_dispatch_uses_existing_registry_adapter(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )

    with patch("run_agent.handle_function_call", return_value='{"ok": true}') as dispatch:
        result = invoke_tool(
            _agent(
                valid_tool_names={"web_search", "read_file"},
                tools=[
                    {"type": "function", "function": {"name": "web_search"}},
                    {"type": "function", "function": {"name": "read_file"}},
                ],
            ),
            ToolInvocation("web_search", {"q": "x"}, task_id="task-1", call_id="call-1"),
        )

    dispatch.assert_called_once()
    args, kwargs = dispatch.call_args
    assert args == ("web_search", {"q": "x"}, "task-1")
    assert kwargs["tool_call_id"] == "call-1"
    assert kwargs["session_id"] == "session-1"
    assert kwargs["enabled_tools"] == ["web_search", "read_file"]
    assert kwargs["skip_pre_tool_call_hook"] is True
    assert result.content == '{"ok": true}'
    assert result.adapter_name == "registry"
    assert result.blocked_by is None
    assert result.duration_ms >= 0


def test_agent_level_memory_tool_runs_post_and_transform_hooks(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )
    observed = []

    def fake_invoke_hook(hook_name, **kwargs):
        observed.append((hook_name, kwargs["result"]))
        if hook_name == "transform_tool_result":
            return ['{"transformed": true}']
        return []

    with (
        patch("tools.memory_tool.memory_tool", return_value='{"stored": true}'),
        patch("hermes_cli.plugins.invoke_hook", side_effect=fake_invoke_hook),
    ):
        result = invoke_tool(
            _agent(),
            ToolInvocation(
                "memory",
                {"action": "add", "target": "memory", "content": "x"},
                task_id="task-1",
                call_id="call-1",
            ),
        )

    assert result.content == '{"transformed": true}'
    assert result.adapter_name == "memory"
    assert observed == [
        ("post_tool_call", '{"stored": true}'),
        ("transform_tool_result", '{"stored": true}'),
    ]


def test_checkpoint_happens_inside_invocation_before_file_mutation(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )
    events = []
    checkpoint_mgr = SimpleNamespace(
        enabled=True,
        get_working_dir_for_path=lambda path: "/repo",
        ensure_checkpoint=lambda work_dir, reason: events.append(("checkpoint", work_dir, reason)),
    )

    def fake_dispatch(*args, **kwargs):
        events.append(("dispatch", args[0]))
        return '{"ok": true}'

    with patch("run_agent.handle_function_call", side_effect=fake_dispatch):
        invoke_tool(
            _agent(_checkpoint_mgr=checkpoint_mgr, valid_tool_names={"write_file"}),
            ToolInvocation(
                "write_file",
                {"path": "x.py", "content": "print(1)"},
                task_id="task-1",
                call_id="call-1",
            ),
        )

    assert events == [
        ("checkpoint", "/repo", "before write_file"),
        ("dispatch", "write_file"),
    ]


def test_invocation_result_builds_tool_result_context():
    from agent.tool_invocation import ToolInvocation, ToolInvocationResult

    invocation = ToolInvocation(
        "web_search",
        {"query": "x"},
        task_id="task-1",
        call_id="call-1",
    )
    messages = []
    result = ToolInvocationResult(
        content='{"ok": true}',
        duration_ms=1234,
        adapter_name="registry",
    )

    ctx = result.to_result_context(invocation, messages, is_error=False)

    assert ctx.name == "web_search"
    assert ctx.args == {"query": "x"}
    assert ctx.content == '{"ok": true}'
    assert ctx.duration == 1.234
    assert ctx.call_id == "call-1"
    assert ctx.task_id == "task-1"
    assert ctx.messages is messages
    assert ctx.blocked is False
    assert ctx.is_error is False


def test_blocked_invocation_result_marks_result_context_blocked():
    from agent.tool_invocation import ToolInvocation, ToolInvocationResult

    invocation = ToolInvocation(
        "write_file",
        {"path": "x.py"},
        task_id="task-1",
        call_id="call-1",
    )
    result = ToolInvocationResult(
        content='{"error": "blocked"}',
        duration_ms=0,
        adapter_name="blocked",
        blocked_by="guardrail",
    )

    ctx = result.to_result_context(invocation, [], is_error=True)

    assert ctx.blocked is True
    assert ctx.is_error is True
    assert ctx.duration == 0.0


def test_known_error_invocation_results_mark_context_error_by_default():
    from agent.tool_invocation import ToolInvocation, ToolInvocationPreparation, ToolInvocationResult

    invocation = ToolInvocation(
        "web_search",
        {"query": "x"},
        task_id="task-1",
        call_id="call-1",
    )
    blocked = ToolInvocationPreparation(
        blocked_content='{"error": "blocked"}',
        blocked_by="plugin",
    ).to_blocked_result()

    assert blocked.to_result_context(invocation, []).is_error is True
    assert ToolInvocationResult.error("boom").to_result_context(invocation, []).is_error is True
    assert ToolInvocationResult.cancelled("stopped").to_result_context(invocation, []).is_error is True


def test_invocation_result_exposes_display_hint_metadata():
    from agent.tool_invocation import ToolInvocationResult

    result = ToolInvocationResult(
        content='{"error": "blocked"}',
        duration_ms=42,
        adapter_name="blocked",
        blocked_by="guardrail",
    )

    hint = result.display_hint

    assert hint.adapter_name == "blocked"
    assert hint.blocked_by == "guardrail"
    assert hint.duration_ms == 42
    assert hint.blocked is True


def test_runtime_helper_can_return_full_invocation_result(monkeypatch):
    from agent.agent_runtime_helpers import invoke_tool_result
    from agent.tool_invocation import ToolInvocationResult

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )

    with patch("run_agent.handle_function_call", return_value='{"ok": true}'):
        result = invoke_tool_result(
            _agent(
                valid_tool_names={"web_search"},
                tools=[{"type": "function", "function": {"name": "web_search"}}],
            ),
            "web_search",
            {"query": "x"},
            "task-1",
            "call-1",
            [],
        )

    assert isinstance(result, ToolInvocationResult)
    assert result.content == '{"ok": true}'
    assert result.adapter_name == "registry"
    assert result.blocked_by is None


def test_preparation_builds_blocked_invocation_result():
    from agent.tool_invocation import ToolInvocationPreparation, ToolInvocationResult

    preparation = ToolInvocationPreparation(
        blocked_content='{"error": "blocked"}',
        blocked_by="plugin",
    )

    result = preparation.to_blocked_result()

    assert isinstance(result, ToolInvocationResult)
    assert result.content == '{"error": "blocked"}'
    assert result.duration_ms == 0
    assert result.adapter_name == "blocked"
    assert result.blocked_by == "plugin"


def test_invocation_result_builds_error_and_cancelled_results():
    from agent.tool_invocation import ToolInvocationResult

    error = ToolInvocationResult.error("boom", duration_ms=12)
    cancelled = ToolInvocationResult.cancelled("stopped")

    assert error.content == "boom"
    assert error.duration_ms == 12
    assert error.adapter_name == "error"
    assert error.blocked_by is None
    assert cancelled.content == "stopped"
    assert cancelled.duration_ms == 0
    assert cancelled.adapter_name == "cancelled"


def test_allowed_preparation_has_no_blocked_result():
    from agent.tool_invocation import ToolInvocationPreparation

    assert ToolInvocationPreparation().to_blocked_result() is None


def test_prepared_invocation_exposes_execution_state():
    from agent.tool_invocation import (
        PreparedToolInvocation,
        ToolInvocation,
        ToolInvocationPreparation,
    )

    invocation = ToolInvocation(
        "web_search",
        {"query": "x"},
        task_id="task-1",
        call_id="call-1",
    )
    preparation = ToolInvocationPreparation(
        blocked_content='{"error": "blocked"}',
        blocked_by="guardrail",
    )

    prepared = PreparedToolInvocation(invocation, preparation)

    assert prepared.name == "web_search"
    assert prepared.args == {"query": "x"}
    assert prepared.call_id == "call-1"
    assert prepared.allows_execution is False
    assert prepared.blocked_result.blocked_by == "guardrail"


def test_prepared_invocation_builds_result_context():
    from agent.tool_invocation import (
        PreparedToolInvocation,
        ToolInvocation,
        ToolInvocationPreparation,
        ToolInvocationResult,
    )

    messages = []
    prepared = PreparedToolInvocation(
        ToolInvocation("web_search", {"query": "x"}, task_id="task-1", call_id="call-1"),
        ToolInvocationPreparation(),
    )
    result = ToolInvocationResult(
        content='{"ok": true}',
        duration_ms=250,
        adapter_name="registry",
        is_error=False,
    )

    ctx = prepared.to_result_context(result, messages)

    assert ctx.name == "web_search"
    assert ctx.args == {"query": "x"}
    assert ctx.content == '{"ok": true}'
    assert ctx.duration == 0.25
    assert ctx.call_id == "call-1"
    assert ctx.task_id == "task-1"
    assert ctx.messages is messages
    assert ctx.is_error is False


def test_prepared_invocation_builds_cancelled_and_missing_results():
    from agent.tool_invocation import (
        PreparedToolInvocation,
        ToolInvocation,
        ToolInvocationPreparation,
    )

    prepared = PreparedToolInvocation(
        ToolInvocation("web_search", {"query": "x"}, task_id="task-1", call_id="call-1"),
        ToolInvocationPreparation(),
    )

    cancelled = prepared.cancelled_result()
    missing = prepared.missing_result()

    assert cancelled.content == "[Tool execution cancelled — web_search was skipped due to user interrupt]"
    assert cancelled.adapter_name == "cancelled"
    assert cancelled.is_error is True
    assert missing.content == "Error executing tool 'web_search': thread did not return a result"
    assert missing.adapter_name == "error"
    assert missing.is_error is True


def test_invoke_prepared_tool_uses_existing_preparation(monkeypatch):
    from agent.tool_invocation import (
        PreparedToolInvocation,
        ToolInvocation,
        ToolInvocationPreparation,
        invoke_prepared_tool,
    )

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pre-hook reran")),
    )
    agent = _agent(
        _tool_guardrails=SimpleNamespace(
            before_call=lambda name, args: (_ for _ in ()).throw(AssertionError("guardrail reran"))
        )
    )
    prepared = PreparedToolInvocation(
        ToolInvocation("web_search", {"query": "x"}, task_id="task-1", call_id="call-1"),
        ToolInvocationPreparation(),
    )

    with patch("run_agent.handle_function_call", return_value='{"ok": true}') as dispatch:
        result = invoke_prepared_tool(agent, prepared)

    dispatch.assert_called_once()
    assert result.content == '{"ok": true}'
    assert result.adapter_name == "registry"


def test_prepare_tool_returns_prepared_invocation(monkeypatch):
    from agent.tool_invocation import PreparedToolInvocation, ToolInvocation, prepare_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: "blocked",
    )
    invocation = ToolInvocation("web_search", {}, task_id="task-1", call_id="call-1")

    prepared = prepare_tool(_agent(), invocation)

    assert isinstance(prepared, PreparedToolInvocation)
    assert prepared.invocation is invocation
    assert prepared.allows_execution is False
    assert prepared.blocked_result.blocked_by == "plugin"


def test_prepare_tool_call_parses_model_tool_call(monkeypatch):
    from agent.tool_invocation import PreparedToolInvocation, prepare_tool_call

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="web_search",
            arguments='{"query": "x"}',
        ),
    )
    messages = []

    prepared = prepare_tool_call(_agent(), tool_call, "task-1", messages)

    assert isinstance(prepared, PreparedToolInvocation)
    assert prepared.name == "web_search"
    assert prepared.args == {"query": "x"}
    assert prepared.call_id == "call-1"
    assert prepared.invocation.messages is messages
    assert prepared.allows_execution is True


def test_prepare_tool_call_labels_adapter_without_executing(monkeypatch):
    from agent.tool_invocation import prepare_tool_call

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )
    memory_manager = SimpleNamespace(
        has_tool=lambda name: name == "memory_provider_lookup",
    )

    context_tool = SimpleNamespace(
        id="call-context",
        function=SimpleNamespace(name="ctx_lookup", arguments="{}"),
    )
    memory_provider_tool = SimpleNamespace(
        id="call-memory-provider",
        function=SimpleNamespace(name="memory_provider_lookup", arguments="{}"),
    )
    registry_tool = SimpleNamespace(
        id="call-registry",
        function=SimpleNamespace(name="web_search", arguments="{}"),
    )

    agent = _agent(
        _context_engine_tool_names={"ctx_lookup"},
        _memory_manager=memory_manager,
    )

    assert prepare_tool_call(agent, context_tool, "task-1").adapter_name == "context_engine"
    assert prepare_tool_call(agent, memory_provider_tool, "task-1").adapter_name == "memory_provider"
    assert prepare_tool_call(agent, registry_tool, "task-1").adapter_name == "registry"


def test_prepare_tool_call_falls_back_to_empty_args(monkeypatch):
    from agent.tool_invocation import prepare_tool_call

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )
    malformed = SimpleNamespace(
        id="call-bad-json",
        function=SimpleNamespace(name="web_search", arguments="{bad"),
    )
    non_dict = SimpleNamespace(
        id="call-list",
        function=SimpleNamespace(name="web_search", arguments='["x"]'),
    )

    assert prepare_tool_call(_agent(), malformed, "task-1").args == {}
    assert prepare_tool_call(_agent(), non_dict, "task-1").args == {}


def test_invocation_resets_nudge_counters_only_for_executed_tools(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )

    agent = _agent(_turns_since_memory=4, _iters_since_skill=7)

    with patch("tools.memory_tool.memory_tool", return_value='{"ok": true}'):
        invoke_tool(
            agent,
            ToolInvocation(
                "memory",
                {"action": "add", "target": "memory", "content": "x"},
                task_id="task-1",
                call_id="call-memory",
            ),
        )

    assert agent._turns_since_memory == 0
    assert agent._iters_since_skill == 7

    with patch("run_agent.handle_function_call", return_value='{"ok": true}'):
        invoke_tool(
            agent,
            ToolInvocation(
                "skill_manage",
                {"action": "list"},
                task_id="task-1",
                call_id="call-skill",
            ),
        )

    assert agent._iters_since_skill == 0


def test_invocation_returns_structured_error_when_dispatch_raises(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )

    with patch("run_agent.handle_function_call", side_effect=RuntimeError("boom")):
        result = invoke_tool(
            _agent(valid_tool_names={"web_search"}),
            ToolInvocation(
                "web_search",
                {"query": "x"},
                task_id="task-1",
                call_id="call-1",
            ),
        )

    assert result.content == "Error executing tool 'web_search': boom"
    assert result.adapter_name == "error"
    assert result.duration_ms >= 0


def test_invocation_marks_detected_tool_failures(monkeypatch):
    from agent.tool_invocation import ToolInvocation, invoke_tool

    monkeypatch.setattr(
        "hermes_cli.plugins.get_pre_tool_call_block_message",
        lambda *args, **kwargs: None,
    )

    with patch("run_agent.handle_function_call", return_value='{"error": "not found"}'):
        result = invoke_tool(
            _agent(valid_tool_names={"web_search"}),
            ToolInvocation(
                "web_search",
                {"query": "x"},
                task_id="task-1",
                call_id="call-1",
            ),
        )

    assert result.is_error is True
