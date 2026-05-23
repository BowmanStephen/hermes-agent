"""hermes hooks command."""

def cmd_hooks(args):
    """Shell-hook inspection and management."""
    from hermes_cli.hooks import hooks_command

    hooks_command(args)



