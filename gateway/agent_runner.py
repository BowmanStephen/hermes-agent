"""AgentRunner helper seams for GatewayRunner."""

from __future__ import annotations

import logging
import hashlib
import json
from pathlib import Path
from typing import Any

from hermes_cli.config import cfg_get
from gateway.agent_runtime import (
    apply_session_model_override,
    resolve_session_agent_runtime,
)


CACHE_BUSTING_CONFIG_KEYS: tuple = (
    ("model", "context_length"),
    ("model", "max_tokens"),
    ("compression", "enabled"),
    ("compression", "threshold"),
    ("compression", "target_ratio"),
    ("compression", "protect_last_n"),
    ("agent", "disabled_toolsets"),
)


def _read_yaml_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml

        if config_path.exists():
            with open(config_path, encoding="utf-8") as config_file:
                return yaml.safe_load(config_file) or {}
    except Exception:
        pass
    return {}


def resolve_turn_agent_config(
    *,
    model: str,
    runtime_kwargs: dict,
    service_tier: str | None = None,
) -> dict:
    """Build the effective model/runtime config for a single gateway turn."""

    from hermes_cli.models import resolve_fast_mode_overrides

    runtime = {
        "api_key": runtime_kwargs.get("api_key"),
        "base_url": runtime_kwargs.get("base_url"),
        "provider": runtime_kwargs.get("provider"),
        "api_mode": runtime_kwargs.get("api_mode"),
        "command": runtime_kwargs.get("command"),
        "args": list(runtime_kwargs.get("args") or []),
        "credential_pool": runtime_kwargs.get("credential_pool"),
    }
    route = {
        "model": model,
        "runtime": runtime,
        "signature": (
            model,
            runtime["provider"],
            runtime["base_url"],
            runtime["api_mode"],
            runtime["command"],
            tuple(runtime["args"]),
        ),
    }

    if not service_tier:
        route["request_overrides"] = {}
        return route

    try:
        overrides = resolve_fast_mode_overrides(route["model"])
    except Exception:
        overrides = None
    route["request_overrides"] = overrides or {}
    return route


