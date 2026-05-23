"""hermes logout command."""


def cmd_logout(args):
    """Clear provider authentication."""
    from hermes_cli.auth import logout_command

    logout_command(args)


