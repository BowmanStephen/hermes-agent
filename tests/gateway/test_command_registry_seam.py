"""Tests for the gateway command registry seam."""

from gateway.command_registry import (
    GATEWAY_HANDLER_METHODS,
    get_gateway_command_handler,
    resolve_special_cold_command,
)


def test_gateway_handler_registry_covers_cold_path_commands():
    expected = {
        "topic",
        "help",
        "commands",
        "profile",
        "whoami",
        "status",
        "agents",
        "platform",
        "restart",
        "stop",
        "reasoning",
        "fast",
        "verbose",
        "footer",
        "yolo",
        "model",
        "codex-runtime",
        "personality",
        "kanban",
        "retry",
        "sethome",
        "compress",
        "usage",
        "insights",
        "reload-mcp",
        "reload-skills",
        "bundles",
        "approve",
        "deny",
        "update",
        "debug",
        "title",
        "resume",
        "branch",
        "rollback",
        "background",
        "goal",
        "subgoal",
        "voice",
    }

    assert set(GATEWAY_HANDLER_METHODS) == expected


def test_get_gateway_command_handler_returns_bound_callable():
    def _handler(event):
        return f"handled:{event}"

    handlers = {"status": _handler}

    assert get_gateway_command_handler(handlers, "status")("event") == "handled:event"


def test_get_gateway_command_handler_ignores_missing_unknown_and_noncallable():
    handlers = {"status": "not callable"}

    assert get_gateway_command_handler(handlers, None) is None
    assert get_gateway_command_handler(handlers, "unknown") is None
    assert get_gateway_command_handler(handlers, "status") is None


def test_resolve_special_cold_command_handles_new_undo_and_steer():
    root_new = resolve_special_cold_command(
        "new",
        command_args="",
        telegram_root_lobby=True,
        telegram_root_new_message="root topic guidance",
    )
    assert root_new.response == "root topic guidance"

    new = resolve_special_cold_command("new", command_args="", telegram_root_lobby=False)
    assert new.confirm_command == "new"
    assert new.confirm_title == "/new"
    assert "fresh session" in new.confirm_detail

    undo = resolve_special_cold_command("undo", command_args="", telegram_root_lobby=False)
    assert undo.confirm_command == "undo"
    assert undo.confirm_title == "/undo"
    assert "last user/assistant exchange" in undo.confirm_detail

    steer = resolve_special_cold_command(
        "steer",
        command_args="also inspect logs",
        telegram_root_lobby=False,
    )
    assert steer.rewrite_text == "also inspect logs"

    steer_usage = resolve_special_cold_command("steer", command_args="", telegram_root_lobby=False)
    assert "Usage: /steer <prompt>" in steer_usage.response

    assert resolve_special_cold_command("status", command_args="", telegram_root_lobby=False) is None
