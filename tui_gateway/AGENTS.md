# TUI Gateway Guide

Read the repository operating rules first. This Python service is the TUI's
authoritative backend for sessions, model work, tools, and slash commands.

- Preserve the newline-delimited JSON-RPC protocol with `ui-tui/`; changes need
  a compatible renderer counterpart and an end-to-end transport test.
- Keep the division of authority intact: Ink owns presentation and local
  interaction, while this gateway owns session state, agent invocation,
  approvals, and server-side slash-command execution.
- Do not bypass the general agent, tool, cache, or command contracts to make a
  TUI-only shortcut. The dashboard embeds this same TUI through a PTY bridge.
