"""A reset must not manufacture a dangling parent lineage.

The JSON routing index can outlive its SQLite predecessor row (partial state
restore, legacy DB repair, FK races on reset). ``SessionStore`` nulls
``parent_session_id`` / ``model_config`` when the predecessor row is absent so
the INSERT cannot fail on a self-referential FK and the new row stays usable
as a root session. Exercised directly: upstream re-resolves the DB handle
several times per transition on purpose, so the earlier "snapshot the handle
once" assertion no longer describes the contract.
"""
from unittest.mock import patch

from gateway.config import GatewayConfig
from gateway.session import SessionStore

_KEY = "agent:main:discord:group:g1:u1"


class _FakeSessionDB:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or {}
        self.fail = fail

    def get_session(self, session_id):
        if self.fail:
            raise RuntimeError("db unavailable")
        return self.rows.get(session_id)


def _store(tmp_path, db):
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    store._loaded = True
    store._db = db
    return store


def test_missing_parent_lineage_is_nulled(tmp_path):
    store = _store(tmp_path, _FakeSessionDB())
    kwargs = {"parent_session_id": "missing-predecessor", "model_config": {"model": "m"}, "title": "t"}
    store._drop_missing_parent_lineage(_KEY, kwargs)
    assert kwargs["parent_session_id"] is None
    assert kwargs["model_config"] is None
    assert kwargs["title"] == "t"


def test_present_parent_lineage_is_kept(tmp_path):
    store = _store(tmp_path, _FakeSessionDB(rows={"pred": object()}))
    kwargs = {"parent_session_id": "pred", "model_config": {"model": "m"}}
    store._drop_missing_parent_lineage(_KEY, kwargs)
    assert kwargs["parent_session_id"] == "pred"
    assert kwargs["model_config"] == {"model": "m"}


def test_unreadable_db_is_treated_as_missing(tmp_path):
    """A transient read failure must not turn a user-requested reset into a guaranteed FK failure."""
    store = _store(tmp_path, _FakeSessionDB(fail=True))
    kwargs = {"parent_session_id": "pred", "model_config": {"model": "m"}}
    store._drop_missing_parent_lineage(_KEY, kwargs)
    assert kwargs["parent_session_id"] is None
    assert kwargs["model_config"] is None


def test_no_parent_or_no_db_is_a_no_op(tmp_path):
    store = _store(tmp_path, None)
    kwargs = {"parent_session_id": "pred"}
    store._drop_missing_parent_lineage(_KEY, kwargs)
    assert kwargs["parent_session_id"] == "pred"
    store = _store(tmp_path, _FakeSessionDB())
    kwargs = {"parent_session_id": None, "model_config": {"model": "m"}}
    store._drop_missing_parent_lineage(_KEY, kwargs)
    assert kwargs["model_config"] == {"model": "m"}
