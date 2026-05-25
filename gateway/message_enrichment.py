"""Inbound message enrichment helpers for GatewayRunner."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any


logger = logging.getLogger(__name__)

VisionAnalyzer = Callable[..., Awaitable[str]]
Sanitizer = Callable[[str], str]

VISION_ANALYSIS_PROMPT = (
    "Describe everything visible in this image in thorough detail. "
    "Include any text, code, data, objects, people, layout, colors, "
    "and any other notable visual information."
)


def resolve_image_input_mode(
    *,
    load_config_fn: Callable[[], Any] | None = None,
    read_provider_fn: Callable[[], str] | None = None,
    read_model_fn: Callable[[], str] | None = None,
    decide_fn: Callable[[str, str, Any], str] | None = None,
    logger: logging.Logger = logger,
) -> str:
    """Resolve image-input routing for the currently active model."""

    try:
        if load_config_fn is None:
            from hermes_cli.config import load_config

            load_config_fn = load_config
        if read_provider_fn is None:
            from agent.auxiliary_client import _read_main_provider

            read_provider_fn = _read_main_provider
        if read_model_fn is None:
            from agent.auxiliary_client import _read_main_model

            read_model_fn = _read_main_model
        if decide_fn is None:
            from agent.image_routing import decide_image_input_mode

            decide_fn = decide_image_input_mode

        cfg = load_config_fn()
        provider = read_provider_fn()
        model = read_model_fn()
        return decide_fn(provider, model, cfg)
    except Exception as exc:
        logger.debug("image_routing: decision failed, falling back to text — %s", exc)
        return "text"


async def enrich_message_with_vision(
    user_text: str,
    image_paths: Sequence[str],
    *,
    analyzer: VisionAnalyzer | None = None,
    sanitizer: Sanitizer | None = None,
    logger: logging.Logger = logger,
) -> str:
    """Auto-analyze attached images and prepend sanitized descriptions."""

    if not image_paths:
        return user_text

    if analyzer is None:
        from tools.vision_tools import vision_analyze_tool

        analyzer = vision_analyze_tool
    if sanitizer is None:
        from agent.memory_manager import sanitize_context

        sanitizer = sanitize_context

    enriched_parts = []
    for path in image_paths:
        try:
            logger.debug("Auto-analyzing user image: %s", path)
            result_json = await analyzer(
                image_url=path,
                user_prompt=VISION_ANALYSIS_PROMPT,
            )
            result = json.loads(result_json)
            if result.get("success"):
                description = sanitizer(result.get("analysis", ""))
                enriched_parts.append(
                    f"[The user sent an image~ Here's what I can see:\n{description}]\n"
                    f"[If you need a closer look, use vision_analyze with "
                    f"image_url: {path} ~]"
                )
            else:
                enriched_parts.append(
                    "[The user sent an image but I couldn't quite see it "
                    "this time (>_<) You can try looking at it yourself "
                    f"with vision_analyze using image_url: {path}]"
                )
        except Exception as e:
            logger.error("Vision auto-analysis error: %s", e)
            enriched_parts.append(
                f"[The user sent an image but something went wrong when I "
                f"tried to look at it~ You can try examining it yourself "
                f"with vision_analyze using image_url: {path}]"
            )

    if enriched_parts:
        prefix = "\n\n".join(enriched_parts)
        if user_text:
            return f"{prefix}\n\n{user_text}"
        return prefix
    return user_text
