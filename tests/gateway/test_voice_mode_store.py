"""Tests for VoiceModeStore — voice-mode map persistence + validation.

Pure: a temp JSON path, no gateway, no adapters.
"""

import json
from pathlib import Path

from gateway.voice_mode_store import VoiceModeStore


def test_voice_key_is_platform_namespaced():
    assert VoiceModeStore.voice_key("telegram", "123") == "telegram:123"


def test_missing_file_loads_empty(tmp_path):
    store = VoiceModeStore(tmp_path / "vm.json")
    assert store.modes == {}


def test_bad_json_loads_empty(tmp_path):
    p = tmp_path / "vm.json"
    p.write_text("{not json")
    assert VoiceModeStore(p).modes == {}


def test_load_keeps_valid_prefixed_modes(tmp_path):
    p = tmp_path / "vm.json"
    p.write_text(json.dumps({"telegram:1": "all", "discord:2": "voice_only"}))
    assert VoiceModeStore(p).modes == {"telegram:1": "all", "discord:2": "voice_only"}


def test_load_skips_invalid_mode(tmp_path):
    p = tmp_path / "vm.json"
    p.write_text(json.dumps({"telegram:1": "all", "telegram:2": "bogus"}))
    assert VoiceModeStore(p).modes == {"telegram:1": "all"}


def test_load_skips_legacy_unprefixed_key(tmp_path):
    p = tmp_path / "vm.json"
    p.write_text(json.dumps({"123": "all", "telegram:9": "off"}))
    assert VoiceModeStore(p).modes == {"telegram:9": "off"}


def test_set_persists_and_reloads(tmp_path):
    p = tmp_path / "vm.json"
    store = VoiceModeStore(p)
    store.set("telegram:7", "voice_only")
    assert store.get("telegram:7") == "voice_only"
    # a fresh store reads the same persisted value
    assert VoiceModeStore(p).get("telegram:7") == "voice_only"


def test_get_defaults_to_off(tmp_path):
    store = VoiceModeStore(tmp_path / "vm.json")
    assert store.get("telegram:missing") == "off"


def test_live_modes_is_the_mutable_backing_dict(tmp_path):
    # GatewayRunner mutates this dict in place then calls save(); the mutation
    # must be visible through the store and persist.
    p = tmp_path / "vm.json"
    store = VoiceModeStore(p)
    live = store.live_modes()
    live["telegram:9"] = "all"
    store.save()
    assert store.get("telegram:9") == "all"
    assert VoiceModeStore(p).get("telegram:9") == "all"


def test_keys_for_modes_filters_by_prefix_and_strips_it(tmp_path):
    p = tmp_path / "vm.json"
    p.write_text(json.dumps({
        "telegram:a": "all",
        "telegram:b": "off",
        "discord:c": "voice_only",
    }))
    store = VoiceModeStore(p)
    assert sorted(store.keys_for_modes({"voice_only", "all"}, "telegram:")) == ["a"]
    assert store.keys_for_modes({"off"}, "telegram:") == ["b"]
    assert sorted(store.keys_for_modes({"voice_only", "all"}, "discord:")) == ["c"]
