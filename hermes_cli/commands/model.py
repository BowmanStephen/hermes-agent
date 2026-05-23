"""hermes model command."""


def cmd_model(args):
    """Select default model — starts with provider selection, then model picker."""
    from hermes_cli.main import _require_tty
    from hermes_cli.main import _require_tty
    _require_tty("model")
    from hermes_cli.models import select_provider_and_model
    select_provider_and_model(args=args)



