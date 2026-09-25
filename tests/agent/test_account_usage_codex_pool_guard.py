"""Tier-3 codex usage credentials must stay quiet on an entry-less pool.

The quota poller calls fetch_account_usage("openai-codex") every cycle; with
no Codex login, _resolve_codex_usage_credentials fell through to an unguarded
load_pool("openai-codex").select(), whose "no available entries" INFO line
fired once per poll — each poll builds a fresh pool, so the pool's own
throttle never engages. Guarding with has_credentials() keeps the exact
failure contract (RuntimeError → the outer guard fails open) without the
per-cycle noise.
"""

from __future__ import annotations

import logging

import pytest

from agent import account_usage
from hermes_cli.auth import AuthError


@pytest.fixture
def _no_codex_runtime(monkeypatch):
    """Tier 2 finds no Codex login: the native runtime resolver raises AuthError."""

    def _raise(*args, **kwargs):
        raise AuthError("no codex credentials")

    monkeypatch.setattr(account_usage, "resolve_codex_runtime_credentials", _raise)


def test_tier3_empty_pool_raises_without_pool_log(_no_codex_runtime, caplog):
    with caplog.at_level(logging.INFO, logger="agent.credential_pool"):
        with pytest.raises(RuntimeError, match="No available openai-codex"):
            account_usage._resolve_codex_usage_credentials(None, None)

    pool_logs = [r for r in caplog.records if "no available entries" in r.getMessage()]
    assert pool_logs == []
