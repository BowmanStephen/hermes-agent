"""hermes webhook command."""

def cmd_webhook(args):
    """Webhook subscription management."""
    from hermes_cli.webhook import webhook_command

    webhook_command(args)



