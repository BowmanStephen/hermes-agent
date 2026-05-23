"""hermes proxy command."""

def cmd_proxy(args):
    """Local OpenAI-compatible proxy to OAuth providers."""
    # Lazy import — pulls in aiohttp, which is gated behind an extras install
    # for users who don't run the proxy or the messaging gateway.
    from hermes_cli.proxy.cli import cmd_proxy as _cmd_proxy

    rc = _cmd_proxy(args)
    if isinstance(rc, int) and rc != 0:
        raise SystemExit(rc)



