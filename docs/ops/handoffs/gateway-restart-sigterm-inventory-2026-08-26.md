> **STATUS (2026-09-09):** Superseded point-in-time snapshot (launchd state, PIDs); fleet now on 0.21.0.

# Local Gateway Restart / SIGTERM Inventory

Date captured: 2026-08-26 11:26 CDT (America/Chicago)
Host: macOS 26.6.2, user `stephenbowman`
Scope: read-only inventory of launchd, cron, running processes, scripts, updater/repair paths, watchdogs, and repository code. No process was stopped, restarted, killed, reloaded, or modified.

## Executive result

The active production gateway is supervised by the user LaunchAgent `ai.hermes.gateway`. Its wrapper process is PID 39565 and its gateway worker is PID 39566. The active plist explicitly runs `gateway run --replace --external-supervisor`, with `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=30`, and `ExitTimeOut=25`. `launchctl print` reports the job `running`, `runs=12`, and `last exit code=1` for the wrapper.

A second active LaunchAgent, `ai.hermes.dashboard`, runs a separate dashboard process (PID 39570); it is not a gateway restart command. An older disabled gateway plist is present under `~/Library/LaunchAgents/disabled/` but is not loaded. The active `com.hermes.fleet-pull` plist is not running and points to a missing checkout path, so it is not current restart evidence.

The repository contains several legitimate restart/SIGTERM mechanisms and defenses, but the read-only evidence does not identify which actor caused the historical 38 restarts referenced by the parent task. The current enriched shutdown-context logging is present in the checkout, while older log records do not expose a complete initiating parent/command history.

## 1. Active launchd jobs

### Candidate A — production gateway (confirmed active restart authority)

- Path: `/Users/stephenbowman/Library/LaunchAgents/ai.hermes.gateway.plist`
- Owner: user LaunchAgent domain (`gui/$(id -u)`); filesystem owner is the logged-in user.
- Trigger: `RunAtLoad=true`; `KeepAlive=true`; launchd respawns after exit, throttled to 30 seconds.
- Program/arguments:
  - wrapper: `/Users/stephenbowman/repos/hermes-agent-dev/venv/bin/python -m hermes_cli.stderr_timestamp --error-log /Users/stephenbowman/.hermes/logs/gateway.error.log --`
  - child: `/Users/stephenbowman/repos/hermes-agent-dev/venv/bin/python -m hermes_cli.main gateway run --replace --external-supervisor`
- Working directory: `/Users/stephenbowman/.hermes`
- Enabled state: loaded and running (`launchctl print gui/501/ai.hermes.gateway` reported `state = running`).
- Last execution evidence: `runs = 12`, `pid = 39565` (wrapper), `last exit code = 1`; `ps` shows worker PID 39566 started 10:55 AM and currently alive.
- Relevant plist excerpt (`ai.hermes.gateway.plist:38-58`):

```xml
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>30</integer>
<key>ExitTimeOut</key><integer>25</integer>
```

This is the only currently loaded local definition found that directly launches the production `gateway run --replace` command. Its `KeepAlive` means an intentional or unexpected SIGTERM can produce a respawn; `--replace` also makes startup eligible to take over an existing same-home gateway.

### Candidate B — dashboard supervisor (active, indirect only)

- Path: `/Users/stephenbowman/Library/LaunchAgents/ai.hermes.dashboard.plist`
- Owner: user LaunchAgent domain.
- Trigger: `RunAtLoad=true`; `KeepAlive=true`; 30-second throttle.
- Arguments: `/Users/stephenbowman/repos/hermes-agent-dev/venv/bin/hermes dashboard --no-open --host 127.0.0.1 --port 9119 --skip-build`
- Enabled state: loaded/running; PID 39570; `runs=5` from `launchctl print`.
- Last execution evidence: `ps` shows PID 39570 alive since 10:55 AM.
- Assessment: does not itself invoke gateway restart. Dashboard source does expose `/api/gateway/restart` and an onboarding webhook-enable auto-restart path (see repository candidates below), but this plist only keeps the dashboard alive.

### Candidate C — fleet pull (inactive/stale)

