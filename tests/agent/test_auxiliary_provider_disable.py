"""Regression tests for provider enablement across auxiliary LLM routes."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

import agent.auxiliary_client as auxiliary


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-only")
    auxiliary._client_cache.clear()
    auxiliary._aux_unhealthy_until.clear()
    auxiliary._aux_unhealthy_logged_at.clear()
    yield hermes_home
    auxiliary._client_cache.clear()
    auxiliary._aux_unhealthy_until.clear()
    auxiliary._aux_unhealthy_logged_at.clear()


def _write_config(hermes_home, *, enabled: bool, provider: str = "openai-codex") -> None:
    config = {
        "model": {"provider": provider, "default": "gpt-5.6-luna"},
        "providers": {"openrouter": {"enabled": enabled}},
    }
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(config))


def test_explicit_openrouter_cannot_create_client_when_disabled(
    _isolated_config, monkeypatch
):
    _write_config(_isolated_config, enabled=False)
    create_client = MagicMock()
    monkeypatch.setattr(auxiliary, "_create_openai_client", create_client)

    client, model = auxiliary.resolve_provider_client(
        "openrouter",
        "google/gemini-3.7-flash",
        task="title_generation",
    )

    assert (client, model) == (None, None)
    create_client.assert_not_called()


def test_explicit_openrouter_still_works_when_enabled(
    _isolated_config, monkeypatch
):
    _write_config(_isolated_config, enabled=True)
    expected_client = SimpleNamespace(base_url="https://openrouter.ai/api/v1")
    create_client = MagicMock(return_value=expected_client)
    monkeypatch.setattr(auxiliary, "_create_openai_client", create_client)
    monkeypatch.setattr(auxiliary, "_select_pool_entry", lambda provider: (False, None))

    client, model = auxiliary.resolve_provider_client(
        "openrouter",
        "google/gemini-3.7-flash",
        task="title_generation",
    )

    assert client is expected_client
    assert model == "google/gemini-3.7-flash"
    create_client.assert_called_once()


def test_auto_fallback_skips_disabled_openrouter(
    _isolated_config, monkeypatch
):
    # No selected main provider: upstream only walks the built-in discovery chain then
    # (_discovery_chain_allowed), and that chain is what must skip disabled OpenRouter.
    _write_config(_isolated_config, enabled=False, provider="auto")
    fallback_client = SimpleNamespace(base_url="https://api.z.ai/v1")
    openrouter_attempt = MagicMock(
        side_effect=lambda: auxiliary._try_openrouter(
            model="google/gemini-3.7-flash"
        )
    )
    fallback_attempt = MagicMock(return_value=(fallback_client, "glm-4.5-flash"))
    monkeypatch.setattr(
        auxiliary,
        "_get_provider_chain",
        lambda: [
            ("openrouter", openrouter_attempt),
            ("zai", fallback_attempt),
        ],
    )
    monkeypatch.setattr(auxiliary, "_try_main_fallback_chain", lambda *a, **k: (None, None, ""))

    client, model, provider = auxiliary._resolve_auto_route(main_runtime={})

    assert client is fallback_client
    assert model == "glm-4.5-flash"
    assert provider == "zai"
    openrouter_attempt.assert_called_once()
    fallback_attempt.assert_called_once()


def test_cached_auto_openrouter_client_is_not_reused_after_disable(
    _isolated_config, monkeypatch
):
    _write_config(_isolated_config, enabled=False)
    cached_client = SimpleNamespace(
        base_url="https://openrouter.ai/api/v1",
        _hermes_aux_effective_provider="openrouter",
    )
    replacement_client = SimpleNamespace(base_url="https://api.z.ai/v1")
    cache_key = auxiliary._client_cache_key(
        "auto",
        async_mode=False,
        main_runtime={},
        task="compression",
        model="google/gemini-3.7-flash",
    )
    auxiliary._client_cache[cache_key] = (
        cached_client,
        "google/gemini-3.7-flash",
        None,
    )
    resolve = MagicMock(return_value=(replacement_client, "glm-4.5-flash"))
    monkeypatch.setattr(auxiliary, "resolve_provider_client", resolve)

    client, model = auxiliary._get_cached_client(
        "auto",
        "google/gemini-3.7-flash",
        main_runtime={},
        task="compression",
    )

    assert client is replacement_client
    # The replacement is not OpenRouter-backed, so _compat_model() swaps the vendor/model slug
    # for the resolver's default rather than echoing the requested id.
    assert model == "glm-4.5-flash"
    assert client is not cached_client
    resolve.assert_called_once()


def test_moa_advisor_cannot_reach_disabled_openrouter(
    _isolated_config, monkeypatch
):
    _write_config(_isolated_config, enabled=False)
    create_client = MagicMock()
    monkeypatch.setattr(auxiliary, "_create_openai_client", create_client)

    client, model = auxiliary._get_cached_client(
        "openrouter",
        "deepseek/deepseek-v4-pro",
        task="moa_reference",
    )

    assert client is None
    assert model in (None, "deepseek/deepseek-v4-pro")
    create_client.assert_not_called()


def test_moa_aggregator_unwrap_honors_disabled_provider(
    _isolated_config, monkeypatch
):
    _write_config(_isolated_config, enabled=False)
    monkeypatch.setattr(
        auxiliary,
        "_resolve_moa_aggregator",
        lambda preset: ("openrouter", "anthropic/claude-opus-4.8"),
    )
    create_client = MagicMock()
    monkeypatch.setattr(auxiliary, "_create_openai_client", create_client)

    client, model = auxiliary.resolve_provider_client("moa", "deep_review")

    assert (client, model) == (None, None)
    create_client.assert_not_called()
