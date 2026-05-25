"""Tests for extracted AgentRunner helper seams."""

from collections import OrderedDict
import threading

from gateway.agent_runner import (
    apply_session_model_override,
    compute_agent_config_signature,
    enforce_agent_cache_cap,
    extract_cache_busting_config,
    init_cached_agent_for_turn,
    is_intentional_model_switch,
    load_fallback_model,
    load_provider_routing,
    load_service_tier,
    release_evicted_agent_soft,
    release_running_agent_state,
    resolve_session_agent_runtime,
    resolve_turn_agent_config,
    snapshot_running_agents,
    sweep_idle_cached_agents,
)


def _runtime_kwargs():
    return {
        "api_key": "***",
        "base_url": "https://openrouter.ai/api/v1",
        "provider": "openrouter",
        "api_mode": "chat_completions",
        "command": None,
        "args": ["--flag"],
        "credential_pool": object(),
        "ignored": "not forwarded",
    }


def test_resolve_turn_agent_config_builds_stable_runtime_signature():
    route = resolve_turn_agent_config(
        model="gpt-5.3-codex",
        runtime_kwargs=_runtime_kwargs(),
        service_tier=None,
    )

    assert route["model"] == "gpt-5.3-codex"
    assert route["runtime"]["provider"] == "openrouter"
    assert route["runtime"]["args"] == ["--flag"]
    assert "ignored" not in route["runtime"]
    assert route["signature"] == (
        "gpt-5.3-codex",
        "openrouter",
        "https://openrouter.ai/api/v1",
        "chat_completions",
        None,
        ("--flag",),
    )
    assert route["request_overrides"] == {}


def test_resolve_turn_agent_config_adds_priority_overrides_when_supported():
    route = resolve_turn_agent_config(
        model="gpt-5.4",
        runtime_kwargs=_runtime_kwargs(),
        service_tier="priority",
    )

    assert route["request_overrides"] == {"service_tier": "priority"}


def test_load_service_tier_normalizes_priority_values(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  service_tier: fast\n", encoding="utf-8")

    assert load_service_tier(config_path) == "priority"

    config_path.write_text("agent:\n  service_tier: normal\n", encoding="utf-8")

    assert load_service_tier(config_path) is None


def test_load_provider_routing_reads_gateway_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "provider_routing:\n"
        "  only: [openrouter]\n"
        "  order: [anthropic, openai]\n",
        encoding="utf-8",
    )

    assert load_provider_routing(config_path) == {
        "only": ["openrouter"],
        "order": ["anthropic", "openai"],
    }


def test_load_fallback_model_prefers_fallback_providers(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "fallback_model:\n"
        "  provider: legacy\n"
        "fallback_providers:\n"
        "  - provider: openrouter\n"
        "    model: minimax/minimax-m2.7\n",
        encoding="utf-8",
    )

    assert load_fallback_model(config_path) == [
        {"provider": "openrouter", "model": "minimax/minimax-m2.7"}
    ]


def test_apply_session_model_override_preserves_none_values():
    runtime = {
        "provider": "anthropic",
        "api_key": "ant-key",
        "base_url": "https://api.anthropic.com",
        "api_mode": "anthropic_messages",
    }
    overrides = {
        "session-a": {
            "model": "gpt-5.4",
            "provider": "openai",
            "api_key": None,
            "base_url": None,
            "api_mode": "chat_completions",
        }
    }

    model, resolved_runtime = apply_session_model_override(
        overrides,
        "session-a",
        "anthropic/claude-sonnet-4",
        runtime,
    )

    assert model == "gpt-5.4"
    assert resolved_runtime["provider"] == "openai"
    assert resolved_runtime["api_key"] == "ant-key"
    assert resolved_runtime["base_url"] == "https://api.anthropic.com"
    assert resolved_runtime["api_mode"] == "chat_completions"


def test_apply_session_model_override_ignores_other_sessions():
    runtime = {"provider": "anthropic", "api_key": "ant-key"}

    model, resolved_runtime = apply_session_model_override(
        {"other": {"model": "gpt-5.4", "provider": "openai"}},
        "session-a",
        "anthropic/claude-sonnet-4",
        runtime,
    )

    assert model == "anthropic/claude-sonnet-4"
    assert resolved_runtime == runtime


