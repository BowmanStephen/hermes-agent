"""Regression test: memory section in DEFAULT_CONFIG must not contain dead keys.

`memory.max_chars` was historically present in some user configs but
is never read by tools/memory_tool.py or agent/agent_init.py.  The real
limit is `memory.memory_char_limit` + `memory.user_char_limit`.

This test fails if the default config dict ever carries the dead key,
preventing drift.
"""
import pytest

from hermes_cli.config import DEFAULT_CONFIG


@pytest.mark.unit
def test_memory_defaults_no_dead_keys():
    """memory.max_chars is not a real setting — reject it from DEFAULT_CONFIG."""
    mem = DEFAULT_CONFIG.get("memory", {})
    assert "max_chars" not in mem, (
        "memory.max_chars is a dead key; remove it.  "
        "Use memory.memory_char_limit / memory.user_char_limit instead."
    )
