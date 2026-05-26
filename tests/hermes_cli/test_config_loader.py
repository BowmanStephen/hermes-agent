"""Tests for the CLI config-loading deep module.

The pure deciders — config-path resolution and the YAML merge / deep-merge /
legacy-format normalization — are extracted from ``cli.py`` so they can be
tested with in-memory dicts and a tmp ``HERMES_HOME``, without booting the
prompt_toolkit TUI, loading a live ``~/.hermes``, or touching the network.

The autouse ``_isolate_hermes_home`` fixture (tests/conftest.py) already points
``HERMES_HOME`` at a per-test tempdir — these tests rely on it and never
hardcode ``~/.hermes``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from hermes_cli import config_loader
from hermes_cli.config_loader import (
    default_cli_config,
    load_cli_config,
    merge_file_config,
    resolve_config_path,
    save_config_value,
)


# ── resolve_config_path (pure path-decision logic) ───────────────────────────


def test_resolve_prefers_user_config_when_present(tmp_path):
    user = tmp_path / "config.yaml"
    user.write_text("model: foo\n", encoding="utf-8")
    project = tmp_path / "proj" / "cli-config.yaml"
    chosen = resolve_config_path(
        user_config_path=user, project_config_path=project, ignore_user_config=False
    )
    assert chosen == user


def test_resolve_falls_back_to_project_when_no_user_config(tmp_path):
    user = tmp_path / "missing.yaml"
    project = tmp_path / "cli-config.yaml"
    chosen = resolve_config_path(
        user_config_path=user, project_config_path=project, ignore_user_config=False
    )
    assert chosen == project


def test_resolve_skips_user_config_when_ignored(tmp_path):
    user = tmp_path / "config.yaml"
    user.write_text("model: foo\n", encoding="utf-8")
    project = tmp_path / "cli-config.yaml"
    chosen = resolve_config_path(
        user_config_path=user, project_config_path=project, ignore_user_config=True
    )
    assert chosen == project


# ── merge_file_config (pure deep-merge / legacy-format logic) ────────────────


def test_merge_string_model_becomes_default():
    defaults = default_cli_config()
    merged = merge_file_config(defaults, {"model": "anthropic/claude"})
    assert merged["model"]["default"] == "anthropic/claude"


def test_merge_dict_model_promotes_model_key_to_default():
    # Old format: model is a dict with "model" but no "default" → promote it.
    defaults = default_cli_config()
    merged = merge_file_config(defaults, {"model": {"model": "x/y", "base_url": "http://z"}})
    assert merged["model"]["default"] == "x/y"
    assert merged["model"]["base_url"] == "http://z"


def test_merge_root_level_provider_used_only_as_fallback():
    # Root-level provider is a fallback ONLY when model.provider is empty; the
    # built-in default is "auto" (truthy), so a root provider is ignored there.
    defaults = default_cli_config()
    merged = merge_file_config(defaults, {"provider": "openrouter"})
    assert merged["model"]["provider"] == "auto"

    # With model.provider explicitly cleared, the root-level fallback fires.
    defaults2 = default_cli_config()
    defaults2["model"]["provider"] = ""
    merged2 = merge_file_config(defaults2, {"provider": "openrouter"})
    assert merged2["model"]["provider"] == "openrouter"


def test_merge_deep_merges_known_dict_sections():
    defaults = default_cli_config()
    merged = merge_file_config(defaults, {"display": {"compact": True}})
    assert merged["display"]["compact"] is True
    # Untouched default keys survive the deep-merge.
    assert merged["display"]["skin"] == "default"


def test_merge_carries_over_unknown_top_level_keys():
    defaults = default_cli_config()
    merged = merge_file_config(defaults, {"honcho": {"enabled": True}})
    assert merged["honcho"] == {"enabled": True}


def test_merge_legacy_root_max_turns_copied_to_agent():
    defaults = default_cli_config()
    merged = merge_file_config(defaults, {"max_turns": 7})
    assert merged["agent"]["max_turns"] == 7


def test_merge_reports_file_terminal_section():
    defaults = default_cli_config()
    _, has_terminal = merge_file_config(
        defaults, {"terminal": {"env_type": "docker"}}, return_terminal_flag=True
    )
    assert has_terminal is True

    defaults2 = default_cli_config()
    _, no_terminal = merge_file_config(defaults2, {"model": "x"}, return_terminal_flag=True)
    assert no_terminal is False


# ── load_cli_config (end-to-end against a tmp HERMES_HOME) ────────────────────


def test_load_cli_config_reads_user_config(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model: anthropic/claude-sonnet\ndisplay:\n  skin: mono\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    cfg = load_cli_config(hermes_home=home)
    assert cfg["model"]["default"] == "anthropic/claude-sonnet"
    assert cfg["display"]["skin"] == "mono"
    # Defaults preserved where the user config is silent.
    assert cfg["agent"]["max_turns"] == 90


def test_default_cli_config_is_pure_defaults():
    # The pure defaults tree, independent of any on-disk project/user config.
    cfg = default_cli_config()
    assert cfg["model"]["default"] == ""
    assert cfg["display"]["skin"] == "default"
    assert cfg["agent"]["max_turns"] == 90


def test_default_cli_config_returns_fresh_copy():
    a = default_cli_config()
    a["display"]["skin"] = "mutated"
    b = default_cli_config()
    # Mutating one copy must not leak into the shared template.
    assert b["display"]["skin"] == "default"


def test_load_cli_config_falls_back_to_project_config_when_no_user_file(tmp_path):
    # With no user config in HERMES_HOME, load falls back to the repo-shipped
    # project cli-config.yaml (lookup order, mirrors the original cli.py).
    home = tmp_path / "empty-home"
    home.mkdir()
    cfg = load_cli_config(hermes_home=home)
    # Default keys the project config doesn't override survive.
    assert cfg["agent"]["max_turns"] == 90
    assert isinstance(cfg.get("model", {}).get("default", ""), str)


def test_load_cli_config_honors_ignore_user_config(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: should/not/load\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_IGNORE_USER_CONFIG", "1")

    cfg = load_cli_config(hermes_home=home)
    # User config skipped → falls back to defaults (project cli-config has no model).
    assert cfg["model"]["default"] != "should/not/load"


# ── save_config_value (round-trip against a tmp HERMES_HOME) ──────────────────


def test_save_config_value_round_trip(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text("display:\n  skin: default\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    ok = save_config_value("display.skin", "mono", hermes_home=home)
    assert ok is True

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["display"]["skin"] == "mono"


def test_save_config_value_creates_nested_key(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text("model: x\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    ok = save_config_value("agent.max_turns", 50, hermes_home=home)
    assert ok is True

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["agent"]["max_turns"] == 50


def test_save_config_value_returns_false_on_failure(monkeypatch, tmp_path):
    # A write error must be swallowed and reported as False, not raised.
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model: x\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("utils.atomic_roundtrip_yaml_update", _boom)

    ok = save_config_value("display.skin", "mono", hermes_home=home)
    assert ok is False


# ── security invariant: no unsafe yaml.load in the extracted module ───────────


def test_module_has_no_unsafe_yaml_load():
    source = Path(config_loader.__file__).read_text(encoding="utf-8")
    # Every PyYAML load must be safe_load. A bare `yaml.load(` is the unsafe API.
    unsafe = re.findall(r"\byaml\.load\s*\(", source)
    assert unsafe == [], f"unsafe yaml.load found in config_loader: {unsafe}"


def test_module_does_not_import_cli():
    source = Path(config_loader.__file__).read_text(encoding="utf-8")
    # One-way dependency: config_loader must never import the cli.py god-file.
    assert not re.search(r"^\s*(from cli import|import cli\b)", source, re.MULTILINE)
    # No prompt_toolkit import (a TUI dep) anywhere in the module body.
    assert not re.search(r"^\s*(from prompt_toolkit|import prompt_toolkit)", source, re.MULTILINE)
