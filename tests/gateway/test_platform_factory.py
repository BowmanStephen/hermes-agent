from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from gateway.platform_factory import create_platform_adapter
from gateway.platform_registry import PlatformEntry, platform_registry


def _register_platform_entry(entry: PlatformEntry):
    previous = platform_registry.get(entry.name)
    platform_registry.register(entry)
    try:
        yield
    finally:
        platform_registry.unregister(entry.name)
        if previous is not None:
            platform_registry.register(previous)


def test_registered_adapter_takes_precedence_and_gets_gateway_context():
    adapter = SimpleNamespace(gateway_runner=None)
    entry = PlatformEntry(
        name=Platform.TELEGRAM.value,
        label="Telegram Override",
        adapter_factory=lambda config: adapter,
        check_fn=lambda: True,
        source="plugin",
    )
    platform_config = PlatformConfig(enabled=True, token="token")
    gateway_config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    gateway_runner = object()

    for _ in _register_platform_entry(entry):
        result = create_platform_adapter(
            Platform.TELEGRAM,
            platform_config,
            gateway_config=gateway_config,
            gateway_runner=gateway_runner,
        )

    assert result is adapter
    assert adapter.gateway_runner is gateway_runner
    assert platform_config.extra["group_sessions_per_user"] is True
    assert platform_config.extra["thread_sessions_per_user"] is True


def test_registered_adapter_failure_does_not_fall_through_to_builtin():
    entry = PlatformEntry(
        name=Platform.TELEGRAM.value,
        label="Telegram Override",
        adapter_factory=lambda config: None,
        check_fn=lambda: True,
        source="plugin",
    )

    for _ in _register_platform_entry(entry):
        result = create_platform_adapter(
            Platform.TELEGRAM,
            PlatformConfig(enabled=True, token="token"),
            gateway_config=SimpleNamespace(
                group_sessions_per_user=False,
                thread_sessions_per_user=False,
            ),
            gateway_runner=object(),
        )

    assert result is None