def load_service_tier(
    config_path: Path,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    """Load Priority Processing setting from config.yaml."""

    cfg = _read_yaml_config(config_path)
    raw = str(cfg_get(cfg, "agent", "service_tier", default="") or "").strip()
    value = raw.lower()
    if not value or value in {"normal", "default", "standard", "off", "none"}:
        return None
    if value in {"fast", "priority", "on"}:
        return "priority"
    if logger is not None:
        logger.warning("Unknown service_tier '%s', ignoring", raw)
    return None


def load_provider_routing(config_path: Path) -> dict:
    """Load OpenRouter provider routing preferences from config.yaml."""

    cfg = _read_yaml_config(config_path)
    return cfg.get("provider_routing", {}) or {}


def load_fallback_model(config_path: Path) -> list | dict | None:
    """Load fallback provider chain from config.yaml."""

    cfg = _read_yaml_config(config_path)
    return cfg.get("fallback_providers") or cfg.get("fallback_model") or None


def extract_cache_busting_config(user_config: dict | None) -> dict[str, Any]:
    """Pull values that must bust the cached agent."""

    out: dict[str, Any] = {}
    cfg = user_config if isinstance(user_config, dict) else {}
    for section, key in CACHE_BUSTING_CONFIG_KEYS:
        section_val = cfg.get(section)
        if isinstance(section_val, dict):
            out[f"{section}.{key}"] = section_val.get(key)
        else:
            out[f"{section}.{key}"] = None
    try:
        from tools.registry import registry

        out["tools.registry_generation"] = getattr(registry, "_generation", None)
    except Exception:
        out["tools.registry_generation"] = None
    return out


def compute_agent_config_signature(
    model: str,
    runtime: dict,
    enabled_toolsets: list,
    ephemeral_prompt: str,
    cache_keys: dict | None = None,
) -> str:
    """Compute a stable string key from agent config values."""

    api_key = str(runtime.get("api_key", "") or "")
    api_key_fingerprint = hashlib.sha256(api_key.encode()).hexdigest() if api_key else ""
    cache_keys_sorted = sorted((cache_keys or {}).items())

    blob = json.dumps(
        [
            model,
            api_key_fingerprint,
            runtime.get("base_url", ""),
            runtime.get("provider", ""),
            runtime.get("api_mode", ""),
            sorted(enabled_toolsets) if enabled_toolsets else [],
            ephemeral_prompt or "",
            cache_keys_sorted,
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def snapshot_running_agents(
    running_agents: dict,
    *,
    pending_sentinel: object,
) -> dict:
    """Return running agents, excluding setup-pending sentinels."""

    return {
        session_key: agent
        for session_key, agent in running_agents.items()
        if agent is not pending_sentinel
    }


def init_cached_agent_for_turn(
    agent: Any,
    interrupt_depth: int,
    *,
    now: float | None = None,
) -> None:
    """Reset per-turn state on a cached agent before a new turn starts."""

    if interrupt_depth == 0:
        if now is None:
            import time

            now = time.time()
        agent._last_activity_ts = now
        agent._last_activity_desc = "starting new turn (cached)"
    agent._api_call_count = 0


def is_intentional_model_switch(
    session_model_overrides: dict,
    session_key: str,
    agent_model: str,
) -> bool:
    """Return True when the agent model matches a session-scoped override."""

    override = session_model_overrides.get(session_key)
    return override is not None and override.get("model") == agent_model


def release_running_agent_state(
    session_key: str,
    *,
    running_agents: dict,
    running_agents_ts: dict,
    busy_ack_ts: dict | None = None,
    run_generation: int | None = None,
    is_session_run_current: Any | None = None,
) -> bool:
    """Clear per-running-agent dictionaries for one session."""

    if not session_key:
        return False
    if (
        run_generation is not None
        and is_session_run_current is not None
        and not is_session_run_current(session_key, run_generation)
    ):
        return False
    running_agents.pop(session_key, None)
    running_agents_ts.pop(session_key, None)
    if busy_ack_ts is not None:
        busy_ack_ts.pop(session_key, None)
    return True


def release_evicted_agent_soft(
    agent: Any,
    *,
    cleanup_agent_resources: Any | None = None,
) -> None:
    """Soft-clean a cache-evicted agent while preserving session tool state."""

    if agent is None:
        return
    try:
        if hasattr(agent, "release_clients"):
            agent.release_clients()
        elif cleanup_agent_resources is not None:
            cleanup_agent_resources(agent)
    except Exception:
        pass


def _running_agent_ids(running_agents: dict, *, pending_sentinel: object) -> set[int]:
    return {
        id(agent)
        for agent in running_agents.values()
        if agent is not None and agent is not pending_sentinel
    }


def _cache_entry_agent(entry: Any) -> Any | None:
    return entry[0] if isinstance(entry, tuple) and entry else None


def enforce_agent_cache_cap(
    agent_cache: dict | None,
    *,
    running_agents: dict,
    pending_sentinel: object,
    max_size: int,
    schedule_release: Any,
    logger: logging.Logger | None = None,
) -> list:
    """Evict excess LRU cached agents while preserving active turns."""

    if agent_cache is None:
        return []
    if not hasattr(agent_cache, "move_to_end"):
        return []

    running_ids = _running_agent_ids(
        running_agents,
        pending_sentinel=pending_sentinel,
    )
    excess = max(0, len(agent_cache) - max_size)
    evict_plan: list[tuple] = []
    if excess > 0:
        ordered_keys = list(agent_cache.keys())
        for key in ordered_keys[:excess]:
            agent = _cache_entry_agent(agent_cache.get(key))
            if agent is not None and id(agent) in running_ids:
                continue
            evict_plan.append((key, agent))

    for key, _ in evict_plan:
        agent_cache.pop(key, None)

    remaining_over_cap = len(agent_cache) - max_size
    if remaining_over_cap > 0 and logger is not None:
        logger.warning(
            "Agent cache over cap (%d > %d); %d excess slot(s) held by "
            "mid-turn agents — will re-check on next insert.",
            len(agent_cache), max_size, remaining_over_cap,
        )

    evicted_keys = []
    for key, agent in evict_plan:
        evicted_keys.append(key)
        if logger is not None:
            logger.info(
                "Agent cache at cap; evicting LRU session=%s (cache_size=%d)",
                key, len(agent_cache),
            )
        if agent is not None:
            schedule_release(key, agent, "evict")
    return evicted_keys


def sweep_idle_cached_agents(
    agent_cache: dict | None,
    *,
    cache_lock: Any | None,
    running_agents: dict,
    pending_sentinel: object,
    idle_ttl_secs: float,
    schedule_release: Any,
    now: float | None = None,
    logger: logging.Logger | None = None,
) -> int:
    """Evict cached agents idle past the configured TTL."""

    if agent_cache is None or cache_lock is None:
        return 0
    if now is None:
        import time

        now = time.time()

    to_evict: list[tuple] = []
    running_ids = _running_agent_ids(
        running_agents,
        pending_sentinel=pending_sentinel,
    )
    with cache_lock:
        for key, entry in list(agent_cache.items()):
            agent = _cache_entry_agent(entry)
            if agent is None:
                continue
            if id(agent) in running_ids:
                continue
            last_activity = getattr(agent, "_last_activity_ts", None)
            if last_activity is None:
                continue
            if (now - last_activity) > idle_ttl_secs:
                to_evict.append((key, agent))
        for key, _ in to_evict:
            agent_cache.pop(key, None)

    for key, agent in to_evict:
        if logger is not None:
            logger.info(
                "Agent cache idle-TTL evict: session=%s (idle=%.0fs)",
                key, now - getattr(agent, "_last_activity_ts", now),
            )
        schedule_release(key, agent, "idle")
    return len(to_evict)
