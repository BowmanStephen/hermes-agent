"""Resolve a *session reference* to a *session id*.

Extracted from ``hermes_cli/main.py`` so the resolution logic is a real,
testable seam instead of private functions that ``cli.py``, ``commands/chat.py``
and the test suite reach *up* into. The session store (``~/.hermes/sessions``
via ``SessionDB``) is injected, so resolution can be tested against a fake
in-memory store with no filesystem.

- **session id** — canonical id of a stored conversation.
- **session reference** — an id, a human title, "last" (most recent per source),
  or an interactive pick.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Protocol


class SessionStore(Protocol):
    """Seam over session persistence. Real impl wraps ``SessionDB``."""

    def get_session(self, session_id: str) -> Optional[dict]: ...
    def resolve_session_by_title(self, title: str) -> Optional[str]: ...
    def get_compression_tip(self, session_id: str) -> Optional[str]: ...
    def search_sessions(self, *, source: str, limit: int) -> List[dict]: ...


class SessionResolver:
    """Turn a session reference into a concrete session id."""

    def __init__(self, store: SessionStore):
        self._store = store

    def resolve_reference(self, name_or_id: str) -> Optional[str]:
        """Resolve an id or a title to a session id.

        Tries an exact id first, then a title (auto-latest). When resolved,
        projects forward through the compression chain so a remembered root id
        lands on the live tip rather than a dead compressed parent. A tip-lookup
        failure keeps the already-resolved id.
        """
        session = self._store.get_session(name_or_id)
        resolved: Optional[str] = (
            session["id"] if session else self._store.resolve_session_by_title(name_or_id)
        )
        if resolved:
            try:
                resolved = self._store.get_compression_tip(resolved) or resolved
            except Exception:
                pass
        return resolved

    def resolve_last(self, source: str = "cli") -> Optional[str]:
        """Most recently-used session id for a source, or None."""
        sessions = self._store.search_sessions(source=source, limit=1)
        return sessions[0]["id"] if sessions else None


def read_tui_active_session_file(path: Optional[str]) -> Optional[str]:
    """Read the session id from a TUI active-session marker file."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        sid = str(data.get("session_id") or "").strip()
        return sid or None
    except Exception:
        return None


def session_matches_query(session: dict, query: str) -> bool:
    """Case-insensitive match of a session against an interactive-picker query
    over its title, preview, id, and source."""
    q = query.lower()
    return (
        q in (session.get("title") or "").lower()
        or q in (session.get("preview") or "").lower()
        or q in session.get("id", "").lower()
        or q in (session.get("source") or "").lower()
    )


class SessionDBStore:
    """Real :class:`SessionStore` backed by ``hermes_state.SessionDB``.

    Opens a ``SessionDB`` on construction; call :meth:`close` when done.
    """

    def __init__(self):
        from hermes_state import SessionDB

        self._db = SessionDB()

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._db.get_session(session_id)

    def resolve_session_by_title(self, title: str) -> Optional[str]:
        return self._db.resolve_session_by_title(title)

    def get_compression_tip(self, session_id: str) -> Optional[str]:
        return self._db.get_compression_tip(session_id)

    def search_sessions(self, *, source: str, limit: int) -> List[dict]:
        return self._db.search_sessions(source=source, limit=limit)

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
