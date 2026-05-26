"""Platform adapter creation for the gateway runner."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter
from hermes_cli.config import cfg_get

logger = logging.getLogger(__name__)


def create_platform_adapter(
    platform: Platform,
    config: Any,
    *,
    gateway_config: Any,
    gateway_runner: Any = None,
    load_gateway_config: Optional[Callable[[], dict]] = None,
    logger_: Optional[logging.Logger] = None,
) -> Optional[BasePlatformAdapter]:
    """Create an adapter for a gateway platform.

    Plugin-registered adapters are resolved before built-ins so user plugins
    can provide new platforms or override bundled adapters.
    """
    active_logger = logger_ or logger

    if hasattr(config, "extra") and isinstance(config.extra, dict):
        config.extra.setdefault(
            "group_sessions_per_user",
            getattr(gateway_config, "group_sessions_per_user", False),
        )
        config.extra.setdefault(
            "thread_sessions_per_user",
            getattr(gateway_config, "thread_sessions_per_user", False),
        )

    try:
        from gateway.platform_registry import platform_registry

        if platform_registry.is_registered(platform.value):
            adapter = platform_registry.create_adapter(platform.value, config)
            if adapter is not None:
                if hasattr(adapter, "gateway_runner"):
                    adapter.gateway_runner = gateway_runner
                return adapter
            active_logger.error(
                "Platform '%s' is registered but adapter creation failed "
                "(check dependencies and config)",
                platform.value,
            )
            return None
    except Exception as e:
        active_logger.debug(
            "Platform registry lookup for '%s' failed: %s",
            platform.value,
            e,
        )

    if platform == Platform.TELEGRAM:
        from gateway.platforms.telegram import TelegramAdapter, check_telegram_requirements

        if not check_telegram_requirements():
            active_logger.warning("Telegram: python-telegram-bot not installed")
            return None
        adapter = TelegramAdapter(config)
        _notify_mode = os.getenv("HERMES_TELEGRAM_NOTIFICATIONS", "")
        if not _notify_mode and load_gateway_config is not None:
            try:
                _gw_cfg = load_gateway_config()
                _raw = cfg_get(
                    _gw_cfg,
                    "display",
                    "platforms",
                    "telegram",
                    "notifications",
                )
                if _raw not in {None, ""}:
                    _notify_mode = str(_raw).strip().lower()
            except Exception:
                pass
        _notify_mode = _notify_mode or "important"
        if _notify_mode not in {"all", "important"}:
            active_logger.warning(
                "Unknown telegram notifications mode '%s', "
                "defaulting to 'important' (valid: all, important)",
                _notify_mode,
            )
            _notify_mode = "important"
        adapter._notifications_mode = _notify_mode
        return adapter

    if platform == Platform.WHATSAPP:
        from gateway.platforms.whatsapp import WhatsAppAdapter, check_whatsapp_requirements

        if not check_whatsapp_requirements():
            active_logger.warning("WhatsApp: Node.js not installed or bridge not configured")
            return None
        return WhatsAppAdapter(config)

    if platform == Platform.SLACK:
        from gateway.platforms.slack import SlackAdapter, check_slack_requirements

        if not check_slack_requirements():
            active_logger.warning("Slack: slack-bolt not installed. Run: pip install 'hermes-agent[slack]'")
            return None
        return SlackAdapter(config)

    if platform == Platform.SIGNAL:
        from gateway.platforms.signal import SignalAdapter, check_signal_requirements

        if not check_signal_requirements():
            active_logger.warning("Signal: SIGNAL_HTTP_URL or SIGNAL_ACCOUNT not configured")
            return None
        return SignalAdapter(config)

    if platform == Platform.HOMEASSISTANT:
        from gateway.platforms.homeassistant import HomeAssistantAdapter, check_ha_requirements

        if not check_ha_requirements():
            active_logger.warning("HomeAssistant: aiohttp not installed or HASS_TOKEN not set")
            return None
        return HomeAssistantAdapter(config)

    if platform == Platform.EMAIL:
        from gateway.platforms.email import EmailAdapter, check_email_requirements

        if not check_email_requirements():
            active_logger.warning("Email: EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_IMAP_HOST, or EMAIL_SMTP_HOST not set")
            return None
        return EmailAdapter(config)

    if platform == Platform.SMS:
        from gateway.platforms.sms import SmsAdapter, check_sms_requirements

        if not check_sms_requirements():
            active_logger.warning("SMS: aiohttp not installed or TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set")
            return None
        return SmsAdapter(config)

    if platform == Platform.DINGTALK:
        from gateway.platforms.dingtalk import DingTalkAdapter, check_dingtalk_requirements

        if not check_dingtalk_requirements():
            active_logger.warning("DingTalk: dingtalk-stream not installed or DINGTALK_CLIENT_ID/SECRET not set")
            return None
        return DingTalkAdapter(config)

    if platform == Platform.FEISHU:
        from gateway.platforms.feishu import FeishuAdapter, check_feishu_requirements

        if not check_feishu_requirements():
            active_logger.warning("Feishu: lark-oapi not installed or FEISHU_APP_ID/SECRET not set")
            return None
        return FeishuAdapter(config)

    if platform == Platform.WECOM_CALLBACK:
        from gateway.platforms.wecom_callback import (
            WecomCallbackAdapter,
            check_wecom_callback_requirements,
        )

        if not check_wecom_callback_requirements():
            active_logger.warning("WeComCallback: aiohttp/httpx not installed")
            return None
        return WecomCallbackAdapter(config)

    if platform == Platform.WECOM:
        from gateway.platforms.wecom import WeComAdapter, check_wecom_requirements

        if not check_wecom_requirements():
            active_logger.warning("WeCom: aiohttp not installed or WECOM_BOT_ID/SECRET not set")
            return None
        return WeComAdapter(config)

    if platform == Platform.WEIXIN:
        from gateway.platforms.weixin import WeixinAdapter, check_weixin_requirements

        if not check_weixin_requirements():
            active_logger.warning("Weixin: aiohttp/cryptography not installed")
            return None
        return WeixinAdapter(config)

    if platform == Platform.MATTERMOST:
        from gateway.platforms.mattermost import MattermostAdapter, check_mattermost_requirements

        if not check_mattermost_requirements():
            active_logger.warning("Mattermost: MATTERMOST_TOKEN or MATTERMOST_URL not set, or aiohttp missing")
            return None
        return MattermostAdapter(config)

    if platform == Platform.MATRIX:
        from gateway.platforms.matrix import MatrixAdapter, check_matrix_requirements

        if not check_matrix_requirements():
            active_logger.warning("Matrix: mautrix not installed or credentials not set. Run: pip install 'mautrix[encryption]'")
            return None
        return MatrixAdapter(config)

    if platform == Platform.API_SERVER:
        from gateway.platforms.api_server import APIServerAdapter, check_api_server_requirements

        if not check_api_server_requirements():
            active_logger.warning("API Server: aiohttp not installed")
            return None
        return APIServerAdapter(config)

    if platform == Platform.WEBHOOK:
        from gateway.platforms.webhook import WebhookAdapter, check_webhook_requirements

        if not check_webhook_requirements():
            active_logger.warning("Webhook: aiohttp not installed")
            return None
        adapter = WebhookAdapter(config)
        adapter.gateway_runner = gateway_runner
        return adapter

    if platform == Platform.MSGRAPH_WEBHOOK:
        from gateway.platforms.msgraph_webhook import (
            MSGraphWebhookAdapter,
            check_msgraph_webhook_requirements,
        )

        if not check_msgraph_webhook_requirements():
            active_logger.warning("MSGraph webhook: aiohttp not installed")
            return None
        return MSGraphWebhookAdapter(config)

    if platform == Platform.BLUEBUBBLES:
        from gateway.platforms.bluebubbles import BlueBubblesAdapter, check_bluebubbles_requirements

        if not check_bluebubbles_requirements():
            active_logger.warning("BlueBubbles: aiohttp/httpx missing or BLUEBUBBLES_SERVER_URL/BLUEBUBBLES_PASSWORD not configured")
            return None
        return BlueBubblesAdapter(config)

    if platform == Platform.QQBOT:
        from gateway.platforms.qqbot import QQAdapter, check_qq_requirements

        if not check_qq_requirements():
            active_logger.warning("QQBot: aiohttp/httpx missing or QQ_APP_ID/QQ_CLIENT_SECRET not configured")
            return None
        return QQAdapter(config)

    if platform == Platform.YUANBAO:
        from gateway.platforms.yuanbao import YuanbaoAdapter, WEBSOCKETS_AVAILABLE

        if not WEBSOCKETS_AVAILABLE:
            active_logger.warning("Yuanbao: websockets not installed. Run: pip install websockets")
            return None
        return YuanbaoAdapter(config)

    return None
