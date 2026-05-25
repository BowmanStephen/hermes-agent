"""Tests for extracted Telegram topic helper behavior."""

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.telegram_topic_manager import (
    TELEGRAM_CAPABILITY_HINT_COOLDOWN_S,
    TELEGRAM_LOBBY_REMINDER_COOLDOWN_S,
    disable_telegram_topic_mode_for_chat,
    is_telegram_topic_lane,
    is_telegram_topic_mode_enabled,
    is_telegram_topic_root_lobby,
    recover_telegram_topic_thread_id,
    record_telegram_topic_binding,
    sanitize_telegram_topic_title,
    should_send_telegram_topic_notice,
    telegram_topic_auto_rename_disabled,
    telegram_topic_help_text,
    telegram_topic_new_header,
    telegram_topic_root_lobby_message,
    telegram_topic_root_new_message,
    telegram_topic_root_status_message,
)


def _source(*, thread_id: str | None = None, chat_type: str = "dm") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type=chat_type,
        user_id="user-1",
        thread_id=thread_id,
    )


class _SessionDB:
    def __init__(self, value):
        self.value = value

    def is_telegram_topic_mode_enabled(self, *, chat_id, user_id):
        return self.value


def test_topic_mode_enabled_only_honors_real_true_for_telegram_dms():
    assert is_telegram_topic_mode_enabled(_source(), _SessionDB(True)) is True
    assert is_telegram_topic_mode_enabled(_source(), _SessionDB("true")) is False
    assert is_telegram_topic_mode_enabled(_source(chat_type="group"), _SessionDB(True)) is False
    assert is_telegram_topic_mode_enabled(_source(), None) is False


def test_topic_mode_helpers_classify_root_and_lane_threads():
    assert is_telegram_topic_root_lobby(_source(), topic_mode_enabled=True) is True
    assert is_telegram_topic_root_lobby(_source(thread_id="1"), topic_mode_enabled=True) is True
    assert is_telegram_topic_lane(_source(thread_id="1"), topic_mode_enabled=True) is False
    assert is_telegram_topic_lane(_source(thread_id="42"), topic_mode_enabled=True) is True
    assert is_telegram_topic_lane(_source(thread_id="42"), topic_mode_enabled=False) is False


def test_topic_notice_cooldown_is_per_chat_and_updates_only_after_window():
    timestamps = {}
    now = iter([100.0, 101.0, 100.0 + TELEGRAM_LOBBY_REMINDER_COOLDOWN_S + 0.1])

    assert should_send_telegram_topic_notice(_source(), timestamps, now_fn=lambda: next(now)) is True
    assert should_send_telegram_topic_notice(_source(), timestamps, now_fn=lambda: next(now)) is False
    assert should_send_telegram_topic_notice(_source(), timestamps, now_fn=lambda: next(now)) is True


def test_topic_notice_supports_capability_hint_cooldown():
    timestamps = {}
    now = iter([100.0, 100.0 + TELEGRAM_CAPABILITY_HINT_COOLDOWN_S - 1])

    assert should_send_telegram_topic_notice(
        _source(),
        timestamps,
        cooldown_s=TELEGRAM_CAPABILITY_HINT_COOLDOWN_S,
        now_fn=lambda: next(now),
    ) is True
    assert should_send_telegram_topic_notice(
        _source(),
        timestamps,
        cooldown_s=TELEGRAM_CAPABILITY_HINT_COOLDOWN_S,
        now_fn=lambda: next(now),
    ) is False


def test_topic_messages_match_user_facing_guidance():
    assert "main chat is reserved for system commands" in telegram_topic_root_lobby_message()
    assert "Use /new inside" in telegram_topic_root_new_message()
    assert telegram_topic_new_header(_source(thread_id="42"), topic_mode_enabled=True)
    assert telegram_topic_new_header(_source(thread_id="1"), topic_mode_enabled=True) is None


def test_record_topic_binding_persists_source_and_session_entry():
    class SessionEntry:
        session_key = "key-1"
        session_id = "sess-1"

    class SessionDB:
        def __init__(self):
            self.calls = []

        def bind_telegram_topic(self, **kwargs):
            self.calls.append(kwargs)

    db = SessionDB()

    assert record_telegram_topic_binding(_source(thread_id="42"), SessionEntry(), db) is True
    assert db.calls == [
        {
            "chat_id": "chat-1",
            "thread_id": "42",
            "user_id": "user-1",
            "session_key": "key-1",
            "session_id": "sess-1",
        }
    ]


