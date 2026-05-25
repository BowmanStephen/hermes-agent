"""Proxy runner — delegates agent execution to a remote Hermes API server.

When ``GATEWAY_PROXY_URL`` is configured, the gateway acts as a thin relay:
it handles platform I/O (encryption, threading, media) and passes all agent
work to the remote via ``POST /v1/chat/completions`` with SSE streaming.

This module is intentionally independent from GatewayRunner so it can be
extracted, tested, and reused without dragging in the full gateway state.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from typing import Any

from gateway.config import Platform, StreamingConfig
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig

logger = logging.getLogger(__name__)


async def run_agent_via_proxy(
    proxy_url: str,
    message: str,
    context_prompt: str,
    history: list[dict[str, Any]],
    source: Any,
    session_id: str,
    session_key: str | None = None,
    run_generation: int | None = None,
    event_message_id: str | None = None,
    adapter: Any | None = None,
    is_session_run_current: Any | None = None,
    thread_metadata: dict[str, Any] | None = None,
    streaming_config: StreamingConfig | None = None,
) -> dict[str, Any]:
    """Forward the message to a remote Hermes API server instead of
    running a local AIAgent.

    The caller (GatewayRunner) passes resolved dependencies—proxy URL,
    adapter handle, session-run-current check, and thread metadata—so
    this function stays dependency-free.
    """
    try:
        from aiohttp import ClientSession as _AioClientSession, ClientTimeout
    except ImportError:
        return {
            "final_response": "⚠️ Proxy mode requires aiohttp. Install with: pip install aiohttp",
            "messages": [],
            "api_calls": 0,
            "tools": [],
        }

    proxy_key = os.getenv("GATEWAY_PROXY_KEY", "").strip()
    proxy_url = proxy_url.rstrip("/")

    def _run_still_current() -> bool:
        if is_session_run_current is None or run_generation is None or not session_key:
            return True
        return bool(is_session_run_current(session_key, run_generation))

    if not _run_still_current():
        return {
            "final_response": "",
            "messages": [],
            "api_calls": 0,
            "tools": [],
            "completed": False,
        }

    # Build messages in OpenAI chat format
    api_messages: list[dict[str, str]] = []
    if context_prompt:
        api_messages.append({"role": "system", "content": context_prompt})
    for msg in history:
        role = msg.get("role")
        content = msg.get("content")
        if role in {"user", "assistant"} and content:
            api_messages.append({"role": role, "content": content})
    api_messages.append({"role": "user", "content": message})

    # HTTP headers
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if proxy_key:
        headers["Authorization"] = f"Bearer {proxy_key}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id

    body = {
        "model": "hermes-agent",
        "messages": api_messages,
        "stream": True,
    }

    _stream_consumer = None
    stream_task = None

    if adapter is not None:
        _scfg = streaming_config
        if _scfg is None:
            _scfg = StreamingConfig()
        try:
            _streaming_enabled = bool(_scfg.enabled and _scfg.transport != "off")
        except Exception:
            _streaming_enabled = False

        if _streaming_enabled:
            try:
                _adapter_supports_edit = getattr(adapter, "SUPPORTS_MESSAGE_EDITING", True)
                _effective_cursor = _scfg.cursor if _adapter_supports_edit else ""
                _buffer_only = False
                if getattr(source, "platform", None) == Platform.MATRIX:
                    _effective_cursor = ""
                    _buffer_only = True
                _fresh_final_secs = (
                    float(getattr(_scfg, "fresh_final_after_seconds", 0.0) or 0.0)
                    if getattr(source, "platform", None) == Platform.TELEGRAM
                    else 0.0
                )
                consumer_config = StreamConsumerConfig(
                    edit_interval=_scfg.edit_interval,
                    buffer_threshold=_scfg.buffer_threshold,
                    cursor=_effective_cursor,
                    buffer_only=_buffer_only,
                    fresh_final_after_seconds=_fresh_final_secs,
                    transport=_scfg.transport or "edit",
                    chat_type=getattr(source, "chat_type", "") or "",
                )
                _stream_consumer = GatewayStreamConsumer(
                    adapter=adapter,
                    chat_id=getattr(source, "chat_id", None) or "",
                    config=consumer_config,
                    metadata=thread_metadata,
                    initial_reply_to_id=event_message_id,
                )
            except Exception:
                logger.debug("Proxy: could not set up stream consumer", exc_info=True)

    if _stream_consumer:
        stream_task = asyncio.create_task(_stream_consumer.run())

    _send_typing = getattr(adapter, "send_typing", None) if adapter else None
    _status_chat_id = getattr(source, "chat_id", None) or ""
    if _send_typing and _run_still_current():
        try:
            if thread_metadata:
                maybe_awaitable = _send_typing(_status_chat_id, metadata=thread_metadata)
            else:
                maybe_awaitable = _send_typing(_status_chat_id)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            pass

    import aiohttp
    timeout = ClientTimeout(total=0, sock_read=1800)
    _t0 = time.time()
    full_response = ""

    try:
        async with _AioClientSession(timeout=timeout) as session:
            async with session.post(
                f"{proxy_url}/v1/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                if getattr(resp, "status", 200) != 200:
                    error_text = await resp.text()
                    logger.warning(
                        "Proxy error (%d) from %s: %s",
                        resp.status, proxy_url, error_text[:500],
                    )
                    return {
                        "final_response": f"⚠️ Proxy error ({resp.status}): {error_text[:300]}",
                        "messages": [],
                        "api_calls": 0,
                        "tools": [],
                    }

                buffer = ""
                done = False
                content = getattr(resp, "content", resp)
                chunk_iter = content.iter_any() if hasattr(content, "iter_any") else content
                async for raw_chunk in chunk_iter:
                    if not _run_still_current():
                        logger.info(
                            "Discarding stale proxy stream for %s — generation %d is no longer current",
                            session_key or "?",
                            run_generation or 0,
                        )
                        return {
                            "final_response": "",
                            "messages": [],
                            "api_calls": 0,
                            "tools": [],
                            "history_offset": len(history),
                            "session_id": session_id,
                            "response_previewed": False,
                        }
                    buffer += raw_chunk.decode("utf-8", errors="replace")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        payload = line[6:] if line.startswith("data: ") else line
                        if payload.strip() == "[DONE]":
                            done = True
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        text = delta.get("content", "")
                        if text:
                            full_response += text
                            if _stream_consumer:
                                _stream_consumer.on_delta(text)
                    if done:
                        break

                if not full_response and buffer.strip():
                    try:
                        data = json.loads(buffer)
                        full_response = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    except Exception:
                        full_response = buffer.strip()

    except asyncio.CancelledError:
        raise
    except aiohttp.ClientError as e:
        logger.warning("Proxy connection error to %s: %s", proxy_url, e, exc_info=True)
        return {
            "final_response": f"⚠️ Proxy connection error: {e}",
            "messages": [],
            "api_calls": 0,
            "tools": [],
        }
    except Exception as e:
        logger.error("Proxy connection error to %s: %s", proxy_url, e)
        if not full_response:
            return {
                "final_response": f"⚠️ Proxy connection error: {e}",
                "messages": [],
                "api_calls": 0,
                "tools": [],
            }
    finally:
        if _stream_consumer:
            _stream_consumer.finish()
        if stream_task:
            try:
                await asyncio.wait_for(stream_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                stream_task.cancel()

        _elapsed = time.time() - _t0
        logger.info(
            "proxy response: url=%s session=%s time=%.1fs response=%d chars",
            proxy_url, (session_id or "")[:20], _elapsed, len(full_response),
        )

    if not _run_still_current():
        logger.info(
            "Discarding stale proxy result for %s — generation %d is no longer current",
            session_key or "?",
            run_generation or 0,
        )
        return {
            "final_response": "",
            "messages": [],
            "api_calls": 0,
            "tools": [],
            "history_offset": len(history),
            "session_id": session_id,
            "response_previewed": False,
        }

    return {
        "final_response": full_response or "(No response from remote agent)",
        "messages": [
            {"role": "user", "content": message},
            {"role": "assistant", "content": full_response},
        ],
        "api_calls": 1,
        "tools": [],
        "history_offset": len(history),
        "session_id": session_id,
        "response_previewed": _stream_consumer is not None and bool(full_response),
    }
