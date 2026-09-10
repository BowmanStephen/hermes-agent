> **STATUS (2026-09-09):** Superseded; explicitly inconclusive companion to the SIGTERM inventory.

# Gateway restart-churn hypothesis test

Date captured: 2026-08-26 11:39:14 CDT
Host: macOS 26.6.2, user `stephenbowman`
Task: `t_21eb00e9`

## Decision

No production gateway restart or `gateway run --replace` was performed. The leading hypothesis remains external lifecycle activity (updater/desktop handoff, dashboard/API action, operator/repair path, or another actor sending SIGTERM), followed by launchd replacement. A destructive reproduction is not safe in this worker context because the active gateway worker (PID 39566) is the direct parent of this task's Hermes process (PID 21872), and restarting it would terminate the worker and potentially interrupt sibling work and live connectors.

## Evidence reviewed

- Parent inventory: `/Users/stephenbowman/repos/hermes-agent-dev/.worktrees/t_5ad65dd5/gateway-restart-sigterm-inventory-2026-08-26.md`.
- Parent correlation: 38 in-scope cycles; 6 planned, 32 unexpected SIGTERM exits; unexpected exits returned code 1 and were replaced by launchd; no self-exit or crash evidence.
- Active launchd job: `gui/501/ai.hermes.gateway`, state `running`, `runs = 12`, wrapper PID 39565, worker PID 39566, last exit code 1, `KeepAlive=true`, `RunAtLoad=true`, `--replace --external-supervisor`.
- Current process topology: PID 39565 is launchd's wrapper (PPID 1); PID 39566 is its gateway worker; this task's Hermes process PID 21872 has PPID 39566.
- Gateway pidfile identifies PID 39566, same-home `/Users/stephenbowman/.hermes`, and argv `gateway run --replace --external-supervisor`.
- Historical signal-sender PIDs (5139, 3582, 64556, 63738, 66229, 73374, 34774) are no longer present, so their command lines cannot be recovered from the live process table.

## Safe controlled observation

A read-only 10-second observation was performed instead of a restart:

- Before: launchd gateway `state = running`, `runs = 12`, `pid = 39565`.
- After: launchd gateway `state = running`, `runs = 12`, `pid = 39565`.
- Both wrapper and worker remained alive with the same start time (10:55:55 CDT).
- This confirms no spontaneous churn during the observation window, but does not identify historical SIGTERM senders.

Current read-only connector probes were also healthy/expected: port 8787 returned HTTP 302, ports 9901 and 9119 returned HTTP 200, and ports 8642 and 8646 returned HTTP 404 as expected for those endpoints.

## Evidence gap and follow-up

The 32 unexpected historical SIGTERMs still have `parent_cmdline='(unknown)'`; the historical sender processes are gone, and broad unified-log correlation did not identify the initiating client. Disabling any restart-capable path would therefore be speculative and unsafe. Future evidence should be collected during the next naturally occurring event using the enriched shutdown-context and handoff logs; if a controlled restart is later required, it must be initiated outside this gateway-child worker context with an operator watching the launchd and connector state.

No repository source files or live configuration were modified.
