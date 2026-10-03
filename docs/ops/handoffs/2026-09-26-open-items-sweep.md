# Hermes fork: open-items sweep — 2026-09-26

Produced by a read-only multi-agent workflow sweep (14 subagents: 6 branch auditors, 2 PR triagers,
4 loose-end specialists, plus 2 re-runs after first-pass failures). Nothing was executed, pushed,
committed, or restarted; the live gateway and bookie bot were untouched.

## 1. Stale local branches — verdicts

| Branch | +commits | Verdict | Action | Why |
|---|---|---|---|---|
| `codex/aux-provider-disable-gap` | +11 | superseded | **drop** | All 11 commits' intent is in main (aux-provider gates, redaction, status-write guard, FK guards, kanban sticky-blocked, numpy pin). Nanoid bump moot (main on nanoid 5/6). |
| `fix/dashboard-profile-env-isolation` | +11 | superseded | **drop** | Branch tip landed on main verbatim as `17635a35a5` (now in `web_server_gateway.py`); FK fixes rearchitected by RecoverableHandleCache. |
| `healing/fork-port-20260825` | +14 | superseded | **drop** | Every commit's intent in main; the one missing identifier (discord bulk-sync threshold) was superseded by main's `_DISCORD_COMMAND_SYNC_POLICIES` mechanism. |
| `vps/reconcile-20260817` | +11 | mixed | **drop** | 9 of 11 commits in main verbatim/equivalent. Sole residual: legacy (non-pm-managed) node PATH edge in system-unit resolution — would need a fresh implementation against main's facts.json resolver, not this branch. |
| `docs/localize-development-guidance` | +1 | superseded | **drop** | Main independently shipped the same AGENTS.md localization 09-04 (`4441a2a28d`) with a strictly richer routing table. |
| `chore/preserved-dependency-update` | +1 | mixed | **rebase** | nanoid half already in main; **Electron 41.10.3 bump is NOT in main** (still 40.10.2). Upstream 0.21.5 rewired pinning (`electron-builder.config.cjs`, new `desktop-electron-pin` test), so re-apply just the Electron bump and regen the lockfile. |

Net: **5 branches droppable, 1 worth a targeted re-apply (Electron 41).**

## 2. Open upstream PRs (14) — triage

### From 2026-09-10 (recent batch)

| PR | Title | Rec | Note |
|---|---|---|---|
| 107452 | honor explicit empty platform toolsets | chase-review | clean, zero engagement, awaiting first review |
| 107450 | kanban worker lifecycle tools | **close** | bot-flagged duplicate of #62887 (earlier PR in a 3-way cluster) |
| 107449 | forward output_schema through dispatch | **close** | duplicate of #89537 which has the same fix + regression test |
| 107436 | stt CPU compute_type on Apple Silicon | rebase | conflicts; competing #81645 may be stale, so this stays viable |
| 107435 | Z.AI GLM-5.x overload handling | rebase | conflicts with main |
| 107433 | kanban.default_max_runtime_seconds | rebase | conflicts with main |
| 107432 | kanban crash reason | rebase | conflicts with main |
| 107431 | macOS orphan reaper fail-closed | rebase | conflicts with main |

### From 2026-08-15/16 (stale batch)

| PR | Title | Rec | Note |
|---|---|---|---|
| 87369 | pricing: strip date suffixes on snapshot ids | rebase | CONFLICTING, no maintainer signal |
| 87365 | pricing: GPT-5.4 family table entries | rebase | CONFLICTING against fast-moving table |
| 87360 | pricing: Z.AI/Ollama subscription_included | rebase | CONFLICTING; author note only |
| 87351 | cron ledger fail-open on interruption | rebase | CONFLICTING; bot comment only |
| 87336 | relay: defer close_session under live turn | chase-review | **mergeable clean** — just awaiting review |
| 86814 | composer plugin @ mentions | rebase | CONFLICTING, untouched since 08-15 |

Net: **9 rebases, 2 closes (duplicates), 2 awaiting review, 1 clean.**

