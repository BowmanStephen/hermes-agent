"""Fixtures shared across hermes_cli kanban tests."""

from __future__ import annotations

import sys

import pytest

# Modules that cache HERMES_HOME-derived paths at import time. A fixture that
# repoints HERMES_HOME must evict these so the fresh root is picked up.
_HOME_CACHING_PREFIXES = ("hermes_cli", "hermes_state")
_HOME_CACHING_MODULES = ("hermes_constants",)


def evict_home_caching_modules(monkeypatch) -> None:
    """Drop HERMES_HOME-caching modules from ``sys.modules``, restorably.

    Fixtures that repoint ``HERMES_HOME`` have to evict these so the next
    import re-resolves against the new root. Doing that with a bare
    ``del sys.modules[...]`` leaks: every module object imported at
    collection time by OTHER test files stays bound in those files, while
    ``sys.modules`` now holds nothing (or, after the next import, a
    DIFFERENT object). Autouse fixtures that probe ``sys.modules`` — notably
    the ``_kanban_write_guard`` in the root conftest — then silently no-op,
    and patches land on an object the test under inspection never sees.
    That was worth 16 failures in ``-k kanban`` that all passed in isolation.

    ``monkeypatch.delitem`` records each eviction and pytest restores the
    original module objects at teardown, so the leak can't outlive the test.

    Restoring ``sys.modules`` alone is NOT enough. Importing a submodule also
    binds it as an attribute on its parent package, and the re-import
    overwrites that attribute with the new module object. Restoring
    ``sys.modules['hermes_cli.main']`` therefore leaves
    ``hermes_cli.main`` — what ``from hermes_cli import main`` actually
    resolves — pointing at the throwaway module. ``update_cmd._m()`` uses
    exactly that form, so it kept handing later tests a module whose
    ``PROJECT_ROOT`` no test had patched. Snapshot the parent attribute too.
    """
    for name in list(sys.modules):
        if not (
            name.startswith(_HOME_CACHING_PREFIXES) or name in _HOME_CACHING_MODULES
        ):
            continue
        monkeypatch.delitem(sys.modules, name, raising=False)
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name) if parent_name else None
        if parent is not None and hasattr(parent, child):
            monkeypatch.setattr(parent, child, getattr(parent, child), raising=False)


@pytest.fixture
def all_assignees_spawnable(monkeypatch):
    """Pretend every assignee maps to a real Hermes profile.

    Most dispatcher tests use synthetic assignees ("alice", "bob") that
    don't correspond to actual profile directories on disk. Without this
    patch, the dispatcher's profile-exists guard (PR #20105) routes
    those tasks into ``skipped_nonspawnable`` instead of spawning, which
    would break tests that assert spawn behavior.
    """
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)


@pytest.fixture(autouse=True)
def _suppress_concurrent_hermes_gate(request, monkeypatch):
    """Default ``_detect_concurrent_hermes_instances`` to ``[]`` for every test.

    The Windows update path now refuses to proceed when another
    ``hermes.exe`` is detected (issue #26670). On a developer's Windows
    machine running the test suite via ``hermes`` itself, this would
    flag the running agent as a concurrent instance and abort every
    ``cmd_update`` test. Tests that want to exercise the gate explicitly
    re-patch ``_detect_concurrent_hermes_instances`` with their own
    return value — autouse here gives a clean default without touching
    the rest of the suite.

    Tests that need to call the REAL function (e.g. unit tests for the
    helper itself) opt out with ``@pytest.mark.real_concurrent_gate``.
    """
    if request.node.get_closest_marker("real_concurrent_gate"):
        return
    try:
        from hermes_cli import main as _cli_main
    except Exception:
        return
    # raising=False: under pytest's per-test spawn isolation, a concurrent
    # xdist worker importing a module that transitively touches hermes_cli.main
    # can briefly expose a partially-initialized module object here — one where
    # _detect_concurrent_hermes_instances isn't defined yet. A bare setattr
    # would raise AttributeError and error the (unrelated) test. The attribute
    # always exists once main.py finishes importing, so a no-op when it's
    # transiently absent is the correct, race-free default.
    monkeypatch.setattr(
        _cli_main,
        "_detect_concurrent_hermes_instances",
        lambda *_a, **_k: [],
        raising=False,
    )
