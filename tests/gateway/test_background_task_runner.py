from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.background_task_runner import run_background_task
from gateway.config import Platform
from gateway.platforms.base import SendResult
from gateway.session import SessionSource


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        user_name="Ada",
        chat_id="chat-1",
        chat_type="dm",
        thread_id="topic-1",
    )


def _adapter() -> SimpleNamespace:
    return SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True)),
        send_image=AsyncMock(return_value=SendResult(success=True)),
        send_document=AsyncMock(return_value=SendResult(success=True)),
        extract_media=MagicMock(return_value=([], "background response")),
        extract_images=MagicMock(return_value=([], "background response")),
    )


@pytest.mark.asyncio
async def test_run_background_task_sends_credential_error_without_agent() -> None:
    adapter = _adapter()
    agent_cls = MagicMock()

    await run_background_task(
        prompt="summarize",
        source=_source(),
        task_id="bg_test",
        adapters={Platform.TELEGRAM: adapter},
        thread_metadata_for_source=lambda source, anchor=None: {"thread_id": source.thread_id},
        load_gateway_config=lambda: {},
        resolve_session_agent_runtime=lambda **_kw: ("model", {}),
        platform_config_key=lambda platform: platform.value,
        get_platform_tools=lambda _config, _platform: set(),
        provider_routing={},
        resolve_session_reasoning_config=lambda **_kw: None,
        set_reasoning_config=lambda _value: None,
        load_service_tier=lambda: None,
        set_service_tier=lambda _value: None,
        resolve_turn_agent_config=lambda _prompt, model, runtime: {"model": model, "runtime": runtime},
        enrich_message_with_vision=AsyncMock(),
        run_in_executor_with_context=AsyncMock(),
        cleanup_agent_resources=MagicMock(),
        agent_cls=agent_cls,
    )

    adapter.send.assert_awaited_once()
    assert "no provider credentials" in adapter.send.call_args.kwargs["content"]
    agent_cls.assert_not_called()


@pytest.mark.asyncio
async def test_run_background_task_runs_agent_and_sends_text_result() -> None:
    adapter = _adapter()
    cleanup = MagicMock()

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_conversation(self, *, user_message, task_id):
            assert user_message == "summarize"
            assert task_id == "bg_test"
            return {"final_response": "background response"}

    async def _executor(fn):
        return fn()

    await run_background_task(
        prompt="summarize",
        source=_source(),
        task_id="bg_test",
        adapters={Platform.TELEGRAM: adapter},
        thread_metadata_for_source=lambda source, anchor=None: {"thread_id": source.thread_id},
        load_gateway_config=lambda: {"agent": {"disabled_toolsets": ["web"]}},
        resolve_session_agent_runtime=lambda **_kw: ("model", {"api_key": "sk-test"}),
        platform_config_key=lambda platform: platform.value,
        get_platform_tools=lambda _config, _platform: {"terminal"},
        provider_routing={"only": ["anthropic"]},
        resolve_session_reasoning_config=lambda **_kw: {"effort": "low"},
        set_reasoning_config=lambda _value: None,
        load_service_tier=lambda: "priority",
        set_service_tier=lambda _value: None,
        resolve_turn_agent_config=lambda _prompt, model, runtime: {
            "model": model,
            "runtime": runtime,
            "request_overrides": {"temperature": 0},
        },
        enrich_message_with_vision=AsyncMock(),
        run_in_executor_with_context=_executor,
        cleanup_agent_resources=cleanup,
        session_db="db",
        fallback_model="fallback",
        agent_cls=FakeAgent,
    )

    adapter.send.assert_awaited_once()
    content = adapter.send.call_args.kwargs["content"]
    assert "Background task complete" in content
    assert "background response" in content
    cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_run_background_task_enriches_image_attachments() -> None:
    adapter = _adapter()
    seen: dict[str, str] = {}

    class FakeAgent:
        def __init__(self, **_kwargs):
            pass

        def run_conversation(self, *, user_message, task_id):
            seen["message"] = user_message
            return {"final_response": "background response"}

    async def _executor(fn):
        return fn()

    enrich = AsyncMock(return_value="enriched prompt")

    await run_background_task(
        prompt="summarize image",
        source=_source(),
        task_id="bg_test",
        adapters={Platform.TELEGRAM: adapter},
        thread_metadata_for_source=lambda _source, _anchor=None: None,
        load_gateway_config=lambda: {},
        resolve_session_agent_runtime=lambda **_kw: ("model", {"api_key": "sk-test"}),
        platform_config_key=lambda platform: platform.value,
        get_platform_tools=lambda _config, _platform: set(),
        provider_routing={},
        resolve_session_reasoning_config=lambda **_kw: None,
        set_reasoning_config=lambda _value: None,
        load_service_tier=lambda: None,
        set_service_tier=lambda _value: None,
        resolve_turn_agent_config=lambda _prompt, model, runtime: {"model": model, "runtime": runtime},
        enrich_message_with_vision=enrich,
        run_in_executor_with_context=_executor,
        cleanup_agent_resources=MagicMock(),
        agent_cls=FakeAgent,
        media_urls=["/tmp/a.png", "/tmp/voice.ogg"],
        media_types=["image/png", "audio/ogg"],
    )

    enrich.assert_awaited_once_with("summarize image", ["/tmp/a.png"])
    assert seen["message"] == "enriched prompt"