def test_recover_topic_thread_id_rewrites_unknown_or_lobby_to_latest_user_binding():
    class SessionDB:
        def list_telegram_topic_bindings_for_chat(self, *, chat_id):
            assert chat_id == "chat-1"
            return [
                {"thread_id": "222", "user_id": "user-1"},
                {"thread_id": "111", "user_id": "user-1"},
                {"thread_id": "333", "user_id": "other"},
            ]

    db = SessionDB()

    assert recover_telegram_topic_thread_id(
        _source(thread_id="9999"),
        db,
        topic_mode_enabled=True,
    ) == "222"
    assert recover_telegram_topic_thread_id(
        _source(thread_id=None),
        db,
        topic_mode_enabled=True,
    ) == "222"


def test_recover_topic_thread_id_keeps_known_thread_and_disabled_mode():
    class SessionDB:
        def list_telegram_topic_bindings_for_chat(self, *, chat_id):
            return [{"thread_id": "222", "user_id": "user-1"}]

    db = SessionDB()

    assert recover_telegram_topic_thread_id(
        _source(thread_id="222"),
        db,
        topic_mode_enabled=True,
    ) is None
    assert recover_telegram_topic_thread_id(
        _source(thread_id=None),
        db,
        topic_mode_enabled=False,
    ) is None


def test_sanitize_topic_title_collapses_whitespace_and_caps_length():
    assert sanitize_telegram_topic_title("  Build   Telegram Topic UX  ") == (
        "Build Telegram Topic UX"
    )
    assert sanitize_telegram_topic_title("") == "Hermes Chat"
    long_title = "x" * 130
    sanitized = sanitize_telegram_topic_title(long_title)
    assert len(sanitized) == 120
    assert sanitized.endswith("...")


def test_topic_auto_rename_disabled_reads_platform_extra():
    class PlatformConfig:
        extra = {"disable_topic_auto_rename": "yes"}

    class Config:
        platforms = {Platform.TELEGRAM: PlatformConfig()}

    assert telegram_topic_auto_rename_disabled(Config(), _source(thread_id="42")) is True
    PlatformConfig.extra["disable_topic_auto_rename"] = "off"
    assert telegram_topic_auto_rename_disabled(Config(), _source(thread_id="42")) is False
    PlatformConfig.extra["disable_topic_auto_rename"] = True
    assert telegram_topic_auto_rename_disabled(Config(), _source(thread_id="42")) is True


def test_topic_help_text_includes_restore_and_disable_usage():
    text = telegram_topic_help_text()

    assert "/topic off" in text
    assert "/topic <id>" in text
    assert "restore" in text


def test_topic_root_status_lists_unlinked_sessions():
    class SessionDB:
        def list_unlinked_telegram_sessions_for_user(self, *, chat_id, user_id, limit):
            assert chat_id == "chat-1"
            assert user_id == "user-1"
            assert limit == 10
            return [
                {
                    "id": "sess-1",
                    "title": "Existing thread",
                    "preview": "last reply",
                }
            ]

    text = telegram_topic_root_status_message(_source(), SessionDB())

    assert "Telegram multi-session topics are enabled." in text
    assert "Previous unlinked sessions:" in text
    assert "- Existing thread" in text
    assert "`sess-1`" in text
    assert "Example: Send /topic sess-1 inside a topic." in text


def test_topic_root_status_handles_no_unlinked_sessions():
    class SessionDB:
        def list_unlinked_telegram_sessions_for_user(self, *, chat_id, user_id, limit):
            return []

    text = telegram_topic_root_status_message(_source(), SessionDB())

    assert "No previous unlinked Telegram sessions found." in text


def test_disable_topic_mode_turns_off_chat_and_clears_cooldowns():
    class SessionDB:
        def __init__(self):
            self.disabled_chat_id = None

        def is_telegram_topic_mode_enabled(self, *, chat_id, user_id):
            return True

        def disable_telegram_topic_mode(self, *, chat_id):
            self.disabled_chat_id = chat_id

    db = SessionDB()
    lobby_ts = {"chat-1": 10.0, "other": 1.0}
    hint_ts = {"chat-1": 20.0}

    result = disable_telegram_topic_mode_for_chat(
        _source(),
        db,
        lobby_reminder_ts=lobby_ts,
        capability_hint_ts=hint_ts,
        unavailable_message="unavailable",
    )

    assert "topic mode is now OFF" in result
    assert db.disabled_chat_id == "chat-1"
    assert lobby_ts == {"other": 1.0}
    assert hint_ts == {}


def test_disable_topic_mode_handles_unavailable_and_never_enabled():
    assert disable_telegram_topic_mode_for_chat(
        _source(),
        None,
        unavailable_message="unavailable",
    ) == "unavailable"

    class SessionDB:
        def is_telegram_topic_mode_enabled(self, *, chat_id, user_id):
            return False

    assert "not currently enabled" in disable_telegram_topic_mode_for_chat(
        _source(),
        SessionDB(),
        unavailable_message="unavailable",
    )
