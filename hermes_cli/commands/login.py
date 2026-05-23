"""hermes login command."""


def cmd_login(args):
    """Authenticate Hermes CLI with a provider."""
    from hermes_cli.auth import login_command

    login_command(args)


