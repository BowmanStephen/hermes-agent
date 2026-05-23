"""hermes status command."""


def cmd_status(args):
    """Show status of all components."""
    from hermes_cli.status import show_status

    show_status(args)


