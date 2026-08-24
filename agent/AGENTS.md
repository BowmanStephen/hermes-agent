# Agent Core Guide

Read the repository operating rules first. This directory owns the conversation
loop's supporting machinery: provider adapters, memory, caching, compression,
delegation, secret scope, and auxiliary model work.

## Conversation and cache contract

- A conversation's system prompt, tool schemas, prior messages, and memory
  prefix are byte-stable until compression. Never inject a synthetic user
  message mid-loop or create adjacent messages with the same role.
- Context compression is the one supported context rewrite. Preserve lineage
  and ensure the compressed conversation remains valid for the provider.
- Cache-affecting changes to skills, memory, or tool configuration take effect
  on the next session by default; immediate invalidation must be explicit.
- Agent-level tools such as todo and memory are intercepted in `run_agent.py`
  before the general tool dispatcher. Keep that boundary deliberate.

## Delegation and auxiliary work

- `delegate_task` shares the caller's iteration budget. A subagent must not
  bypass cancellation, budget accounting, or the parent task's context rules.
- Keep side-LLM configuration under `auxiliary` in `config.yaml`; resolution
  lives in `auxiliary_client.py`. Do not add one-off provider settings.
- Curator work is idle-time maintenance. It must be bounded, profile-scoped,
  and unable to block an interactive turn.

## Secrets and profiles

- Use the scoped-secret API whenever multiplexed profiles are possible. A
  scoped miss must return the supplied default, never fall back to the process
  environment (which belongs to the default profile).
- This applies equally to credentials and authorization settings. A fallback
  can leak a different profile's allowlist or token.
- State, checkpoints, and caches use `get_hermes_home()`; tests set
  `HERMES_HOME` rather than relying only on `Path.home()`.

## Validation

Exercise a real turn or resolution chain when changing memory, compression,
provider routing, secret scope, or tool interception. Mock-only tests often
miss the imports and context boundaries that define the behavior.
