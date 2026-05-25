"""Session configuration summary formatting for gateway commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _configured_context_length(data: dict[str, Any], model: str) -> int | None:
    model_cfg = data.get("model", {})
    if isinstance(model_cfg, dict):
        raw_ctx = model_cfg.get("context_length")
        if raw_ctx is not None:
            try:
                return int(raw_ctx)
            except (TypeError, ValueError):
                pass

    try:
        custom_providers = data.get("custom_providers", [])
        if custom_providers:
            for custom_provider in custom_providers:
                if not isinstance(custom_provider, dict):
                    continue
                provider_model = custom_provider.get("model") or ""
                provider_models = custom_provider.get("models") or {}
                if provider_model and provider_model == model:
                    raw_provider_ctx = custom_provider.get("context_length")
                    if raw_provider_ctx is not None:
                        try:
                            return int(raw_provider_ctx)
                        except (TypeError, ValueError):
                            pass
                if isinstance(provider_models, dict):
                    model_entry = provider_models.get(model)
                    if isinstance(model_entry, dict):
                        model_ctx = model_entry.get("context_length")
                    else:
                        model_ctx = model_entry
                    if model_ctx is not None and isinstance(model_ctx, (int, float)):
                        try:
                            return int(model_ctx)
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass
    return None


def _format_context_length(context_length: int) -> str:
    if context_length >= 1_000_000:
        return f"{context_length / 1_000_000:.1f}M"
    if context_length >= 1_000:
        return f"{context_length // 1_000}K"
    return str(context_length)


def format_session_info(
    *,
    resolve_gateway_model: Callable[[], str],
    load_gateway_config: Callable[[], dict[str, Any]],
    resolve_runtime_agent_kwargs: Callable[[], dict[str, Any]],
) -> str:
    """Resolve current model config and return a formatted info block."""

    from agent.model_metadata import DEFAULT_FALLBACK_CONTEXT, get_model_context_length

    model = resolve_gateway_model()
    config_context_length = None
    provider = None
    base_url = None
    api_key = None
    custom_providers = None
    data = None

    try:
        data = load_gateway_config()
        if data:
            model_cfg = data.get("model", {})
            if isinstance(model_cfg, dict):
                provider = model_cfg.get("provider") or None
                base_url = model_cfg.get("base_url") or None
            try:
                from hermes_cli.config import get_compatible_custom_providers

                custom_providers = get_compatible_custom_providers(data)
            except Exception:
                custom_providers = data.get("custom_providers")
    except Exception:
        pass

    if data:
        config_context_length = _configured_context_length(data, model)

    try:
        runtime = resolve_runtime_agent_kwargs()
        provider = provider or runtime.get("provider")
        base_url = base_url or runtime.get("base_url")
        api_key = runtime.get("api_key")
    except Exception:
        pass

    context_length = get_model_context_length(
        model,
        base_url=base_url or "",
        api_key=api_key or "",
        config_context_length=config_context_length,
        provider=provider or "",
        custom_providers=custom_providers,
    )

    if config_context_length is not None:
        context_source = "config"
    elif context_length == DEFAULT_FALLBACK_CONTEXT:
        context_source = "default - set model.context_length in config to override"
    else:
        context_source = "detected"

    lines = [
        f"◆ Model: `{model}`",
        f"◆ Provider: {provider or 'openrouter'}",
        f"◆ Context: {_format_context_length(context_length)} tokens ({context_source})",
    ]

    if base_url and (
        "localhost" in base_url
        or "127.0.0.1" in base_url
        or "0.0.0.0" in base_url
    ):
        lines.append(f"◆ Endpoint: {base_url}")

    return "\n".join(lines)
