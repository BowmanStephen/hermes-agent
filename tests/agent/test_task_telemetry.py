"""Task-level cost and outcome telemetry contracts."""

from types import SimpleNamespace

import pytest

from agent.task_telemetry import (
    build_task_context_breakdown,
    start_task_telemetry,
    with_task_telemetry,
)
from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    session_db.create_session("session-1", source="test")
    yield session_db
    session_db.close()


def test_task_telemetry_accumulates_live_usage_and_terminal_outcome(db):
    db.record_task_telemetry(
        task_id="job-7",
        turn_id="turn-1",
        session_id="session-1",
        model="model-a",
        provider="provider-a",
        started_at=100.0,
        breakdown={"system_prompt": 12, "user_message": 3},
        budget={"max_cost_usd": 0.05},
    )

    db.queue_token_counts(
        "session-1",
        model="model-a",
        billing_provider="provider-a",
        input_tokens=10,
        output_tokens=4,
        estimated_cost_usd=0.03,
        api_call_count=1,
        api_latency_ms=125.0,
        task_id="job-7",
        turn_id="turn-1",
    )
    db.queue_token_counts(
        "session-1",
        model="model-a",
        billing_provider="provider-a",
        input_tokens=5,
        output_tokens=2,
        estimated_cost_usd=0.03,
        api_call_count=1,
        api_latency_ms=50.0,
        task_id="job-7",
        turn_id="turn-1",
    )
    assert db.flush_token_counts()

    live = db.get_task_telemetry("turn-1")
    assert live["task_id"] == "job-7"
    assert live["model"] == "model-a"
    assert live["input_tokens"] == 15
    assert live["output_tokens"] == 6
    assert live["api_call_count"] == 2
    assert live["estimated_cost_usd"] == pytest.approx(0.06)
    assert live["api_latency_ms"] == pytest.approx(175.0)
    assert live["budget_alert"] == "cost_limit_exceeded"
    assert live["breakdown"] == {"system_prompt": 12, "user_message": 3}

    db.record_task_telemetry(
        task_id="job-7",
        turn_id="turn-1",
        session_id="session-1",
        ended_at=200.0,
        latency_ms=1000.0,
        outcome="completed",
        quality_score=0.9,
    )

    completed = db.get_task_telemetry("turn-1")
    assert completed["ended_at"] == 200.0
    assert completed["latency_ms"] == pytest.approx(1000.0)
    assert completed["outcome"] == "completed"
    assert completed["quality_score"] == pytest.approx(0.9)
    assert db.list_task_telemetry(task_id="job-7")[0]["turn_id"] == "turn-1"


def test_task_context_breakdown_keeps_the_named_buckets_separate():
    class MemoryStore:
        def format_for_system_prompt(self, target):
            return {"memory": "remember this", "user": "Stephen"}.get(target, "")

    agent = SimpleNamespace(
        _cached_system_prompt="system instructions",
        _context_files_prompt="project rules",
        tools=[{"function": {"name": "read_file", "description": "Read files"}}],
        _memory_store=MemoryStore(),
        _memory_enabled=True,
        _user_profile_enabled=True,
    )
    messages = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Current task"},
    ]

    breakdown = build_task_context_breakdown(
        agent,
        messages,
        current_user_message="Current task",
        current_user_index=2,
        ext_prefetch_cache="retrieved memory",
        plugin_user_context="hook context",
    )

    for bucket in (
        "system_prompt",
        "tools",
        "context_files",
        "memory",
        "user_message",
        "conversation",
        "injected_context",
    ):
        assert isinstance(breakdown[bucket], int)
        assert breakdown[bucket] >= 0
    assert breakdown["system_prompt"] > 0
    assert breakdown["tools"] > 0
    assert breakdown["context_files"] > 0
    assert breakdown["memory"] > 0
    assert breakdown["user_message"] > 0
    assert breakdown["conversation"] > 0
    assert breakdown["injected_context"] > 0
    assert breakdown["estimated_input_tokens"] == sum(
        breakdown[bucket]
        for bucket in (
            "system_prompt",
            "tools",
            "context_files",
            "memory",
            "user_message",
            "conversation",
            "injected_context",
        )
    )


def test_lifecycle_wrapper_closes_early_return_as_completed(db):
    agent = SimpleNamespace(
        _session_db=db,
        _current_task_id="job-early",
        _current_turn_id="turn-early",
        _task_telemetry_started_at=100.0,
        session_id="session-1",
        model="model-a",
        provider="provider-a",
    )
    start_task_telemetry(
        agent,
        task_id="job-early",
        turn_id="turn-early",
        started_at=100.0,
    )

    @with_task_telemetry
    def early_return(_agent):
        return {"completed": True, "final_response": "done"}

    result = early_return(agent)
    assert result["completed"] is True
    row = db.get_task_telemetry("turn-early")
    assert row["outcome"] == "completed"
    assert row["ended_at"] >= 100.0
    assert row["latency_ms"] >= 0