## 3. AGENTS.md condensation in the live install — ✅ RESOLVED 2026-09-26

The condensation itself was committed 2026-09-25 22:30 by another session (`d3a2ac2542`), still
missing the load-bearing wine2e exclusivity clause. The clause was restored and pushed to fork main
as `a776ee89c8` (docs-only; `7992af33b1..a776ee89c8`). Live install tree is clean; no gateway
restart required. Details below for the record.

All operative instructions survive the condensation, but one named-load-bearing clause got dropped:
the wine2e lane's exclusivity ("fires **ONLY** on pushes to `wine2e/**` branches (inert on PRs and
main; costs nothing on normal work)"). Minor drift: per-marker glosses ("native Windows only",
"Linux or macOS") and the Teknium attribution / developer-guide pointer.

Suggested one-line restore: change "are proven by pushing probes to a `wine2e/**` branch, which runs
the on-demand ..." → "are proven by the `wine2e` lane, which fires only on pushes to `wine2e/**`
branches (inert on PRs and main), running the on-demand ...".

Ready-to-use commit message once fixed:
`docs(agents): condense uv-quarantine scope, platforms marker list, and wine2e lane guidance`

## 4. Ready-to-open upstream PR (drafted, not filed)

**`fix(state): tolerate uninspectable processes in foreign-holder scan`** (base: upstream `main`,
from branch `fix/psutil-holder-scan-skip-uninspectable`, commit `558edb1bcd`, already pushed to the fork).

Body drafted with Problem / Fix / Testing sections; key facts: the psutil arm used eager
`process_iter(["pid","open_files"])` so one unstat-able file anywhere on the machine failed the whole
scan and locked out structural maintenance; the fix enumerates per-PID with per-entry tolerance,
mirroring the Linux `/proc` arm. Validated in the fork's 0.21.5 final sweep (47,230 passed); fork main
carries the port (`2020d90ee4`). Reviewer-risk notes captured (catch breadth, asymmetry vs the Linux
arm's hermes-looking uninspectable flag, macos-only test marker).

## 5. Closed upstream PRs — both content survived

- **107451** (profile MCP `enabled` key): closed unmerged, but upstream landed an equivalent superset
  (`_mcp_entry_enabled` in `tui_gateway/methods_profiles.py`, `_parse_enabled_flag` in
  `hermes_cli/tools_config.py`, plus a dedicated test). Nothing to do.
- **107434** (dashboard named-profile env isolation): closed unmerged, but all identifiers landed in
  upstream `main` verbatim, including the exact test. Nothing to do.

## 6. Ship plan: `fix/log-triage-batch` (verified, awaiting go)

Pre-flight verified: `fix/pool-empty-log-noise` fully contained; push to `main` is a clean
fast-forward (local main == origin/main == `7992af33b1`); branch not yet on origin.

Commits: uv.lock relock (numpy→messaging extra) · empty-pool log provider attribution + quota-poller
guard · desktop backend start queued behind in-flight stop · gateway graceful answers for
dead/unpersisted sessions (approvals `{}`, timeline 200 `unpersisted_live`) · plugin-discovery logs
INFO→DEBUG · package-lock dev/peer flag resync.

Plan (Option A, PR route): `git push -u origin fix/log-triage-batch` → `gh pr create --repo
BowmanStephen/hermes-agent --base main` → merge → cutover. Deploy triggers for this batch:
**venv sync required** (uv.lock changed), **gateway restart required** (5 of 6 commits are Python-side),
web rebuild only precautionary (lock metadata only). Desktop Electron fix ships only on a desktop
app rebuild. Cutover happens only on Stephen's explicit go.

## 7. Also standing

- Next upstream sync is cheap right now: 118 commits behind, merge-tree probe clean (fetched 09-25 21:19).
- Housekeeping after the drops: retire worktrees `upstream-0.21.5` and detached `upstream-pristine`
  (keep `t_*` worker worktrees and `upstream-pr-pristine` until the psutil PR is filed).
