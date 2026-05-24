"""CLI command implementations."""

from .cron import cmd_cron
from .doctor import cmd_doctor
from .status import cmd_status
from .dump import cmd_dump
from .debug import cmd_debug
from .config import cmd_config
from .auth import cmd_auth
from .login import cmd_login
from .logout import cmd_logout
from .gateway import cmd_gateway
from .version import cmd_version
from .uninstall import cmd_uninstall
from .logs import cmd_logs
from .model import cmd_model
from .proxy import cmd_proxy
from .slack import cmd_slack
from .kanban import cmd_kanban
from .hooks import cmd_hooks
from .webhook import cmd_webhook
from .import_cmd import cmd_import
from .backup import cmd_backup
from .setup import cmd_setup
from .postinstall import cmd_postinstall
from .update import cmd_update
from .completion import cmd_completion
from .chat import cmd_chat
from .whatsapp import cmd_whatsapp
from .profile import cmd_profile
from .dashboard import cmd_dashboard

# Slash-command registry and dispatch core (CommandSurface, COMMAND_REGISTRY,
# resolve_*, telegram_menu_commands, discord_skill_commands_by_category, etc.)
# lives in _registry.py — formerly the hermes_cli/commands.py monolith, folded
# into this package so it is no longer shadowed by the package of the same name.
# Re-export its full public surface (including single-underscore internals that
# the gateway/Telegram/Discord adapters import) so that
# `from hermes_cli.commands import <X>` keeps resolving.
from . import _registry as _registry  # noqa: F401

for _name in dir(_registry):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_registry, _name)
del _name
