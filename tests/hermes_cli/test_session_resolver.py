"""Tests for SessionResolver — session-reference -> session-id resolution.

Driven by a fake in-memory store: no SessionDB, no ~/.hermes/sessions.
"""

import json

from hermes_cli.session_resolver import (
    SessionResolver,
    read_tui_active_session_file,
    session_matches_query,
)


class FakeSessionStore:
    """In-memory SessionStore for tests."""

    def __init__(self, *, by_id=None, by_title=None, tips=None, recent=None, raise_on_tip=False):
        self._by_id = by_id or {}          # id -> session dict
        self._by_title = by_title or {}    # title -> id
        self._tips = tips or {}            # id -> compression tip id
        self._recent = recent or {}        # source -> [session dicts]
        self._raise_on_tip = raise_on_tip

    def get_session(self, session_id):
        return self._by_id.get(session_id)

    def resolve_session_by_title(self, title):
        return self._by_title.get(title)

    def get_compression_tip(self, session_id):
        if self._raise_on_tip:
            raise RuntimeError("tip lookup failed")
        return self._tips.get(session_id)

    def search_sessions(self, *, source, limit):
        return self._recent.get(source, [])[:limit]


# ── resolve_reference ─────────────────────────────────────────────────────────


def test_resolve_reference_by_exact_id():
    store = FakeSessionStore(by_id={"sess_abc": {"id": "sess_abc"}})
    assert SessionResolver(store).resolve_reference("sess_abc") == "sess_abc"


def test_resolve_reference_by_title():
    store = FakeSessionStore(by_title={"My Chat": "sess_xyz"})
    assert SessionResolver(store).resolve_reference("My Chat") == "sess_xyz"


def test_resolve_reference_projects_to_compression_tip():
    store = FakeSessionStore(by_id={"root": {"id": "root"}}, tips={"root": "tip_live"})
    assert SessionResolver(store).resolve_reference("root") == "tip_live"


def test_resolve_reference_keeps_id_when_tip_lookup_fails():
    store = FakeSessionStore(by_id={"root": {"id": "root"}}, raise_on_tip=True)
    assert SessionResolver(store).resolve_reference("root") == "root"


def test_resolve_reference_miss_returns_none():
    assert SessionResolver(FakeSessionStore()).resolve_reference("nope") is None


# ── resolve_last ──────────────────────────────────────────────────────────────


def test_resolve_last_returns_most_recent_for_source():
    store = FakeSessionStore(recent={"cli": [{"id": "sess_last"}]})
    assert SessionResolver(store).resolve_last(source="cli") == "sess_last"


def test_resolve_last_empty_returns_none():
    assert SessionResolver(FakeSessionStore()).resolve_last(source="tui") is None


# ── read_tui_active_session_file (pure) ───────────────────────────────────────


def test_read_tui_active_returns_session_id(tmp_path):
    p = tmp_path / "active.json"
    p.write_text(json.dumps({"session_id": "sess_tui"}))
    assert read_tui_active_session_file(str(p)) == "sess_tui"


def test_read_tui_active_none_path_returns_none():
    assert read_tui_active_session_file(None) is None


def test_read_tui_active_missing_or_bad_returns_none(tmp_path):
    assert read_tui_active_session_file(str(tmp_path / "absent.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_tui_active_session_file(str(bad)) is None


def test_read_tui_active_empty_session_id_returns_none(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"session_id": "  "}))
    assert read_tui_active_session_file(str(p)) is None


# ── session_matches_query (pure, from the picker) ─────────────────────────────


def test_matches_query_on_title_case_insensitive():
    assert session_matches_query({"title": "Daily Standup"}, "standup") is True


def test_matches_query_on_id_and_source():
    assert session_matches_query({"id": "sess_abc", "source": "tui"}, "abc") is True
    assert session_matches_query({"id": "x", "source": "telegram"}, "tele") is True


def test_matches_query_no_match():
    assert session_matches_query({"title": "Foo", "id": "y", "source": "cli"}, "zzz") is False