def test_resolve_session_agent_runtime_prefers_complete_session_override():
    model, runtime = resolve_session_agent_runtime(
        session_key="session-a",
        session_model_overrides={
            "session-a": {
                "model": "gpt-5.4",
                "provider": "openai",
                "api_key": "override-key",
                "base_url": "https://api.openai.com/v1",
                "api_mode": "chat_completions",
            }
        },
        resolve_session_key_for_source=lambda _source: "unused",
        resolve_gateway_model=lambda _config=None: "anthropic/claude-sonnet-4",
        resolve_runtime_agent_kwargs=lambda: {
            "provider": "anthropic",
            "api_key": "global-key",
        },
        default_model_for_provider=lambda _provider: "unused",
    )

    assert model == "gpt-5.4"
    assert runtime == {
        "provider": "openai",
        "api_key": "override-key",
        "base_url": "https://api.openai.com/v1",
        "api_mode": "chat_completions",
    }


def test_resolve_session_agent_runtime_uses_provider_default_for_empty_model():
    model, runtime = resolve_session_agent_runtime(
        session_model_overrides={},
        resolve_session_key_for_source=lambda _source: "unused",
        resolve_gateway_model=lambda _config=None: "",
        resolve_runtime_agent_kwargs=lambda: {"provider": "openai"},
        default_model_for_provider=lambda provider: f"{provider}/default",
    )

    assert model == "openai/default"
    assert runtime == {"provider": "openai"}


def test_compute_agent_config_signature_uses_full_api_key_and_cache_keys():
    runtime_a = {
        "api_key": "eyJhbGci.token-for-account-a",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "provider": "openai-codex",
        "api_mode": "codex_responses",
    }
    runtime_b = dict(runtime_a)
    runtime_b["api_key"] = "eyJhbGci.token-for-account-b"

    assert runtime_a["api_key"][:8] == runtime_b["api_key"][:8]
    assert compute_agent_config_signature(
        "gpt-5.3-codex",
        runtime_a,
        ["hermes-telegram"],
        "",
    ) != compute_agent_config_signature(
        "gpt-5.3-codex",
        runtime_b,
        ["hermes-telegram"],
        "",
    )

    runtime = {"api_key": "k", "base_url": "u", "provider": "p"}
    assert compute_agent_config_signature(
        "m",
        runtime,
        [],
        "",
        cache_keys={"compression.threshold": 0.50},
    ) != compute_agent_config_signature(
        "m",
        runtime,
        [],
        "",
        cache_keys={"compression.threshold": 0.75},
    )


def test_extract_cache_busting_config_pulls_documented_keys(monkeypatch):
    from tools.registry import registry

    monkeypatch.setattr(registry, "_generation", 321)

    out = extract_cache_busting_config({
        "model": {"context_length": 272_000, "max_tokens": 4096},
        "compression": {"enabled": False, "threshold": 0.6},
    })

    assert out["model.context_length"] == 272_000
    assert out["model.max_tokens"] == 4096
    assert out["compression.enabled"] is False
    assert out["compression.threshold"] == 0.6
    assert out["compression.target_ratio"] is None
    assert out["tools.registry_generation"] == 321


def test_snapshot_running_agents_filters_pending_sentinel():
    pending = object()
    active_agent = object()

    assert snapshot_running_agents(
        {
            "active": active_agent,
            "pending": pending,
        },
        pending_sentinel=pending,
    ) == {"active": active_agent}


def test_init_cached_agent_for_fresh_turn_resets_idle_marker():
    class Agent:
        _last_activity_ts = 123.0
        _last_activity_desc = "previous activity"
        _api_call_count = 10

    agent = Agent()

    init_cached_agent_for_turn(agent, interrupt_depth=0, now=456.0)

    assert agent._last_activity_ts == 456.0
    assert agent._last_activity_desc == "starting new turn (cached)"
    assert agent._api_call_count == 0


def test_init_cached_agent_for_interrupt_turn_preserves_idle_marker():
    class Agent:
        _last_activity_ts = 123.0
        _last_activity_desc = "previous activity"
        _api_call_count = 10

    agent = Agent()

    init_cached_agent_for_turn(agent, interrupt_depth=1, now=456.0)

    assert agent._last_activity_ts == 123.0
    assert agent._last_activity_desc == "previous activity"
    assert agent._api_call_count == 0


def test_is_intentional_model_switch_matches_session_override():
    overrides = {
        "session-a": {
            "model": "gpt-5.4",
            "provider": "openai",
        }
    }

    assert is_intentional_model_switch(overrides, "session-a", "gpt-5.4") is True


def test_is_intentional_model_switch_rejects_missing_or_different_override():
    overrides = {
        "session-a": {
            "model": "gpt-5.4",
            "provider": "openai",
        }
    }

    assert is_intentional_model_switch(overrides, "session-a", "gpt-5.4-mini") is False
    assert is_intentional_model_switch(overrides, "session-b", "gpt-5.4") is False


