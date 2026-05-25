from types import SimpleNamespace

from gateway.config import Platform
from gateway.message_authorization import resolve_source_authorization
from gateway.session import SessionSource


def _pairing_store(approved: bool = False):
    return SimpleNamespace(is_approved=lambda *_args, **_kwargs: approved)


def test_chat_allowlist_authorizes_anonymous_telegram_group(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "-1001878443972")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id=None,
        chat_id="-1001878443972",
        chat_type="group",
    )

    decision = resolve_source_authorization(
        source,
        pairing_store=_pairing_store(),
    )

    assert decision.authorized is True
    assert decision.warned_telegram_group_users_legacy is False


def test_legacy_telegram_group_user_chat_ids_are_warned_once(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "-1001878443972")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="999",
        chat_id="-1001878443972",
        chat_type="forum",
    )

    decision = resolve_source_authorization(
        source,
        pairing_store=_pairing_store(),
        warned_telegram_group_users_legacy=False,
    )

    assert decision.authorized is True
    assert decision.warned_telegram_group_users_legacy is True
