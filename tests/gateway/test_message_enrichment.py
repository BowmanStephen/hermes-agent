import json
from unittest.mock import Mock

import pytest

from gateway.message_enrichment import (
    enrich_message_with_vision,
    resolve_image_input_mode,
)


def test_resolve_image_input_mode_uses_current_provider_model_and_config():
    calls = {}

    def _decide(provider, model, cfg):
        calls["args"] = (provider, model, cfg)
        return "native"

    result = resolve_image_input_mode(
        load_config_fn=lambda: {"gateway": "cfg"},
        read_provider_fn=lambda: "openrouter",
        read_model_fn=lambda: "qwen/qwen-vl",
        decide_fn=_decide,
    )

    assert result == "native"
    assert calls["args"] == ("openrouter", "qwen/qwen-vl", {"gateway": "cfg"})


def test_resolve_image_input_mode_falls_back_to_text_on_errors():
    logger = Mock()

    def _explode():
        raise RuntimeError("config unavailable")

    result = resolve_image_input_mode(load_config_fn=_explode, logger=logger)

    assert result == "text"
    logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_enrich_message_with_vision_prepends_sanitized_analysis_and_path():
    async def _analyze(*, image_url, user_prompt):
        assert image_url == "/tmp/cat.png"
        assert "Describe everything visible" in user_prompt
        return json.dumps({"success": True, "analysis": "  <secret>cat</secret>  "})

    result = await enrich_message_with_vision(
        "caption",
        ["/tmp/cat.png"],
        analyzer=_analyze,
        sanitizer=lambda text: text.replace("<secret>", "").replace("</secret>", "").strip(),
    )

    assert result.startswith("[The user sent an image~ Here's what I can see:\ncat]")
    assert "vision_analyze with image_url: /tmp/cat.png" in result
    assert result.endswith("caption")


@pytest.mark.asyncio
async def test_enrich_message_with_vision_returns_user_text_without_images():
    result = await enrich_message_with_vision(
        "plain text",
        [],
        analyzer=Mock(),
        sanitizer=lambda text: text,
    )

    assert result == "plain text"
