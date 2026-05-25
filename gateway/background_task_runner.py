"""Background slash-command task execution helpers."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, Optional

from gateway.session import SessionSource

logger = logging.getLogger(__name__)


async def run_background_task(
    *,
    prompt: str,
    source: SessionSource,
    task_id: str,
    adapters: Mapping[Any, Any],
    thread_metadata_for_source: Callable[[SessionSource, Optional[str]], Any],
    load_gateway_config: Callable[[], dict],
    resolve_session_agent_runtime: Callable[..., tuple[str, dict]],
    platform_config_key: Callable[[Any], str],
    get_platform_tools: Callable[[dict, str], set[str]],
    provider_routing: dict[str, Any],
    resolve_session_reasoning_config: Callable[..., dict | None],
    set_reasoning_config: Callable[[dict | None], None],
    load_service_tier: Callable[[], Any],
    set_service_tier: Callable[[Any], None],
    resolve_turn_agent_config: Callable[[str, str, dict], dict[str, Any]],
    enrich_message_with_vision: Callable[[str, list[str]], Any],
    run_in_executor_with_context: Callable[[Callable[[], Any]], Any],
    cleanup_agent_resources: Callable[[Any], None],
    session_db: Any = None,
    fallback_model: Any = None,
    event_message_id: Optional[str] = None,
    media_urls: Optional[list[str]] = None,
    media_types: Optional[list[str]] = None,
    agent_cls: Any = None,
    log: logging.Logger = logger,
) -> None:
    """Execute a background agent task and deliver the result to the chat."""
    adapter = adapters.get(source.platform)
    if not adapter:
        log.warning("No adapter for platform %s in background task %s", source.platform, task_id)
        return

    thread_metadata = thread_metadata_for_source(source, event_message_id)
    try:
        config = load_gateway_config()
        model, runtime_kwargs = resolve_session_agent_runtime(
            source=source,
            user_config=config,
        )
        if not runtime_kwargs.get("api_key"):
            await _send_credentials_error(adapter, source, task_id, thread_metadata)
            return

        execution = await _run_background_agent(
            prompt=prompt,
            source=source,
            task_id=task_id,
            config=config,
            model=model,
            runtime_kwargs=runtime_kwargs,
            media_urls=media_urls or [],
            media_types=media_types or [],
            platform_config_key=platform_config_key,
            get_platform_tools=get_platform_tools,
            provider_routing=provider_routing,
            resolve_session_reasoning_config=resolve_session_reasoning_config,
            set_reasoning_config=set_reasoning_config,
            load_service_tier=load_service_tier,
            set_service_tier=set_service_tier,
            resolve_turn_agent_config=resolve_turn_agent_config,
            enrich_message_with_vision=enrich_message_with_vision,
            run_in_executor_with_context=run_in_executor_with_context,
            cleanup_agent_resources=cleanup_agent_resources,
            session_db=session_db,
            fallback_model=fallback_model,
            agent_cls=agent_cls,
            log=log,
        )
        await _deliver_background_result(
            adapter, source, prompt, execution, thread_metadata,
        )
    except Exception as exc:
        log.exception("Background task %s failed", task_id)
        await _try_send_background_error(adapter, source, task_id, exc, thread_metadata)


async def _send_credentials_error(adapter: Any, source: SessionSource, task_id: str, metadata: Any) -> None:
    await adapter.send(
        chat_id=source.chat_id,
        content=f"❌ Background task {task_id} failed: no provider credentials configured.",
        metadata=metadata,
    )


async def _run_background_agent(
    *,
    prompt: str,
    source: SessionSource,
    task_id: str,
    config: dict,
    model: str,
    runtime_kwargs: dict,
    media_urls: list[str],
    media_types: list[str],
    platform_config_key: Callable[[Any], str],
    get_platform_tools: Callable[[dict, str], set[str]],
    provider_routing: dict[str, Any],
    resolve_session_reasoning_config: Callable[..., dict | None],
    set_reasoning_config: Callable[[dict | None], None],
    load_service_tier: Callable[[], Any],
    set_service_tier: Callable[[Any], None],
    resolve_turn_agent_config: Callable[[str, str, dict], dict[str, Any]],
    enrich_message_with_vision: Callable[[str, list[str]], Any],
    run_in_executor_with_context: Callable[[Callable[[], Any]], Any],
    cleanup_agent_resources: Callable[[Any], None],
    session_db: Any,
    fallback_model: Any,
    agent_cls: Any,
    log: logging.Logger,
) -> dict:
    if agent_cls is None:
        from run_agent import AIAgent as agent_cls

    platform_key = platform_config_key(source.platform)
    enabled_toolsets = sorted(get_platform_tools(config, platform_key))
    disabled_toolsets = (config.get("agent") or {}).get("disabled_toolsets") or None
    reasoning_config = resolve_session_reasoning_config(source=source)
    set_reasoning_config(reasoning_config)
    service_tier = load_service_tier()
    set_service_tier(service_tier)
    turn_route = resolve_turn_agent_config(prompt, model, runtime_kwargs)
    enriched_prompt = await _enrich_background_prompt(
        prompt, media_urls, media_types, enrich_message_with_vision, log,
    )
    return await run_in_executor_with_context(
        lambda: _run_agent_sync(
            agent_cls=agent_cls,
            source=source,
            task_id=task_id,
            prompt=enriched_prompt,
            turn_route=turn_route,
            max_iterations=int(os.getenv("HERMES_MAX_ITERATIONS", "90")),
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=disabled_toolsets,
            reasoning_config=reasoning_config,
            service_tier=service_tier,
            provider_routing=provider_routing,
            session_db=session_db,
            fallback_model=fallback_model,
            platform_key=platform_key,
            cleanup_agent_resources=cleanup_agent_resources,
        )
    )


async def _enrich_background_prompt(
    prompt: str,
    media_urls: list[str],
    media_types: list[str],
    enrich_message_with_vision: Callable[[str, list[str]], Any],
    log: logging.Logger,
) -> str:
    image_paths = [
        path for index, path in enumerate(media_urls)
        if (media_types[index] if index < len(media_types) else "").startswith("image/")
    ]
    if not image_paths:
        return prompt
    try:
        return await enrich_message_with_vision(prompt, image_paths)
    except Exception as exc:
        log.warning("Background task vision enrichment failed: %s", exc)
        return prompt


def _run_agent_sync(
    *,
    agent_cls: Any,
    source: SessionSource,
    task_id: str,
    prompt: str,
    turn_route: dict[str, Any],
    max_iterations: int,
    enabled_toolsets: list[str],
    disabled_toolsets: list[str] | None,
    reasoning_config: dict | None,
    service_tier: Any,
    provider_routing: dict[str, Any],
    session_db: Any,
    fallback_model: Any,
    platform_key: str,
    cleanup_agent_resources: Callable[[Any], None],
) -> dict:
    agent = agent_cls(
        model=turn_route["model"],
        **turn_route["runtime"],
        max_iterations=max_iterations,
        quiet_mode=True,
        verbose_logging=False,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        reasoning_config=reasoning_config,
        service_tier=service_tier,
        request_overrides=turn_route.get("request_overrides"),
        providers_allowed=provider_routing.get("only"),
        providers_ignored=provider_routing.get("ignore"),
        providers_order=provider_routing.get("order"),
        provider_sort=provider_routing.get("sort"),
        provider_require_parameters=provider_routing.get("require_parameters", False),
        provider_data_collection=provider_routing.get("data_collection"),
        session_id=task_id,
        platform=platform_key,
        user_id=source.user_id,
        user_name=source.user_name,
        chat_id=source.chat_id,
        chat_name=source.chat_name,
        chat_type=source.chat_type,
        thread_id=source.thread_id,
        session_db=session_db,
        fallback_model=fallback_model,
    )
    try:
        return agent.run_conversation(user_message=prompt, task_id=task_id)
    finally:
        cleanup_agent_resources(agent)


async def _deliver_background_result(
    adapter: Any,
    source: SessionSource,
    prompt: str,
    result: dict | None,
    metadata: Any,
) -> None:
    response = result.get("final_response", "") if result else ""
    if not response and result and result.get("error"):
        response = f"Error: {result['error']}"
    if not response:
        await _send_empty_background_result(adapter, source, prompt, metadata)
        return

    media_files, response = adapter.extract_media(response)
    images, text_content = adapter.extract_images(response)
    header = f'✅ Background task complete\nPrompt: "{_preview_prompt(prompt)}"\n\n'
    if text_content:
        await adapter.send(chat_id=source.chat_id, content=header + text_content, metadata=metadata)
    elif not images and not media_files:
        await adapter.send(
            chat_id=source.chat_id,
            content=header + "(No response generated)",
            metadata=metadata,
        )
    await _send_background_images(adapter, source, images or [], metadata)
    await _send_background_media(adapter, source, media_files or [], metadata)


async def _send_empty_background_result(adapter: Any, source: SessionSource, prompt: str, metadata: Any) -> None:
    await adapter.send(
        chat_id=source.chat_id,
        content=f'✅ Background task complete\nPrompt: "{_preview_prompt(prompt)}"\n\n(No response generated)',
        metadata=metadata,
    )


async def _send_background_images(adapter: Any, source: SessionSource, images: list[tuple[str, str]], metadata: Any) -> None:
    for image_url, alt_text in images:
        try:
            await adapter.send_image(
                chat_id=source.chat_id,
                image_url=image_url,
                caption=alt_text,
                metadata=metadata,
            )
        except Exception:
            pass


async def _send_background_media(adapter: Any, source: SessionSource, media_files: list[tuple[str, bool]], metadata: Any) -> None:
    for media_path, _is_voice in media_files:
        try:
            await adapter.send_document(
                chat_id=source.chat_id,
                file_path=media_path,
                metadata=metadata,
            )
        except Exception:
            pass


async def _try_send_background_error(adapter: Any, source: SessionSource, task_id: str, exc: Exception, metadata: Any) -> None:
    try:
        await adapter.send(
            chat_id=source.chat_id,
            content=f"❌ Background task {task_id} failed: {exc}",
            metadata=metadata,
        )
    except Exception:
        pass


def _preview_prompt(prompt: str) -> str:
    return prompt[:60] + ("..." if len(prompt) > 60 else "")
