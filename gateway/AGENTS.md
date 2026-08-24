# Gateway Guide

Read the repository operating rules first. The gateway owns platform adapters,
session routing, lifecycle, and message delivery.

## Session capabilities and profiles

- Surface-specific tools are a property of the session client, not the backend
  process environment. Add them through a named toolset selected from the
  session platform/source; never gate GUI capabilities on `HERMES_DESKTOP`.
- `check_fn` answers reachability or opt-in only. It is process-wide cached and
  cannot safely answer a per-session question.
- Platform adapters with unique credentials acquire and release scoped token
  locks. Multiplexed profile reads must use the scoped-secret helpers and fail
  closed on a scoped miss.
- Control/approval commands must bypass both gateway message guards. Test both
  guards whenever changing command admission.

## Streaming delivery contract

- Draft frames are append-only prefixes. Do not alter formatting, add cursors,
  close fences, or reset segments during a draft stream.
- `finish(final_text)` is authoritative. Post-stream augmentation belongs in
  that payload, not in a mutation after the stream has sealed.
- Mark non-final adapter sends with `_interim_send`; seal interception exists
  on both egress paths.
- If a final follows an editable stream, reconcile with an edit first. Plain
  send is only the fallback when no editable message exists.
- Keep `draft_stream_is_message` checks explicit (`is True`) to avoid mock
  truthiness bugs. Extend `tests/gateway/test_stream_final_contract.py` for
  stream-contract changes.

## Lifecycle

- Background terminal completion notifications start a new agent turn. Preserve
  the configured verbosity behavior and never violate message alternation.
- `hermes serve` is desktop-owned and may exit with the app. Messaging gateways
  are detached services and must survive Desktop shutdown.
- Do not identify Hermes processes with substring scans of argv. Use the
  canonical gateway/update matchers and full command lines.