def test_release_running_agent_state_clears_all_runtime_dicts():
    running_agents = {"session-a": object(), "session-b": object()}
    running_agents_ts = {"session-a": 1.0, "session-b": 2.0}
    busy_ack_ts = {"session-a": 3.0, "session-b": 4.0}

    assert release_running_agent_state(
        "session-a",
        running_agents=running_agents,
        running_agents_ts=running_agents_ts,
        busy_ack_ts=busy_ack_ts,
    ) is True

    assert set(running_agents) == {"session-b"}
    assert set(running_agents_ts) == {"session-b"}
    assert set(busy_ack_ts) == {"session-b"}


def test_release_running_agent_state_honors_generation_guard():
    running_agents = {"session-a": object()}
    running_agents_ts = {"session-a": 1.0}
    busy_ack_ts = {"session-a": 2.0}

    assert release_running_agent_state(
        "session-a",
        running_agents=running_agents,
        running_agents_ts=running_agents_ts,
        busy_ack_ts=busy_ack_ts,
        run_generation=7,
        is_session_run_current=lambda session_key, generation: False,
    ) is False

    assert "session-a" in running_agents
    assert "session-a" in running_agents_ts
    assert "session-a" in busy_ack_ts


def test_release_running_agent_state_noops_empty_session_key():
    running_agents = {"": object()}
    running_agents_ts = {"": 1.0}

    assert release_running_agent_state(
        "",
        running_agents=running_agents,
        running_agents_ts=running_agents_ts,
    ) is False

    assert "" in running_agents
    assert "" in running_agents_ts


def test_release_evicted_agent_soft_prefers_release_clients():
    class Agent:
        def __init__(self):
            self.release_calls = 0

        def release_clients(self):
            self.release_calls += 1

    cleanup_calls = []
    agent = Agent()

    release_evicted_agent_soft(agent, cleanup_agent_resources=cleanup_calls.append)

    assert agent.release_calls == 1
    assert cleanup_calls == []


def test_release_evicted_agent_soft_falls_back_to_cleanup():
    class Agent:
        pass

    cleanup_calls = []
    agent = Agent()

    release_evicted_agent_soft(agent, cleanup_agent_resources=cleanup_calls.append)

    assert cleanup_calls == [agent]


def test_enforce_agent_cache_cap_skips_active_lru_without_substitution():
    active = object()
    idle_a = object()
    idle_b = object()
    cache = OrderedDict(
        [
            ("active", (active, "sig")),
            ("idle-a", (idle_a, "sig")),
            ("idle-b", (idle_b, "sig")),
        ]
    )
    scheduled = []

    evicted = enforce_agent_cache_cap(
        cache,
        running_agents={"active": active},
        pending_sentinel=object(),
        max_size=2,
        schedule_release=lambda key, agent, reason: scheduled.append((key, agent, reason)),
    )

    assert evicted == []
    assert list(cache) == ["active", "idle-a", "idle-b"]
    assert scheduled == []


def test_enforce_agent_cache_cap_evicts_idle_entries_in_excess_window():
    active = object()
    idle_second = object()
    idle_third = object()
    idle_fourth = object()
    cache = OrderedDict(
        [
            ("s1", (active, "sig")),
            ("s2", (idle_second, "sig")),
            ("s3", (idle_third, "sig")),
            ("s4", (idle_fourth, "sig")),
        ]
    )
    scheduled = []

    evicted = enforce_agent_cache_cap(
        cache,
        running_agents={"s1": active},
        pending_sentinel=object(),
        max_size=2,
        schedule_release=lambda key, agent, reason: scheduled.append((key, agent, reason)),
    )

    assert evicted == ["s2"]
    assert list(cache) == ["s1", "s3", "s4"]
    assert scheduled == [("s2", idle_second, "evict")]


def test_sweep_idle_cached_agents_evicts_stale_but_not_active():
    stale = type("Agent", (), {"_last_activity_ts": 10.0})()
    active = type("Agent", (), {"_last_activity_ts": 10.0})()
    fresh = type("Agent", (), {"_last_activity_ts": 99.0})()
    cache = OrderedDict(
        [
            ("stale", (stale, "sig")),
            ("active", (active, "sig")),
            ("fresh", (fresh, "sig")),
        ]
    )
    scheduled = []

    evicted = sweep_idle_cached_agents(
        cache,
        cache_lock=threading.Lock(),
        running_agents={"active": active},
        pending_sentinel=object(),
        idle_ttl_secs=30.0,
        now=100.0,
        schedule_release=lambda key, agent, reason: scheduled.append((key, agent, reason)),
    )

    assert evicted == 1
    assert list(cache) == ["active", "fresh"]
    assert scheduled == [("stale", stale, "idle")]
