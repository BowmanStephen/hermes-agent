"""hermes cron command."""


def cmd_cron(args):
    """Cron job management."""
    from hermes_cli.cron import cron_command

    cron_command(args)


