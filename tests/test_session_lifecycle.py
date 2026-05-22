from __future__ import annotations


def test_resume_session_follows_compression_tip_and_reopens(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import resume_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("current", "cli")
        db.create_session("root", "cli")
        db.set_session_title("root", "Compressed Work")
        db.end_session("root", "compression")
        db.create_session("tip", "cli", parent_session_id="root")
        db.append_message("tip", "session_meta", "internal")
        db.append_message("tip", "user", "continue here")
        db.end_session("tip", "manual")

        result = resume_session(
            db,
            "root",
            current_session_id="current",
            end_current_reason="resumed_other",
        )

        assert result.session_id == "tip"
        assert result.requested_session_id == "root"
        assert result.resolved_from_session_id == "root"
        assert result.title == "Compressed Work"
        assert result.user_message_count == 1
        assert result.messages == [{"role": "user", "content": "continue here"}]
        assert db.get_session("current")["end_reason"] == "resumed_other"
        assert db.get_session("tip")["ended_at"] is None
        assert db.get_session("tip")["end_reason"] is None
    finally:
        db.close()


def test_resume_session_rejects_missing_resolved_descendant(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from session_lifecycle import SessionNotFound, resume_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("root", "cli")
        monkeypatch.setattr(db, "resolve_resume_session_id", lambda _session_id: "missing")

        try:
            resume_session(db, "root")
        except SessionNotFound as exc:
            assert str(exc) == "missing"
        else:
            raise AssertionError("expected missing resolved descendant to fail")
    finally:
        db.close()


def test_branch_session_copies_rich_transcript_and_marks_parent_branched(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import branch_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("parent", "telegram", model="test-model")
        db.set_session_title("parent", "Current Work")
        history = [
            {"role": "session_meta", "content": "internal"},
            {"role": "user", "content": "hello", "message_id": "platform-user-1"},
            {
                "role": "assistant",
                "content": "world",
                "finish_reason": "stop",
                "reasoning": "thinking",
                "reasoning_content": "provider scratchpad",
                "reasoning_details": [{"type": "summary", "text": "step"}],
                "codex_reasoning_items": [{"id": "r1", "type": "reasoning"}],
                "codex_message_items": [{"id": "m1", "type": "message"}],
                "message_id": "platform-assistant-1",
            },
        ]

        result = branch_session(
            db,
            parent_session_id="parent",
            history=history,
            branch_title="Experiment",
            source="telegram",
            model="test-model",
            new_session_id="branch",
        )

        assert result.session_id == "branch"
        assert result.parent_session_id == "parent"
        assert result.title == "Experiment"
        assert result.user_message_count == 1
        assert db.get_session("parent")["end_reason"] == "branched"
        branch = db.get_session("branch")
        assert branch["parent_session_id"] == "parent"
        assert db.get_session_title("branch") == "Experiment"
        copied = db.get_messages_as_conversation("branch")
        assert copied == history[1:]
    finally:
        db.close()


def test_branch_session_rejects_missing_parent(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import SessionNotFound, branch_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        try:
            branch_session(
                db,
                parent_session_id="missing",
                history=[{"role": "user", "content": "hello"}],
                source="cli",
                new_session_id="branch",
            )
        except SessionNotFound as exc:
            assert str(exc) == "missing"
        else:
            raise AssertionError("expected missing parent to fail")

        assert db.get_session("branch") is None
    finally:
        db.close()


def test_branch_session_rejects_existing_child_id(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import SessionAlreadyExists, branch_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("parent", "cli")
        db.create_session("branch", "cli")

        try:
            branch_session(
                db,
                parent_session_id="parent",
                history=[{"role": "user", "content": "hello"}],
                source="cli",
                new_session_id="branch",
            )
        except SessionAlreadyExists as exc:
            assert str(exc) == "branch"
        else:
            raise AssertionError("expected existing child id to fail")

        assert db.get_session("parent")["end_reason"] is None
        assert db.get_messages_as_conversation("branch") == []
    finally:
        db.close()


def test_split_session_for_compression_preserves_lineage_title_and_prompt(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import split_session_for_compression

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("old", "cli", model="test-model")
        db.set_session_title("old", "Planning")

        result = split_session_for_compression(
            db,
            old_session_id="old",
            source="cli",
            model="test-model",
            model_config={"max_iterations": 90},
            system_prompt="new prompt",
            new_session_id="new",
        )

        assert result.session_id == "new"
        assert result.parent_session_id == "old"
        assert result.title == "Planning #2"
        assert db.get_session("old")["end_reason"] == "compression"
        new_row = db.get_session("new")
        assert new_row["parent_session_id"] == "old"
        assert new_row["system_prompt"] == "new prompt"
        assert db.get_session_title("new") == "Planning #2"
    finally:
        db.close()


def test_split_session_for_compression_rejects_existing_child_id(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import SessionAlreadyExists, split_session_for_compression

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("old", "cli", model="test-model")
        db.create_session("new", "cli", model="test-model")

        try:
            split_session_for_compression(
                db,
                old_session_id="old",
                source="cli",
                model="test-model",
                model_config={},
                system_prompt="new prompt",
                new_session_id="new",
            )
        except SessionAlreadyExists as exc:
            assert str(exc) == "new"
        else:
            raise AssertionError("expected existing child id to fail")

        assert db.get_session("old")["end_reason"] is None
        assert db.get_session("new")["parent_session_id"] is None
    finally:
        db.close()


def test_split_session_for_compression_tolerates_title_failure(tmp_path, monkeypatch):
    from hermes_state import SessionDB
    from session_lifecycle import split_session_for_compression

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("old", "cli", model="test-model")
        db.set_session_title("old", "Planning")
        monkeypatch.setattr(
            db,
            "get_next_title_in_lineage",
            lambda _title: (_ for _ in ()).throw(RuntimeError("title failed")),
        )

        result = split_session_for_compression(
            db,
            old_session_id="old",
            source="cli",
            model="test-model",
            model_config={"max_iterations": 90},
            system_prompt="new prompt",
            new_session_id="new",
        )

        assert result.session_id == "new"
        assert result.title is None
        assert db.get_session("old")["end_reason"] == "compression"
        new_row = db.get_session("new")
        assert new_row["parent_session_id"] == "old"
        assert new_row["system_prompt"] == "new prompt"
        assert db.get_session_title("new") is None
    finally:
        db.close()


def test_activate_session_reopens_target_and_ends_previous(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import activate_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("current", "gateway")
        db.create_session("target", "telegram")
        db.end_session("target", "user_exit")

        result = activate_session(
            db,
            "target",
            current_session_id="current",
            end_current_reason="session_switch",
        )

        assert result.session_id == "target"
        assert result.previous_session_id == "current"
        assert db.get_session("current")["end_reason"] == "session_switch"
        target = db.get_session("target")
        assert target["ended_at"] is None
        assert target["end_reason"] is None
    finally:
        db.close()


def test_activate_session_rejects_missing_target(tmp_path):
    from hermes_state import SessionDB
    from session_lifecycle import SessionNotFound, activate_session

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("current", "gateway")

        try:
            activate_session(
                db,
                "missing",
                current_session_id="current",
                end_current_reason="session_switch",
            )
        except SessionNotFound as exc:
            assert str(exc) == "missing"
        else:
            raise AssertionError("expected missing target to fail")

        assert db.get_session("current")["end_reason"] is None
    finally:
        db.close()
