"""hermes config command."""


def cmd_config(args):
    """Configuration management."""
    from hermes_cli.config import config_command

    config_command(args)


