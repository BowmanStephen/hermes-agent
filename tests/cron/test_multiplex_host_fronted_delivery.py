"""Cron delivery for multiplex-host-fronted platforms.

Bug report: under ``gateway.multiplex_profiles`` + ``profile_routes``, a
routed profile (e.g. a Discord bot thread's profile) deliberately keeps
``platforms.discord.enabled: false`` in its own config — the HOST gateway's
adapter fronts the platform and routes the profile's thread to it. The
multiplex cron ticker runs each profile's jobs under that profile's home and
passes the host's LIVE adapters in, but ``_deliver_result``'s native
``pconfig.enabled`` gate read only the profile's config and rejected the
platform ("not configured/enabled") — so every routed profile's cron job with
a platform origin/target silently failed delivery. This mirrors the relay
carve-out: a host-fronted logical platform is deliberately NOT natively
enabled in the profile's own config.
"""

import asyncio
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

from cron.scheduler import _deliver_result
from gateway.config import Platform, PlatformConfig


class TestMultiplexHostFrontedDeliveryGate:
    def _host_adapter(self):
        # A plain live native adapter (no relay fronting involved).
        adapter = AsyncMock()
        adapter.supports_inchannel_continuable = False
        return adapter

    def _job(self):
        return {
            "id": "bot-routine",
            "name": "[bot:bookie] watch",
            "deliver": "origin",
            "origin": {"platform": "discord", "chat_id": "123",
                       "thread_id": "456"},
        }

    def _profile_config(self, discord_enabled=False):
        """A routed profile's config: discord present but disabled."""
        config = MagicMock()
        config.platforms = {
            Platform.DISCORD: PlatformConfig(enabled=discord_enabled),
        }
        config.get_home_channel = lambda p: None
        return config

    def _run(self, adapters, gateway_config):
        loop = MagicMock()
        loop.is_running.return_value = True

        def fake_run_coro(coro, _loop):
            future = Future()
            try:
                future.set_result(asyncio.run(coro))
            except BaseException as e:  # noqa: BLE001
                future.set_exception(e)
            return future

        router = MagicMock()

        async def _deliver_to_platform(target, content, metadata):
            return {"success": True, "raw_response": None}

        router._deliver_to_platform = _deliver_to_platform

        with patch("gateway.config.load_gateway_config",
                   return_value=gateway_config), \
             patch("cron.scheduler.load_config",
                   return_value={"cron": {"wrap_response": False}}), \
             patch("gateway.delivery.DeliveryRouter", return_value=router), \
             patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro):
            return _deliver_result(self._job(), "Routine output.",
                                   adapters=adapters, loop=loop)

    def test_host_fronted_platform_is_not_rejected(self):
        """A live host adapter must deliver a routed profile's job even though
        the profile's own config disables the platform."""
        result = self._run({Platform.DISCORD: self._host_adapter()},
                           self._profile_config(discord_enabled=False))
        assert result is None  # None == delivered without errors

    def test_gate_preserved_without_live_adapter(self):
        """No live adapter for the platform → the configured/enabled gate
        stays: a standalone profile run must still fail closed."""
        result = self._run({}, self._profile_config(discord_enabled=False))
        assert result is not None
        assert "not configured/enabled" in result
