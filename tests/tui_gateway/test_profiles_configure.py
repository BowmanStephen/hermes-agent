import yaml

from hermes_cli import profiles as profiles_cli
from tui_gateway import server


def test_profiles_configure_pins_cli_and_discord_toolsets(monkeypatch, tmp_path):
    profile_dir = tmp_path / "scoped-bot"
    profile_dir.mkdir()
    (profile_dir / "config.yaml").write_text(
        "platform_toolsets:\n"
        "  cli: [terminal]\n"
        "  discord: [terminal]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(profiles_cli, "get_profile_dir", lambda _name: profile_dir)

    response = server._methods["profiles.configure"](
        "request-1",
        {
            "name": "scoped-bot",
            "enabled_toolsets": ["web", "file"],
            "enabled_mcp_servers": [],
            "platform_toolsets": {
                "cli": ["web", "file"],
                "discord": ["web", "file"],
            },
        },
    )

    assert response["result"]["ok"] is True
    assert response["result"]["applied"] == {
        "toolsets": True,
        "mcp_servers": True,
        "platform_toolsets": True,
    }

    saved = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert saved["tools"]["enabled_toolsets"] == ["file", "web"]
    assert saved["platform_toolsets"] == {
        "cli": ["web", "file"],
        "discord": ["web", "file"],
    }


def test_profiles_describe_honors_mcp_enabled_flags(monkeypatch, tmp_path):
    profile_dir = tmp_path / "scoped-bot"
    profile_dir.mkdir()
    (profile_dir / "config.yaml").write_text(
        "mcp_servers:\n"
        "  enabled-server:\n"
        "    command: enabled\n"
        "    enabled: true\n"
        "  disabled-server:\n"
        "    command: disabled\n"
        "    enabled: false\n"
        "  legacy-disabled-server:\n"
        "    command: legacy\n"
        "    disabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(profiles_cli, "get_profile_dir", lambda _name: profile_dir)

    response = server._methods["profiles.describe"]("request-2", {"name": "scoped-bot"})

    assert "error" not in response, response.get("error")
    status = {item["name"]: item["enabled"] for item in response["result"]["mcp_servers"]}
    assert status == {
        "enabled-server": True,
        "disabled-server": False,
        "legacy-disabled-server": False,
    }


def test_profiles_configure_replaces_mcp_enabled_flags(monkeypatch, tmp_path):
    profile_dir = tmp_path / "scoped-bot"
    profile_dir.mkdir()
    (profile_dir / "config.yaml").write_text(
        "mcp_servers:\n"
        "  wanted-server:\n"
        "    command: wanted\n"
        "    enabled: false\n"
        "  unwanted-server:\n"
        "    command: unwanted\n"
        "    enabled: true\n"
        "    disabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(profiles_cli, "get_profile_dir", lambda _name: profile_dir)

    response = server._methods["profiles.configure"](
        "request-3",
        {"name": "scoped-bot", "enabled_mcp_servers": ["wanted-server"]},
    )

    assert response["result"]["ok"] is True
    saved = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert saved["mcp_servers"] == {
        "wanted-server": {"command": "wanted", "enabled": True},
        "unwanted-server": {"command": "unwanted", "enabled": False},
    }
