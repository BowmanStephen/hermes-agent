"""Validate live user config at ~/.hermes/config.yaml has no known dead keys.

Dead keys waste prompt context (the whole config YAML is loaded into
system tools metadata) and mislead users into thinking a setting
works when it doesn't.
"""
import os
from pathlib import Path
import pytest
import yaml


@pytest.fixture
def user_config():
    p = Path.home() / ".hermes" / "config.yaml"
    if not p.exists():
        pytest.skip("no user config.yaml found")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_user_config_memory_no_max_chars(user_config):
    """memory.max_chars is dead — reject it from user config."""
    mem = user_config.get("memory", {})
    assert "max_chars" not in mem, (
        "Remove `max_chars` from the `memory:` section.  "
        "The Hermes runtime only reads memory_char_limit + user_char_limit."
    )


@pytest.mark.unit
def test_user_config_no_smart_model_routing(user_config):
    """smart_model_routing is not in DEFAULT_CONFIG — reject it as dead."""
    assert "smart_model_routing" not in user_config, (
        "Remove `smart_model_routing:` from config.yaml.  "
        "It is not read by the Hermes runtime."
    )
