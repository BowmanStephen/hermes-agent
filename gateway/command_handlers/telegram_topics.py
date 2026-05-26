"""TelegramTopicGatewayCommands aggregate."""
from __future__ import annotations

from .telegram_topic_setup import TelegramTopicSetupGatewayCommands
from .telegram_topic_commands import TelegramTopicCommandGatewayCommands


class TelegramTopicGatewayCommands(
    TelegramTopicSetupGatewayCommands,
    TelegramTopicCommandGatewayCommands,
):
    """Backwards-compatible aggregate for split telegramtopicgatewaycommands aggregate."""
