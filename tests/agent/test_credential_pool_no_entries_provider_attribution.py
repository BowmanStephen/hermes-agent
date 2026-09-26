"""The credential-pool "no available entries" INFO line must name the provider.

select() on any provider's pool can emit this line, and periodic callers build
a fresh pool per request (quota poller, model detection), so an unattributed
line is impossible to triage from agent.log: an unused-provider probe is
indistinguishable from the serving pool actually starving.
"""

from __future__ import annotations

import logging

from agent.credential_pool import CredentialPool


def test_no_entries_log_names_the_provider(caplog):
    pool = CredentialPool("openai-codex", [])

    with caplog.at_level(logging.INFO, logger="agent.credential_pool"):
        assert pool.select() is None

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "credential pool[openai-codex]:" in m and "no available entries" in m
        for m in messages
    ), messages
