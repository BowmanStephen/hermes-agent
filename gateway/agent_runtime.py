"""Agent runtime resolution helpers for gateway sessions."""

from __future__ import annotations

import logging
from typing import Any


def apply_session_model_override(
    session_model_overrides: dict,
    session_key: str,
    model: str,
    runtime_kwargs: dict,
) -> tuple:
    """Apply a session-scoped /model override to runtime config."""

    override = session_model_overrides.get(session_key)
    if not override:
        return model, runtime_kwargs
    model = override.get("model", model)
    for key in ("provider", "api_key", "base_url", "api_mode"):
        val = override.get(key)
        if val is not None:
            runtime_kwargs[key] = val
    return model, runtime_kwargs


def resolve_session_agent_runtime(
    *,
    source: Any | None = None,
    session_key: str | None = None,
    user_config: dict | None = None,
    session_model_overrides: dict,
    resolve_session_key_for_source: Any,
    resolve_gateway_model: Any,
    resolve_runtime_agent_kwargs: Any,
    default_model_for_provider: Any | None = None,
    logger: logging.Logger | None = None,
) -> tuple[str, dict]:
    """Resolve model/runtime for a session, honoring session-scoped overrides."""

    resolved_session_key = session_key
    if not resolved_session_key and source is not None:
        try:
            resolved_session_key = resolve_session_key_for_source(source)
        except Exception:
            resolved_session_key = None

    model = resolve_gateway_model(user_config)
    override = (
        session_model_overrides.get(resolved_session_key)
        if resolved_session_key
        else None
    )
    if override:
        override_model = override.get("model", model)
        override_runtime = {
            "provider": override.get("provider"),
            "api_key": override.get("api_key"),
            "base_url": override.get("base_url"),
            "api_mode": override.get("api_mode"),
        }
        if override_runtime.get("api_key"):
            if logger is not None:
                logger.debug(
                    "Session model override (fast): session=%s config_model=%s -> "
                    "override_model=%s provider=%s",
                    resolved_session_key or "",
                    model,
                    override_model,
                    override_runtime.get("provider"),
                )
            return override_model, override_runtime
        if logger is not None:
            logger.debug(
                "Session model override (no api_key, fallback): session=%s "
                "config_model=%s override_model=%s",
                resolved_session_key or "",
                model,
                override_model,
            )
    elif logger is not None:
        logger.debug(
            "No session model override: session=%s config_model=%s override_keys=%s",
            resolved_session_key or "",
            model,
            list(session_model_overrides.keys())[:5] if session_model_overrides else "[]",
        )

    runtime_kwargs = resolve_runtime_agent_kwargs()
    runtime_model = runtime_kwargs.pop("model", None)
    if runtime_model:
        if logger is not None:
            logger.info(
                "Runtime provider supplied explicit model override: %s -> %s",
                model,
                runtime_model,
            )
        model = runtime_model
    if override and resolved_session_key:
        model, runtime_kwargs = apply_session_model_override(
            session_model_overrides,
            resolved_session_key,
            model,
            runtime_kwargs,
        )

    if not model and runtime_kwargs.get("provider"):
        try:
            if default_model_for_provider is None:
                from hermes_cli.models import get_default_model_for_provider

                default_model_for_provider = get_default_model_for_provider
            model = default_model_for_provider(runtime_kwargs["provider"])
            if model and logger is not None:
                logger.info(
                    "No model configured - defaulting to %s for provider %s",
                    model,
                    runtime_kwargs["provider"],
                )
        except Exception:
            pass

    return model, runtime_kwargs
