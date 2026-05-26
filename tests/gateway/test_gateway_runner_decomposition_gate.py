"""Structural gate for GatewayRunner decomposition."""

from pathlib import Path


def test_gateway_runner_is_below_next_slice_line_gate():
    run_py = Path("gateway/run.py")

    assert len(run_py.read_text(encoding="utf-8").splitlines()) < 4_800


def test_model_command_services_are_under_object_gate():
    command_modules = [
        Path("gateway/command_handlers/model_modes.py"),
        Path("gateway/command_handlers/model_switch.py"),
        Path("gateway/command_handlers/runtime_modes.py"),
        Path("gateway/command_handlers/display_modes.py"),
    ]

    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in command_modules
        if path.exists() and len(path.read_text(encoding="utf-8").splitlines()) >= 400
    }

    assert oversized == {}


def test_command_handler_services_are_under_object_gate():
    command_modules = [
        path
        for path in Path("gateway/command_handlers").glob("*.py")
        if path.name != "_imports.py" and not path.name.startswith("__")
    ]

    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in command_modules
        if len(path.read_text(encoding="utf-8").splitlines()) >= 400
    }

    assert oversized == {}
