"""hermes gateway command."""


def cmd_gateway(args):
    """Gateway management commands."""
    from hermes_cli.gateway import gateway_command

    gateway_command(args)