- Path: `/Users/stephenbowman/Library/LaunchAgents/com.hermes.fleet-pull.plist`
- Owner: user LaunchAgent domain.
- Trigger: `StartInterval=1200` seconds plus `RunAtLoad=true`.
- Arguments: `/bin/bash -c exec "$HOME/repos/hermes-fleet/scripts/fleet-pull.sh"`
- Enabled state: loaded but `state = not running`.
- Last execution evidence: `launchctl print` reported `runs=149`, `last exit code=0`; current target `/Users/stephenbowman/repos/hermes-fleet/scripts/fleet-pull.sh` was not found during inspection (`/Users/stephenbowman/hermes-fleet` also does not exist).
- Assessment: historical execution evidence exists, but no current script was available to determine whether it restarted a gateway. Treat as an evidence gap, not an active cause.

### Candidate D — disabled historical gateway plist

- Path: `/Users/stephenbowman/Library/LaunchAgents/disabled/ai.hermes.gateway.plist.pre-discord-fix-20260816-151916`
- State: under `disabled/`; not listed as a loaded job.
- Arguments: legacy `/Users/stephenbowman/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace` (no `--external-supervisor`).
- Assessment: historical configuration only; not current execution evidence.

### Other launch agents inspected

`com.hermes.workspace`, `com.hermes.webui`, `com.hermes.a2a-tunnel`, and `com.hermes.a2a-macbook-tunnel` are active but do not launch the Hermes production gateway. Their arguments are respectively a Node workspace server, a third-party web UI bootstrap, and SSH tunnels. The workspace plist has `KeepAlive=true`; its wrapper uses `exec node`, not gateway lifecycle commands. The MacBook SSH tunnel has `runs=48`, `last exit code=255`, but is not a gateway process.

No system LaunchDaemon in `/Library/LaunchDaemons` was Hermes-specific. The system launchd list showed no `hermes-gateway` daemon.

## 2. Current process evidence

Command used: `ps auxww | grep -Ei '[h]ermes|[g]ateway|[w]atchdog|[u]pdat|[r]epair'`.

Relevant processes at capture time:

- PID 39565: launchd-managed stderr wrapper for `gateway run --replace --external-supervisor`.
- PID 39566: `/Users/stephenbowman/repos/hermes-agent-dev/venv/bin/python -m hermes_cli.main gateway run --replace --external-supervisor`.
- PID 39570: launchd-managed dashboard (`hermes dashboard --no-open ... --port 9119`).
- PID 39599: separate `hermes serve --host 127.0.0.1 --port 0` process from the installed profile environment; not the production gateway command.
- Hermes Desktop PID 90452 and helpers: old release at `/Users/stephenbowman/.hermes-old-20260821/.../Hermes.app`; no direct gateway argv observed.
- No standalone `watchdog` process targeting the gateway was found. macOS `/usr/libexec/watchdogd` is a system process and not Hermes-specific.

The gateway PID file `/Users/stephenbowman/.hermes/gateway.pid` and lock file both identify PID 39566 with argv `gateway run --replace --external-supervisor`, home `/Users/stephenbowman/.hermes`, and start timestamp `178775975509`.

## 3. Cron and scheduler inventory

User crontab was captured with `crontab -l`:

- `@reboot ... ~/server/scripts/tmux-sessions.sh` — creates missing tmux sessions; no gateway action.
- `*/30 * * * * bash ~/server/scripts/mini-health.sh` — checks Ollama, Syncthing, Tailscale, disk, and tmux; no gateway action.
- Weekly backup retention, state snapshot pruning, user-md sync, and a cfb-model-lab report — no gateway action in the visible entries.
- A legacy gateway-channel-health line is explicitly commented out and marked disabled on 2026-08-03.
- `atq` showed one historical job scheduled for Sat Aug 15 02:28:00 2026; no command body was available from `atq`, so attribution is unknown.

The Hermes profile config has `toolsets: [kanban]`, and the profile cron runtime files contain heartbeat/lock/execution state but no readable `jobs.json` in the inspected root/profile locations. The repository’s `cron/jobs.py` and `cron/lifecycle_guard.py` show that cron creation rejects direct lifecycle commands to prevent agent-driven SIGTERM/respawn loops. The guard recognizes:

