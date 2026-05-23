"""hermes kanban command."""

def cmd_kanban(args):
    """Multi-profile collaboration board."""
    from hermes_cli.kanban import kanban_command

    return kanban_command(args)



