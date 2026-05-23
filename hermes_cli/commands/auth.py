"""hermes auth command."""


def cmd_auth(args):
    """Manage pooled credentials."""
    from hermes_cli.auth_commands import auth_command

    auth_command(args)


