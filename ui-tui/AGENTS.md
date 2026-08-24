# TUI Guide

Read the repository operating rules first. Ink owns the screen; `tui_gateway`
owns sessions, tools, model calls, and slash-command execution. Their transport
is newline-delimited JSON-RPC over stdio.

- Keep the primary chat experience in Ink. The web dashboard embeds this TUI
  through the PTY bridge; do not rebuild the transcript or composer in React.
- Handle only client-local commands in Ink. Send other slash commands through
  the gateway worker and preserve the gateway's command semantics.
- Keep streaming, approvals, clarifications, session selection, completion,
  and theming on their existing transport contracts. Any protocol change needs
  both sides and an end-to-end transport test.
- Use the package scripts for validation: `npm run typecheck`, `npm run lint`,
  `npm test`, and `npm run build` as appropriate.
