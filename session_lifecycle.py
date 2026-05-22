"""Shared session lifecycle operations for CLI, gateway, and agents."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


class SessionLifecycleError(Exception):
    """Base error for session lifecycle failures."""


class SessionNotFound(SessionLifecycleError):
    """Raised when a requested session row does not exist."""


class SessionAlreadyExists(SessionLifecycleError):
    """Raised when a lifecycle operation would overwrite an existing session."""


@dataclass(frozen=True)
class ResumeSessionResult:
    requested_session_id: str
    session_id: str
    session: Dict[str, Any]
    messages: List[Dict[str, Any]]
    title: Optional[str]
    user_message_count: int
    resolved_from_session_id: Optional[str] = None


@dataclass(frozen=True)
class BranchSessionResult:
    session_id: str
    parent_session_id: str
    title: str
    messages: List[Dict[str, Any]]
    user_message_count: int


@dataclass(frozen=True)
class CompressionSplitResult:
    session_id: str
    parent_session_id: str
    title: Optional[str]


@dataclass(frozen=True)
class ActivateSessionResult:
    session_id: str
    previous_session_id: Optional[str]


def _new_session_id() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _without_session_meta(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in messages if m.get("role") != "session_meta"]


def _user_message_count(messages: Iterable[Dict[str, Any]]) -> int:
    return len([m for m in messages if m.get("role") == "user"])


def _append_transcript_message(db: Any, session_id: str, msg: Dict[str, Any]) -> None:
    db.append_message(
        session_id=session_id,
        role=msg.get("role", "user"),
        content=msg.get("content"),
        tool_name=msg.get("tool_name") or msg.get("name"),
        tool_calls=msg.get("tool_calls"),
        tool_call_id=msg.get("tool_call_id"),
        token_count=msg.get("token_count"),
        finish_reason=msg.get("finish_reason"),
        reasoning=msg.get("reasoning"),
        reasoning_content=msg.get("reasoning_content"),
        reasoning_details=msg.get("reasoning_details"),
        codex_reasoning_items=msg.get("codex_reasoning_items"),
        codex_message_items=msg.get("codex_message_items"),
        platform_message_id=msg.get("platform_message_id") or msg.get("message_id"),
    )


def _get_session_title(db: Any, session_id: str) -> Optional[str]:
    getter = getattr(db, "get_session_title", None)
    if getter is None:
        return None
    try:
        return getter(session_id)
    except Exception:
        return None


def resume_session(
    db: Any,
    requested_session_id: str,
    *,
    current_session_id: Optional[str] = None,
    end_current_reason: Optional[str] = None,
) -> ResumeSessionResult:
    """Resolve and reopen a resumable session, returning its transcript."""
    session = db.get_session(requested_session_id)
    if not session:
        raise SessionNotFound(requested_session_id)

    session_id = requested_session_id
    try:
        resolved_id = db.resolve_resume_session_id(requested_session_id)
    except Exception:
        resolved_id = requested_session_id

    resolved_from = None
    if isinstance(resolved_id, str) and resolved_id and resolved_id != requested_session_id:
        resolved_from = requested_session_id
        session_id = resolved_id
        resolved_session = db.get_session(session_id)
        if not resolved_session:
            raise SessionNotFound(session_id)
        session = resolved_session

    if current_session_id and current_session_id != session_id and end_current_reason:
        db.end_session(current_session_id, end_current_reason)

    messages = _without_session_meta(db.get_messages_as_conversation(session_id) or [])
    db.reopen_session(session_id)

    title = session.get("title") or _get_session_title(db, requested_session_id)
    return ResumeSessionResult(
        requested_session_id=requested_session_id,
        session_id=session_id,
        session=session,
        messages=messages,
        title=title,
        user_message_count=_user_message_count(messages),
        resolved_from_session_id=resolved_from,
    )


def activate_session(
    db: Any,
    session_id: str,
    *,
    current_session_id: Optional[str] = None,
    end_current_reason: Optional[str] = None,
) -> ActivateSessionResult:
    """Make an existing session active by reopening it and ending the previous one."""
    if not db.get_session(session_id):
        raise SessionNotFound(session_id)
    if current_session_id and current_session_id != session_id and end_current_reason:
        db.end_session(current_session_id, end_current_reason)
    db.reopen_session(session_id)
    return ActivateSessionResult(
        session_id=session_id,
        previous_session_id=current_session_id if current_session_id != session_id else None,
    )


def branch_session(
    db: Any,
    *,
    parent_session_id: str,
    history: Iterable[Dict[str, Any]],
    branch_title: Optional[str] = None,
    source: str,
    model: Optional[str] = None,
    model_config: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
    new_session_id: Optional[str] = None,
) -> BranchSessionResult:
    """Create a transcript-preserving branch and mark the parent branched."""
    if not db.get_session(parent_session_id):
        raise SessionNotFound(parent_session_id)
    messages = _without_session_meta(history)
    session_id = new_session_id or _new_session_id()
    if db.get_session(session_id):
        raise SessionAlreadyExists(session_id)

    if not branch_title:
        current_title = _get_session_title(db, parent_session_id)
        branch_title = db.get_next_title_in_lineage(current_title or "branch")

    db.create_session(
        session_id=session_id,
        source=source,
        model=model,
        model_config=model_config,
        system_prompt=system_prompt,
        user_id=user_id,
        parent_session_id=parent_session_id,
    )

    for msg in messages:
        _append_transcript_message(db, session_id, msg)

    db.set_session_title(session_id, branch_title)
    db.end_session(parent_session_id, "branched")
    return BranchSessionResult(
        session_id=session_id,
        parent_session_id=parent_session_id,
        title=branch_title,
        messages=messages,
        user_message_count=_user_message_count(messages),
    )


def split_session_for_compression(
    db: Any,
    *,
    old_session_id: str,
    source: str,
    model: Optional[str],
    model_config: Optional[Dict[str, Any]],
    system_prompt: Optional[str],
    new_session_id: Optional[str] = None,
) -> CompressionSplitResult:
    """End a session for compression and create its continuation child."""
    old_title = _get_session_title(db, old_session_id)
    session_id = new_session_id or _new_session_id()
    if db.get_session(session_id):
        raise SessionAlreadyExists(session_id)

    if not db.get_session(old_session_id):
        db.ensure_session(old_session_id, source=source, model=model)
    db.end_session(old_session_id, "compression")
    db.create_session(
        session_id=session_id,
        source=source,
        model=model,
        model_config=model_config,
        parent_session_id=old_session_id,
    )

    new_title = None
    if old_title:
        try:
            new_title = db.get_next_title_in_lineage(old_title)
            db.set_session_title(session_id, new_title)
        except Exception:
            new_title = None

    if system_prompt is not None:
        db.update_system_prompt(session_id, system_prompt)

    return CompressionSplitResult(
        session_id=session_id,
        parent_session_id=old_session_id,
        title=new_title,
    )