- `hermes gateway restart|stop|uninstall`;
- `hermes -p <profile> gateway restart|stop` when self-targeting;
- launchctl lifecycle operations involving a Hermes gateway label;
- `systemctl restart|stop|start ... hermes-gateway`;
- `kill`/`pkill` combinations targeting Hermes gateway;
- `launchctl submit`/`bootstrap` command forms, including indirect/punctuation/continuation variants.

Assessment: no active Hermes cron job was shown to be a restart source. The scheduler has internal watchdogs and SIGTERM cleanup for child jobs, but those terminate job subprocesses/process groups, not the gateway itself.

## 4. Shell scripts, health checks, updater/repair paths

### `/Users/stephenbowman/server/scripts/mini-health.sh`

Runs every 30 minutes from user cron. Checks services and sends an iMessage alert on failure. It does not call Hermes, launchctl, kill, restart, or SIGTERM. The only process-like check is `tmux has-session`.

### `/Users/stephenbowman/server/scripts/tmux-sessions.sh`

Runs at reboot and only creates absent tmux sessions. No gateway command or signal operation.

### `/Users/stephenbowman/hermes-workspace/launch-workspace.sh`

Used by `com.hermes.workspace`. It sources the workspace `.env`, exports host/port/node settings, then `exec`s `server-entry.js`; no gateway lifecycle operation. Its `trap ':' TERM INT` ignores shell-level TERM/INT for the wrapper, but this is the workspace service, not the Hermes gateway.

### Workspace stable scripts

`/Users/stephenbowman/hermes-workspace/scripts/start-stable.sh` and `stop-stable.sh` call `kill -0`, `kill`, and `kill -9` against the workspace PID file or listeners on the workspace port. They can terminate workspace processes only; they contain no Hermes gateway command.

### Install/updater scripts

Repository `scripts/install.sh` contains a manual foreground/background startup path using `nohup $HERMES_CMD gateway`, and prints instructions to stop with `kill $GATEWAY_PID` or restart with `hermes gateway`. This is an install-time/manual path, not a scheduled active host process.

The repository documentation and updater implementation describe an important legitimate restart source: `hermes update` refreshes service-managed gateways after an update; launchd gateways are restarted through the service manager, while manual gateways are relaunched when mapped to a profile. This path is not currently running merely because the updater code exists. No active updater process was found; only unrelated CodexBar Sparkle updater processes were present.

`docker/stage2-hook.sh` concerns container ownership repair and comments on restart loops; it is not active on this macOS host. Docker/systemd/s6 restart logic is therefore a portability/reference candidate, not local execution evidence.

## 5. Repository restart and SIGTERM sources

### Direct termination primitive

- `/Users/stephenbowman/repos/hermes-agent-dev/gateway/status.py:305-335`, `terminate_pid(pid, force=False)`: POSIX sends `SIGTERM` normally and `SIGKILL` when forced; Windows uses `taskkill /T /F` for force mode.
- `/Users/stephenbowman/repos/hermes-agent-dev/gateway/status.py:1712+`: `--replace` takeover marker documents that a new gateway SIGTERMs the existing same-home gateway before taking over.
- `/Users/stephenbowman/repos/hermes-agent-dev/gateway/run.py:30980-31024`: handles SIGTERM/SIGINT, records shutdown context, spawns detached diagnostics, stops the runner, and routes restart signals through `runner.request_restart(detached=False, via_service=True)`.
- `/Users/stephenbowman/repos/hermes-agent-dev/cron/scheduler.py:3818-3839`: SIGTERMs then SIGKILLs a cron child process group on timeout; this is not a gateway kill path.

### Dashboard/API restart path

- `/Users/stephenbowman/repos/hermes-agent-dev/hermes_cli/web_server.py:4612-4621`: `_spawn_hermes_action` launches management actions from the dashboard, explicitly removing `_HERMES_GATEWAY` from the child environment.
- `web_server.py:4794-4846`: `_spawn_gateway_restart` coalesces/reuses recent restart requests and spawns the `gateway-restart` action.
- `web_server.py:4849-4868`: webhook enable can trigger `_restart_gateway_after_webhook_enable`.
- `web_server.py:4871+`: `POST /api/gateway/restart` starts the background restart action.

