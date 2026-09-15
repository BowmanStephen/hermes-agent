"""Discord cleanup-progress message deletion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.platforms.discord.adapter import DiscordAdapter
from gateway.config import Platform

pytestmark = pytest.mark.asyncio


async def test_delete_message_deletes_resolved_partial_message():
    adapter = object.__new__(DiscordAdapter)
    adapter._client = SimpleNamespace()
    message = SimpleNamespace(delete=AsyncMock())
    channel = SimpleNamespace(get_partial_message=MagicMock(return_value=message))
    adapter._resolve_channel = AsyncMock(return_value=channel)

    result = await adapter.delete_message("123", "456")

    assert result is True
    adapter._resolve_channel.assert_awaited_once_with("123")
    channel.get_partial_message.assert_called_once_with(456)
    message.delete.assert_awaited_once_with()


async def test_delete_message_returns_false_when_discord_rejects_delete():
    adapter = object.__new__(DiscordAdapter)
    adapter._client = SimpleNamespace()
    adapter.platform = Platform.DISCORD
    message = SimpleNamespace(delete=AsyncMock(side_effect=RuntimeError("missing access")))
    channel = SimpleNamespace(get_partial_message=MagicMock(return_value=message))
    adapter._resolve_channel = AsyncMock(return_value=channel)

    result = await adapter.delete_message("123", "456")

    assert result is False
    message.delete.assert_awaited_once_with()
