# Scheduler Guide

Read the repository operating rules first. Cron jobs are profile-scoped,
durable work—not a route for mutating an interactive conversation.

- Preserve the scheduler's file lock, bounded catch-up/grace behavior, and
  per-job execution limits so concurrent ticks cannot duplicate work.
- Cron sessions run separately and default to `skip_memory=True`. Deliveries
  must not be mirrored into a gateway conversation, which would break that
  conversation's role alternation and cached context.
- Treat cron configuration as `config.yaml` state. Test missed-fire, retry,
  lock, and profile-isolation behavior on the actual scheduler path.
