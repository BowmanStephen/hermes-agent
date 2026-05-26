"""Direct tests for voice-mode persistence and adapter sync helpers."""

import json
from types import SimpleNamespace

from gateway.config import Platform
from gateway.voice_mode_manager import (
    load_voice_modes,
    save_voice_modes,
    set_adapter_auto_tts_disabled,
    set_adapter_auto_tts_enabled,
    sync_voice_mode_state_to_adapter,
    voice_mode_key,
)


def test_voice_mode_key_namespaces_platform_chat_ids():
    assert voice_mode_key(Platform.TELEGRAM, "123") == "telegram:123"
    assert voice_mode_key(Platform.SLACK, "123") == "slack:123"


def test_load_voice_modes_filters_invalid_and_legacy_keys(tmp_path, caplog):
    path = tmp_path / "gateway_voice_mode.json"
    path.write_text(
        json.dumps(
            {
                "123": "all",
                "telegram:456": "voice_only",
                "telegram:bad": "invalid",
                "discord:789": "off",
            }
        )
    )

    assert load_voice_modes(path) == {
        "telegram:456": "voice_only",
        "discord:789": "off",
    }
    assert "Skipping legacy unprefixed voice mode key" in caplog.text


def test_save_voice_modes_creates_parent_and_writes_json(tmp_path):
    path = tmp_path / "nested" / "gateway_voice_mode.json"

    save_voice_modes(path, {"telegram:123": "all"})

    assert json.loads(path.read_text()) == {"telegram:123": "all"}


def test_adapter_tts_toggles_keep_enabled_and_disabled_sets_exclusive():
    adapter = SimpleNamespace(
        _auto_tts_disabled_chats=set(),
        _auto_tts_enabled_chats=set(),
    )

    set_adapter_auto_tts_enabled(adapter, "chat-1", enabled=True)
    assert adapter._auto_tts_enabled_chats == {"chat-1"}
    assert adapter._auto_tts_disabled_chats == set()

    set_adapter_auto_tts_disabled(adapter, "chat-1", disabled=True)
    assert adapter._auto_tts_disabled_chats == {"chat-1"}
    assert adapter._auto_tts_enabled_chats == set()


def test_sync_voice_mode_state_to_adapter_filters_platform_and_pushes_default():
    adapter = SimpleNamespace(
        platform=Platform.TELEGRAM,
        _auto_tts_default=False,
        _auto_tts_disabled_chats={"stale"},
        _auto_tts_enabled_chats={"stale"},
    )

    sync_voice_mode_state_to_adapter(
        adapter,
        {
            "telegram:off-chat": "off",
            "telegram:on-chat": "voice_only",
            "telegram:tts-chat": "all",
            "slack:on-chat": "voice_only",
        },
        load_config=lambda: {"voice": {"auto_tts": True}},
    )

    assert adapter._auto_tts_default is True
    assert adapter._auto_tts_disabled_chats == {"off-chat"}
    assert adapter._auto_tts_enabled_chats == {"on-chat", "tts-chat"}
