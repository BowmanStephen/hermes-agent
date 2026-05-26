import pytest

from gateway.voice_enrichment import enrich_message_with_transcription


@pytest.mark.asyncio
async def test_enrich_message_with_transcription_surfaces_path_when_stt_disabled():
    async def _probe(path):
        assert path == "/tmp/voice.ogg"
        return "0:12"

    result = await enrich_message_with_transcription(
        "caption",
        ["/tmp/voice.ogg"],
        stt_enabled=False,
        probe_audio_duration=_probe,
        transcribe_audio_fn=lambda _path: pytest.fail("transcribe_audio should not run"),
    )

    assert "/tmp/voice.ogg" in result
    assert "(duration: 0:12)" in result
    assert result.endswith("caption")


@pytest.mark.asyncio
async def test_enrich_message_with_transcription_prepends_transcript_and_strips_placeholder():
    async def _to_thread(fn, *args):
        return fn(*args)

    result = await enrich_message_with_transcription(
        "(The user sent a message with no text content)",
        ["/tmp/voice.ogg"],
        stt_enabled=True,
        transcribe_audio_fn=lambda path: {"success": True, "transcript": f"hello from {path}"},
        to_thread_fn=_to_thread,
    )

    assert result == '[The user sent a voice message~ Here\'s what they said: "hello from /tmp/voice.ogg"]'


@pytest.mark.asyncio
async def test_enrich_message_with_transcription_mentions_setup_skill_for_missing_provider():
    async def _to_thread(fn, *args):
        return fn(*args)

    result = await enrich_message_with_transcription(
        "",
        ["/tmp/voice.ogg"],
        stt_enabled=True,
        transcribe_audio_fn=lambda _path: {"success": False, "error": "No STT provider configured"},
        has_setup_skill=lambda: True,
        to_thread_fn=_to_thread,
    )

    assert "no STT provider is configured" in result
    assert "hermes-agent-setup" in result
