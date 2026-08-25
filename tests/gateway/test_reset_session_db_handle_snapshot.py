from datetime import datetime, timedelta
from unittest.mock import patch

from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import _DB_UNPINNED, SessionEntry, SessionSource, SessionStore


class _FakeSessionDB:
    def __init__(self):
        self.promoted = []
        self.created = []

    def get_session(self, session_id):
        return None

    def promote_to_session_reset(self, session_id, reason):
        self.promoted.append((session_id, reason))

    def create_session(self, **kwargs):
        self.created.append(kwargs)

    def record_gateway_session_peer(self, **kwargs):
        return None


def test_reset_session_snapshots_db_handle_before_parent_check(tmp_path, monkeypatch):
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(
            sessions_dir=tmp_path / "sessions",
            config=GatewayConfig(),
        )
    store._loaded = True

    session_key = "agent:main:discord:group:g1:u1"
    predecessor_id = "missing-predecessor"
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="g1",
        chat_type="group",
        user_id="u1",
    )
    store._entries[session_key] = SessionEntry(
        session_key=session_key,
        session_id=predecessor_id,
        created_at=datetime(2026, 8, 25),
        updated_at=datetime(2026, 8, 25),
        origin=source,
        display_name="group",
        platform=Platform.DISCORD,
        chat_type="group",
    )

    db = _FakeSessionDB()
    resolution_calls = []

    def resolve_db():
        resolution_calls.append(True)
        return None if len(resolution_calls) == 1 else db

    monkeypatch.setattr(store, "_save", lambda: None)
    monkeypatch.setattr(store, "_open_session_db_for_active_scope", resolve_db)
    store._db_pinned = _DB_UNPINNED

    store.reset_session(session_key)

    assert len(resolution_calls) == 1
    assert db.promoted == []
    assert db.created == []


def test_auto_reset_nulls_missing_parent_session(tmp_path, monkeypatch):
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="g1",
        chat_type="group",
        user_id="u1",
    )
    config = GatewayConfig(
        default_reset_policy=SessionResetPolicy(mode="idle", idle_minutes=1),
    )
    store = SessionStore(
        sessions_dir=tmp_path / "sessions",
        config=config,
        has_active_processes_fn=lambda _session_key: False,
    )
    session_key = store._generate_session_key(source)
    store._entries[session_key] = SessionEntry(
        session_key=session_key,
        session_id="missing-predecessor",
        created_at=datetime.now() - timedelta(hours=2),
        updated_at=datetime.now() - timedelta(hours=2),
        origin=source,
        platform=Platform.DISCORD,
        chat_type="group",
    )

    db = _FakeSessionDB()
    store._db = db
    monkeypatch.setattr(store, "_save_entries", lambda: None)

    store.get_or_create_session(source)

    assert len(db.created) == 1
    assert db.created[0]["parent_session_id"] is None
    assert db.created[0]["model_config"] is None