These are real local restart-capable code paths, but they require a dashboard/API caller. No request log or action log was available in the captured evidence proving one caused the historical restarts.

### In-band restart and service exit contract

- `/Users/stephenbowman/repos/hermes-agent-dev/gateway/restart.py:8-21`: service restart exit code is 75; `--external-supervisor` is represented by `HERMES_GATEWAY_EXTERNAL_SUPERVISOR`.
- `gateway/restart.py:27-45`: in-band restart (`/restart`, SIGUSR1, child CLI) drains turns before stopping.
- `website/docs/reference/cli-commands.md:262-267`: external-supervisor contract says in-chat restart/update exits 75 so launchd/systemd relaunches the gateway.

The active plist’s `--external-supervisor` flag therefore intentionally turns a planned in-band restart into an exit-and-respawn cycle managed by launchd.

### Cron lifecycle defenses

- `/Users/stephenbowman/repos/hermes-agent-dev/cron/lifecycle_guard.py:1-38, 74-102, 597-617, 1264-1271`: blocks direct and indirect gateway lifecycle commands in cron specs.
- `/Users/stephenbowman/repos/hermes-agent-dev/cron/jobs.py:2067-2072`: applies that guard in `create_job`, including model-tool creation rather than only CLI creation.
- `/Users/stephenbowman/repos/hermes-agent-dev/tools/approval.py:1112-1131`: approval detection for launchctl operations targeting Hermes.

### Shutdown attribution logging

The current checkout already includes enriched shutdown-context logging in `gateway/run.py:30999-31018`, writing a one-line context and detached diagnostic log. Parent-task history records that an attribution fix was committed separately (`195f53674cfb86b1a0d1605399363fb562e9bbfb`) and focused tests/live health passed, but historical attribution remains incomplete because prior logs and parent snapshots do not contain the initiating command for all 38 starts.

## 6. Last-execution evidence and gaps

Observed now:

- launchd gateway job: running, `runs=12`, wrapper last exit code 1, wrapper PID 39565, worker PID 39566.
- gateway worker start in `ps`: 10:55 AM on 2026-08-26.
- dashboard job: running, `runs=5`, PID 39570, since 10:55 AM.
- fleet-pull: not running, 149 historical runs, last exit 0, target script absent.
- user cron: no active gateway restart line; legacy channel-health line disabled.
- `atq`: one historical job listed, command body unavailable.
- unified `log show` query for the last 7 days timed out before returning records; therefore no claim is made about absence of launchd unified-log evidence.

Evidence unavailable or unresolved:

1. The exact initiator for each historical restart (dashboard request, update, in-band `/restart`/SIGUSR1, launchd stop/respawn, manual command, or an external actor).
2. The missing `~/repos/hermes-fleet/scripts/fleet-pull.sh` body and its historical logs.
3. The command body of the historical `at` job.
4. A complete launchd/unified-log correlation for all 38 historical restarts; existing older shutdown records predate the enriched marker/context.

## 7. Commands used (all read-only)

```text
date '+%Y-%m-%d %H:%M:%S %Z'
git status --short --branch
launchctl list
launchctl print gui/$(id -u)/ai.hermes.gateway
launchctl print gui/$(id -u)/ai.hermes.dashboard
launchctl print gui/$(id -u)/com.hermes.fleet-pull
launchctl print gui/$(id -u)/com.hermes.workspace
launchctl print gui/$(id -u)/com.hermes.webui
ps auxww | grep -Ei '[h]ermes|[g]ateway|[w]atchdog|[u]pdat|[r]epair'
crontab -l
atq
```

Repository and host-file searches used `search_files` for gateway lifecycle strings, restart/replace flags, SIGTERM, kill/killpg, launchctl/systemctl, watchdogs, updater/repair scripts, cron jobs, and launch agents. Relevant plists and source excerpts were read with `read_file`.

## Safety statement

No process was stopped, restarted, killed, reloaded, or modified. No launchd plist, live config, `.env`, database, cron entry, or profile state was edited.
