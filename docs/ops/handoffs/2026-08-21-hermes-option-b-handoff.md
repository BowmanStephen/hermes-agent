> **STATUS (2026-09-09):** LIVE-OPERATIONAL — binding rules herein: only the default profile owns DISCORD_BOT_TOKEN / BLUEBUBBLES_* / API_SERVER_* / WEBHOOK_* keys. B6 (memory/USER.md three-way) still open.

# Hermes Option B — Parallel Clean Home · Session Handoff

**Decision:** build a new `HERMES_HOME` alongside the existing one, configure it deliberately, cut over when it works. The current install is never modified, so rollback is instant.

**Status: B0–B5 complete. All 18 personas on A2A, memory budget green. The one remaining blocker is `memory/USER.md` — an editorial three-way choice only Stephen can make; the memory symlinks stay off until it is resolved.**

Background: `~/hermes-rebuild-vs-scratch.md` (why B) · `~/hermes-backup-incident-handoff.md` (original incident).

---

## Current position

| | |
|---|---|
| Live home `~/.hermes` | **the new build** — v37, 22 profiles, 22 cron jobs, gateway supervised, doctor clean |
| Retired `~/.hermes-old-20260821` | untouched, 14 GB, 106 B config preserved — rollback source |
| Full backup | `~/hermes-backup-2026-08-20-223823.zip` — 1.8 GB |
| `hermes-fleet` | **3 B5 commits pushed to `origin/main`** (`0613fe2..8d0e215`), tree clean · this machine = **`mini`** |
| Profiles | **22, all v37, real souls, all served by the gateway** |
| Gateway | **running** under launchd, 9 platforms, 0 profiles skipped |

---

## The two rules that matter most

**1. The cutover is done — `~/.hermes` is now the live new build.** Plain `hermes` commands hit it with no env var. `HERMES_HOME` is still the only way to target another home (there is no `--home` flag), and it is required for per-profile work:
```bash
HERMES_HOME=~/.hermes/profiles/<name> hermes config migrate
```

**2. Only the default profile may own the Discord bot and the shared listener.** `DISCORD_BOT_TOKEN`, `BLUEBUBBLES_*`, `API_SERVER_*` and `WEBHOOK_*` belong in the root `.env` only — never in a profile `.env`. Profile `.env` files carry **51 keys**; root carries 62. Violating this makes the gateway silently skip every named profile (see B4).

---

## Standing rules

- **Never run `restore-hermes.py`** — writes 23 stub SOULs over real personas and reports success.
- **`~/.hermes-old-20260821` is read-only.** Copy *out*; delete nothing until the new home has run clean for a while.
- **Never create an output directory without checking it exists first.**
- **Verify by content** (hash, size, `hermes config get`), never by "does the path exist."
- **`hermes config check` is NOT a gate** — never prints "valid", exits 0 even on version mismatch. Use `hermes doctor`.
- **zsh does not word-split unquoted variables.** `for i in $ITEMS` iterates once over the whole string. Use list literals.
- Do not touch `~/repos/cfb-model-lab`.

---

## ✅ B0 — Stand up and harness (COMPLETE)

- `hermes backup` → `~/hermes-backup-2026-08-20-223823.zip`, 1.8 GB, exit 0
- `hermes-fleet` scripts restored via `git checkout -- scripts/mini/` → 96 files, repo fully clean
- `~/.hermes-clean` created, isolation verified both directions

**The 513-byte SOUL mystery is solved.** A brand-new home scaffolds itself on first contact and writes `SOUL.md` at exactly **513 B**. The stubs across the old install and its 23 profiles were Hermes regenerating its built-in default identity into an emptied home — not corruption. Reproduced from scratch.

## ✅ B1 — Credentials (COMPLETE)

10 items hash-verified MATCH: `.env`, `auth.json`, `shared/`, `mcp-tokens/`, `secrets/`, `auth/`, 4× `google_*.json`. 62 keys, mode 600 preserved. `hermes status` shows OpenRouter, OpenAI, Google/Gemini, xAI, NVIDIA NIM, Z.AI, Tavily, FAL all live.

## ✅ B2 — Deliberate configuration (COMPLETE)

| Gate | Result |
|---|---|
| Config | snapshot v36 → migrated to **v37** |
| `hermes doctor` | **All checks passed** |
| `SOUL.md` | 8,690 B — labelled `macbook.SOUL.md`, but that file is **byte-identical** to `mini.SOUL.md`, so the right content landed by luck (see B5) |
| Memory | MEMORY.md 7,747 B + USER.md 4,007 B, hash-verified, injection enabled |
| Memory provider | **holographic active** — the 78 facts are reachable again |
| Cron | **22 jobs** (27 minus 5 disabled debris); all 18 script-backed resolve |
| Scripts | 125 files |

### ⚠️ Critical discovery — the fleet script-path trap

**This machine's cron jobs reference `scripts/mini/`, NOT `scripts/macbook/`.** Deploying `scripts/macbook` as originally planned left **17 of 18 jobs pointing at nonexistent scripts** — they would have installed cleanly, fired on schedule, and failed silently.

Resolved at the time by deploying `fleet/scripts/mini` as the base plus `macbook` extras as a union. Verified: 22 jobs, 0 missing scripts.

> 🔴 **Superseded by B5 — the diagnosis was wrong.** There is no naming drift in
> `hermes-fleet`. This machine simply **is** the Mini (hostname
> `Stephens-Mac-mini-3`; cron job-id overlap `mini.json` 16/17 vs
> `macbook.json` 0/6). The fleet identity was misread as `macbook`. The repo was
> correct all along, and `scripts/mini/` was always the right source.
>
> The `macbook` extras half of that union was therefore **unnecessary** — it
> copied in 26 MacBook-owned scripts and one dead symlink. See B5.

### Left undone deliberately

The 1,475-line config was **not pruned** — `doctor` passes as-is, and removing keys is Stephen's call. Candidates when he wants to:
- Toolset references that do not resolve: `no_mcp`, `touchdesigner`, `hermes-google_chat`, `hermes-teams`
- `a2a` warnings at migrate time are a **false alarm** — it loads as a plugin toolset after the validator runs and shows enabled
- 1Password is enabled with an empty `env:` map — configured but doing nothing. Bitwarden works, applying 19 secrets.

---

## ✅ B3 — Profiles (22) — COMPLETE

All 22 profiles built (then in `~/.hermes-clean/profiles/`, now live at `~/.hermes/profiles/`). `hermes doctor`: **All checks passed**, every profile validated. `cfblocalworker` correctly absent. Old home untouched (still 23 profiles, 106 B config, 22:17). No gateway started.

### Source: the pre-incident snapshot

`~/agent-config-backup-2026-08-20/.../backups/nous-cutover-20260820-124254/profiles/` — 12:42 on 2026-08-20, before the damage. Held exactly the keep-22 roster with real configs and souls.

### What was taken from where

| Component | Source | Why |
|---|---|---|
| `config.yaml` | **snapshot**, then migrated to v37 | preserves each profile's differentiated config; `--clone` would have given 22 identical copies of root |
| `SOUL.md` | **snapshot** (21) + freshly written (`ops`) | see the fleet-is-stale finding below |
| `.env` | root clone at the time — **later corrected in B4** | ⚠️ This was wrong. The snapshot's 51 keys were an intentional subset, not a deficiency. See the B4 correction. |
| `profile.yaml` | generated by `hermes profile create` | holds the kanban routing description |

Method per profile: `hermes profile create <name> --clone --description "…"` for native registration and alias creation, then overwrite `config.yaml` + `SOUL.md` from the snapshot, then migrate.

### Migrating a profile config

`_config_version` is **not** a top-level key — it sits mid-file (line ~902). Snapshot profiles were a mix of **v33, v35, and v36**. All migrate cleanly by pointing `HERMES_HOME` at the profile directory itself:

```bash
HERMES_HOME=~/.hermes-clean/profiles/<name> hermes config migrate
```

The `a2a` / `hermes-google_chat` / `hermes-teams` warnings during migration are the known-benign set from B2.

### ⚠️ Finding: the fleet souls are generationally stale — do not deploy them

All 17 overlapping souls **differ** from the snapshot, and the snapshot is larger in every single case. This is not cosmetic drift. The fleet copies still describe the superseded **"Bot Chat"** shell-profile messaging (`hermes -p <agent> chat …`); the snapshot copies describe the **native A2A mesh** (`a2a_call` / `a2a_orchestrate`, local + remote peer lists).

**Deploying `hermes-fleet/souls/bots/` would have regressed every profile off the A2A migration.** The snapshot was used instead. ✅ **Fixed in B5** — all 22 souls resynced to the repo from the live home.

### `ops` — the one soul written fresh

The snapshot's `ops/SOUL.md` was byte-identical to `swarm-forge/SOUL.md` (both "You are SwarmForge") — a real defect in the snapshot.

Its identity was resolved from its config: `ops/config.yaml` is **byte-identical** to `backend`, `frontend`, and `researcher` (md5 `1bceaadc1bc76e712b9db23c09c33125`) — the worker tier — and distinct from `sysop`. So `ops` is an **Operations Worker**, not a named persona. A new soul (1,892 B) was written to the same worker template as its three siblings, explicitly distinguishing it from Sysop.

### Minor inconsistency, left alone

Four profiles (`critic`, `pliny`, `pocock`, `studio`) declare the model as `openai/gpt-5.6-luna` while the other 18 use bare `gpt-5.6-luna`. Both resolve and `doctor` passes. This came from the original configs, so it was not "corrected" — flagging only.

## ✅ B4 — Cutover (COMPLETE, 2026-08-21)

`~/.hermes` is now the new build. `~/.hermes-old-20260821` holds the old install, untouched at 14 GB with its 106 B config intact. `hermes doctor`: **All checks passed**.

### ⚠️ The plan was wrong: the clean home was config-only

`~/.hermes-clean` was **465 MB against the old home's 14 GB**. It had no runtime and no data stores. A straight `mv` would have produced a non-functional install:

| Absent from the clean home | Size |
|---|---|
| `hermes-agent/` — the git checkout **and** the venv everything executes from | 6.8 G |
| `venvs/` · `node/` | 835 M · 189 M |
| `lcm.db` · `sessions/` | 643 M · 476 M |
| `gateway/`, `state/`, `kanban.db`, `memory_store.db`, `projects.db`, `plugins/`, `platforms/` | small |

**`hermes-agent` is not disposable runtime** — it is a git checkout of `BowmanStephen/hermes-agent`. It was clean and in sync with `origin/main`, so it could be reproduced exactly. Verify that before ever rebuilding it.

### What was done

Chosen: **swap first, reinstall the runtime after; carry operational data, drop session history.**

1. Stopped the gateway and unloaded every hermes launchd job.
2. Copied into the clean home, with the gateway down so the copies were consistent: `kanban.db`, `memory_store.db`, `projects.db`, `lcm.db`, `response_store.db`, `verification_evidence.db` (all `-wal`/`-shm` too, all `PRAGMA integrity_check` = ok), plus `gateway/`, `state/`, `plugins/`, `platforms/`, `channel_directory.json`, `install_id`. **`sessions/` deliberately not carried.**
3. `mv ~/.hermes ~/.hermes-old-20260821` · `mv ~/.hermes-clean ~/.hermes`
4. Rebuilt the runtime, then `hermes gateway install --force`, then reloaded the other services.

### 🔑 The swap kills the `hermes` command — plan for it

`~/.local/bin/hermes` is a 4-line wrapper that execs `~/.hermes/hermes-agent/venv/bin/hermes`. After the rename that path does not exist.

**The old venv is not a usable fallback.** Its `pyvenv.cfg` home points into `~/.hermes/hermes-agent/.hermes-runtime/python/...`, so relocating the directory breaks its interpreter too. There is no bridge — the rebuild is mandatory, not optional.

Rebuild (uv 0.11.21 + `uv.lock` are already on the box; needs network):
```bash
git clone ~/.hermes-old-20260821/hermes-agent ~/.hermes/hermes-agent
cd ~/.hermes/hermes-agent
git remote set-url origin https://github.com/BowmanStephen/hermes-agent.git
UV_PROJECT_ENVIRONMENT=~/.hermes/hermes-agent/venv uv sync --frozen
```
Result: Python 3.11.14 (was 3.11.15 — patch drift, harmless). `node/` and `venvs/` were **not** restored and nothing has needed them; system node v26.7.0 covers it.

### 🔴 Correction to B3: the 62-key `.env` decision was wrong

B3 cloned the root `.env` (62 keys) into all 22 profiles, calling the snapshot's 51 keys "a strict subset, so root is strictly better." **That was backwards.** The 11 extra keys are exactly the single-owner ones:

`API_SERVER_*` · `BLUEBUBBLES_*` · `WEBHOOK_*` — the three port-binding platforms · `DISCORD_BOT_TOKEN` · `OP_SERVICE_ACCOUNT_TOKEN`

They were withheld from profile `.env` files **by design**, so that only the default profile owns the shared HTTP listener and the Discord bot identity. Cloning them everywhere made all 18 named profiles try to claim the listener, and the gateway skipped every one of them:

> `Skipping secondary profile 'sysop' due to port-binding config error … enables port-binding platform(s) bluebubbles, but gateway.multiplex_profiles is on`

Nothing in either `config.yaml` mentioned `bluebubbles` — the platform is enabled by the **presence of its env vars**. Setting `gateway.platforms.bluebubbles.enabled: false` did **not** fix it; only removing the keys did.

Fixed: all 22 profile `.env` files reduced 62 → 51 keys, originals kept at `.env.bak-b4-listener-keys`. Root `.env` still has all 62. Result: **9 platforms, 0 profiles skipped.**

**Rule going forward: profile `.env` files get 51 keys. Never propagate the listener-owning eleven.**

### Also fixed: skills were never carried

B2 did not carry `skills/`. The new home had **0**; the old had 14 categories (5.9 MB). Restored from `~/.hermes-old-20260821/skills/` → **81 builtin skills, 50 enabled**.

### Discord: no backfill burst

Carrying `gateway/discord_message_recovery.db` worked — backfill was **8 messages, zero outbound sends**. `require_mention: true` is holding, so silence until mentioned is correct behavior.

### Services restored

`ai.hermes.gateway`, `com.hermes.webui` (HTTP 200), `com.hermes.workspace`, both a2a tunnels, `com.hermes.fleet-pull`, `com.hindsight.daemon`. Most plists only referenced `~/.hermes/…` **log paths**, which resolve again after the rename — but `~/.hermes/webui/` and `~/.hermes/workspace/` are log dirs that had to be recreated by hand before those two would load.

### Loose ends

- **SQLite WAL-reset vulnerability.** The rebuilt venv links SQLite 3.50.4, which warns on every WAL database. Fix is 3.51.3+ (or 3.50.7 / 3.44.6). Not a cutover failure — pre-existing and cosmetic today.
- **Redundant key in 18 profiles.** `gateway.platforms.bluebubbles.enabled: false` was written into the 18 named profiles while diagnosing. Now unnecessary (the `.env` fix is the real cure) but harmless and explicit. The 4 worker profiles do not have it — a cosmetic inconsistency.
- **Session history is gone from the live home** by choice. The 1,153 sessions remain in `~/.hermes-old-20260821` and the 1.8 GB backup.

### Rollback (still available)

```bash
hermes gateway stop
mv ~/.hermes ~/.hermes-new-20260821
mv ~/.hermes-old-20260821 ~/.hermes
hermes gateway install --force && hermes gateway start
```
The old install's own venv works again as soon as it is back at `~/.hermes`.

---

## ✅ B5 — Commit back (COMPLETE, 2026-08-21) — repo only, no live changes

Three commits on `main` in `hermes-fleet`, working tree **clean** (a dirty tree
blocks this machine's fleet-pull). **Pushed to `origin/main` 2026-08-21**
(`0613fe2..8d0e215`, clean fast-forward, 0 behind). The MacBook and VPS will
pick these up on their next 20-minute fleet-pull — including the resynced souls.

```
8d0e215 docs(runbook): record Mini script-symlink regression + machine identity test
afc3f36 chore(mini): track live scripts; export migrated v37 config + 22-job cron
19c4593 fix(souls): resync souls/bots from live Mini home — A2A, not Bot Chat
```

### 🔴 Correction: this machine is `mini`, not `macbook`

The B5 plan said to fix "script-path drift" because cron referenced
`scripts/mini/` while the fleet identity was `macbook`. **There was no drift.**
This box *is* the Mini; the identity was misread, and the repo was right all along.

Proof, by content:

| Signal | Result |
|---|---|
| hostname | `Stephens-Mac-mini-3` |
| cron job **id** overlap | `cron/mini.json` **16/17** · `cron/macbook.json` **0/6** |
| `fleet/ownership.json` | `mini.paths.scripts = scripts/mini`, `runtime_home = /Users/stephenbowman/.hermes` |
| `validate-fleet-ownership.py` | `machine=mini`, all green |
| `export-cron.sh` auto-detect | resolved `mini` unprompted |

**Why the confusion held so long:** `souls/macbook.SOUL.md` and
`souls/mini.SOUL.md` are **byte-identical** (md5 `64b0bf19…`, 8,690 B). B2
deployed "the macbook soul" and got the right file by luck, so the SOUL check
could never have caught the error.

`macbook` is a **real, different machine** — home `/Users/stephen_bowman/`
(underscore), hostname `stephens-macbook`, role workstation, profiles
`default` + `cfblocalworker`. That is why `cfblocalworker` is correctly absent here.

> ⚠️ **`scripts/macbook/` and `cron/macbook.json` belong to another machine.**
> Never export to them from here. Exporting this Mini's v37 config over
> `configs/macbook.yaml`, as the original B5 item 3 said, would have silently
> overwritten the MacBook's config-as-code with the wrong machine's config.

### What was done

| # | Item | Result |
|---|---|---|
| 1 | Script-path drift | **Void — no drift.** Machine mapping corrected instead. Real hostname added to `fleet/ownership.json` (`auto_machine()` had matched only via its `"mac"+"mini"` substring fallback; the exact list still said `stephens-mac-mini-1`). |
| 2 | Two untracked scripts | `heat-watchdog.sh` (4,359 B) + `kanban-done-notify.py` (8,168 B) → `scripts/mini/`, hash-verified, exec bit set. **A third was found:** see below. |
| 3 | Export config + cron | → `configs/mini.yaml` (875→1,454 lines, v33→**v37**) and `cron/mini.json` (17→**22 jobs**) via `scripts/export-cron.sh`. Config scanned for inline secrets: **none** — all provider keys are `*_env:` name references. |
| 4 | Stale souls | **Confirmed and fixed.** All 17 tracked souls had **0** `a2a_call`/`a2a_orchestrate` refs; live have 4 each. Resynced all 22 from the live home, hash-verified. |
| 5 | `hindsight-daemon.sh` drift | **Already resolved** — it is live and byte-identical to the repo (`6fcf497c…`). No action. |

### Souls: the live home was the correct source, not the snapshot

Verified before overwriting: live `SOUL.md` is **byte-identical to the
pre-incident snapshot for all 21** personas, and the snapshot's `ops/SOUL.md` is
md5-identical to `swarm-forge`'s (the known defect) while the live `ops` is the
freshly written 1,892 B worker soul. So copying from `~/.hermes/profiles/*/SOUL.md`
gives the snapshot's content *plus* the ops correction in one step.

Result: 17 modified + 5 added (`pliny`, `ops`, `backend`, `frontend`, `researcher`)
— exactly what the plan predicted. Retired personas `clerk`, `mentor`, `steward`
are still in `souls/bots/` and were left alone; they are not in the keep-22.

### ✅ Resolved: `~/.hermes/scripts` is a symlink again (2026-08-21)

`runbook.md` has required `~/.hermes/scripts -> <clone>/scripts/<machine>/` since
2026-07-05. The rebuild left it a **real directory**, so
`validate-script-deployment.py --machine mini` failed. **Restored 2026-08-21.** **This is the root cause of
B5 items 1, 2 and 5** — under the intended design the live scripts *are* the repo,
and none of that drift can exist.

Consequences found:
- The two untracked scripts (item 2) — plus a **third**, never listed:
  `check-data-freshness.mjs`, a **dead symlink** into `/Users/stephen_bowman/…`
  (the MacBook's home). Unreferenced by any cron job. A fifth instance of the
  runbook's "MacBook-path bug class".
- 26 MacBook-owned scripts copied in by the rebuild's "mini + macbook extras"
  union. All 26 are tracked under `scripts/macbook/` and byte-identical; **none**
  is referenced by this machine's cron.

**Restored:** `~/.hermes/scripts -> repos/hermes-fleet/scripts/mini`. The prior
real directory (125 files) is preserved at `~/.hermes/scripts.pre-symlink-20260821`,
matching the 2026-07-05 precedent — that is the rollback (`rm` the link, `mv` it back).

Checks that cleared the gate: all **16** cron-referenced scripts resolve through
the link; the only launchd reference (`com.hindsight.daemon.plist` →
`hindsight-daemon.sh`) survives; and the single apparent cross-reference to a
dropped extra was a false positive (`pool-drift-watchdog.sh` `cd`s into
`~/repos/bowman-world-cup-pool` first, and is not scheduled).

After: `runtime_scripts_symlink=ok:scripts/mini`, `hermes doctor` clean, gateway
untouched (same PID 72393), `hermes-weekly-health.sh` run live → exit 0. Live
tree went 125 → 98 files, dropping the 26 unreferenced MacBook extras and the
dead symlink.

### 🔴 The memory symlinks are blocked by repo corruption, not by the rebuild

The same regression hit canonical memory — `~/.hermes/memories/MEMORY.md` and
`USER.md` are plain files, not symlinks. **But do not just flip them.** The repo
copies contained **committed, unresolved git merge conflict markers**:

```
memory/MEMORY.md   <<<<<<< 35   ======= 39   >>>>>>> 41
memory/USER.md     <<<<<<<  1   ======= 17   >>>>>>> 47   ← whole file
```

Introduced by `0613fe2 auto(memory): sync canonical memory` and **already
pushed**. `scripts/commit-memory.sh` staged both files blindly with **no
conflict-marker guard**, which is how it happened. ✅ **Guard added 2026-08-21**
— it can no longer recur; see below.

The **live files are clean** (0 markers). Symlinking as-is would have injected
`<<<<<<< Updated upstream` into the agent's memory on every prompt. This inverts
the usual assumption: here the live copy is healthy and the repo is damaged.

It also explains the budget error — `USER.md` reads 5,061/4,000 because it holds
*two* full competing versions, not because it grew.

**✅ `MEMORY.md` fixed (2026-08-21, `6fc8368`).** Its two sides were unrelated
topics, not competing edits, so all three facts were kept (vault-ops stale route
· software-testing vault isolation · A2A auth model 2026-08-16). Purely
structural: 3 marker lines removed, `=======` replaced by the file's own `§`
separator, no fact text edited. 41→39 lines, 20 facts, gate now
`ok: 7,636/8,000`.

**❌ `USER.md` still conflicted.** Whole-file, no shared region — three complete
versions of Stephen's profile (A: 8 consolidated facts · B: 15 detailed, with
internal duplicates · LIVE: 17, a third lineage). Each holds things the others
drop entirely. This is an editorial choice, not a merge, so it was left for
Stephen. Full three-way comparison: `~/hermes-memory-three-way.md`.

**✅ Live-only facts unioned into `MEMORY.md`** (2026-08-21, `da2c1c5`).
20 repo + **17 live-only = 37 facts**; no pre-existing fact removed (verified).
Recovered: A2A port map (9900/9901/9902), hindsight ports 8889/8890/8891 and its
read-only probe surface, the `ai.hermes.gateway.plist` `HERMES_HOME` requirement,
fleet `kanban.db` path, Fish Audio voice-clone IDs, delegation model chain, the
VPS `--profile research-dick` gotcha. These would have been **destroyed** the
moment the symlink was restored.

Two deliberate exclusions:
- **4 duplicates** already in the repo in different wording (CFB gate, work
  style, executions.db, LCM canary).
- **`Fleet bot handoff via CLI … -c "Bot Chat"`** — the **retired pre-A2A**
  pattern. Re-adding it would contradict the soul resync in `19c4593` and teach
  the fleet a superseded invocation. Say so if you want it back anyway.

> ✅ **Budget resolved (re-verified 2026-08-21).** `MEMORY.md` is **8,065 chars**
> and `fleet/memory_budget.py` exits 0. An earlier revision of this handoff said
> 13,612 / 70% over — that was true at the time of the union and has since been
> pruned. Do not go chasing a pruning pass; it is done.
>
> The gate is still **manual**, wired into no scheduled script, so nothing pages
> if it drifts again. Worth wiring into cron at some point.

Also unreconciled: the repo says fleet routing is `deepseek-v4-flash:0731`,
the recovered delegation fact says `deepseek-v4-pro:0813`. Different scopes
(default routing vs delegation chain) so both were kept, but the version skew
suggests one is stale.

**The memory symlinks stay off until `USER.md` is clean.**

### ✅ Conflict-marker guard added to `commit-memory.sh` (2026-08-21)

`7b0d490` (guard) + `a9dc3c0` (tests), both pushed to `origin/main`.

The markers `0613fe2` published read `<<<<<<< Updated upstream` /
`>>>>>>> Stashed changes` — **stash-pop format, not rebase format**. So the
source is the `rebase --autostash` at `commit-memory.sh:153` (or fleet-pull's
own stash) leaving a conflicted worktree that the script then staged blindly.

The guard sits right after the existence/symlink loop and partitions
`MEMORY_PATHS` into `CLEAN_PATHS` and `QUARANTINED`. `CLEAN_PATHS` — not
`MEMORY_PATHS` — then drives the dirty check and the `add` / `commit --only`
block, so a dirty-but-conflicted file cannot trigger a run that commits nothing.

Four design points, each load-bearing:

| Choice | Why |
|---|---|
| Detect on `^<<<<<<< ` and `^>>>>>>> ` **only** | A bare `=======` is a legitimate markdown heading underline. `memory/USER.md` contains one **today** — detecting on it would false-positive on healthy memory. |
| **Quarantine per file, never abort the run** | `USER.md` is conflicted right now. A whole-run abort would break every scheduled memory sync until Stephen resolves it — which is exactly why the earlier plan said to wait for `USER.md`. Per-file quarantine removes that dependency. |
| Empty `CLEAN_PATHS` → warn and **exit 0** | Nothing publishable is a skip, not a failure; a non-zero exit would page through `fleet-pull.sh`. |
| Length-guard every array expansion | The script runs under `set -u`, where `"${arr[@]}"` on an empty array **errors on bash 3.2** — and `/bin/bash` here is 3.2.57. |

**Tested before committing**, in an isolated clone with its own bare remote, under
both bash 5 and `/bin/bash` 3.2:

- conflicted `USER.md` + modified `MEMORY.md` → `USER.md` quarantined and left
  dirty, `MEMORY.md` committed alone, exit 0
- every file conflicted → loud warning, **exit 0**, HEAD unmoved
- only the conflicted file dirty → no commit at all, exit 0
- markdown heading underline → not detected, committed normally

The repo already had `tests/test_memory_commit.py` (7 tests, pytest; not in any
venv on this box — run it with a throwaway `python3 -m venv` + `pip install
pytest`). All 7 still pass, and **4 new cases** were added for the guard →
**11 passing**.

Confirmed against the real files: `memory/USER.md` has markers at lines 1 and 47
and would be quarantined; `memory/MEMORY.md` has 0 and would commit normally.

### ✅ Cron registry: the 4 entries added (2026-08-21, `a1ea878`, pushed)

| id | name | schedule | script | deliver |
|---|---|---|---|---|
| `73c4bf2f84c1` | achievements-daily-scan | `0 9 * * *` | `achievements-cron.sh` | origin |
| `086812eb2000` | backlog-nudge | `0 10 1 * *` | `backlog_nudge.sh` | discord |
| `f5077afcfbab` | Fleet morning digest | `30 7 * * *` | `fleet_digest_collect.py` | discord:1465376057107546367 |
| `fa6819395503` | hermes-doc-alignment | `0 9 * * *` | monitor `hermes_doc_watch.py` | origin |

`registry.json` encodes an **alerting contract** (`alert_path`, `silent_policy`,
`expiry`), so nothing was guessed — every field was **read off the job's own
snapshot delivery plus the source of its script**:

- `achievements-cron.sh`'s header comment states it outright: *silent when
  nothing new; posts unlocks to Discord itself; exit 1 on failure so the fleet
  watchdog's cron-error paging catches it.*
- `backlog_nudge.sh` prints on **every** code path, including
  `Fleet queue: clear` — so it is **never** silent.
- `fleet_digest_collect.py` is the push half of the phone console, and its
  prompt requires a line even on a quiet window → **always delivers**.
- `hermes_doc_watch.py` runs as `--monitor-script`: an unchanged payload
  suppresses the agent, and the prompt returns exactly `[SILENT]` on `BASELINE`
  or no change. It has **no plain script** (snapshot `script` is `null`), so the
  entry omits the `script` field to match.

`expiry` is `"standing"` for all four — each is an enabled recurring job with no
end date, the same treatment the other 17 standing jobs already get. That is the
one field derived by convention rather than from source; say so if any of them
should carry a review date instead.

**Verified:** validator 39 → 35 errors, and a before/after diff confirms the
delta is *exactly* these four `missing registry entry` lines with **nothing new**
introduced. Registry-specific tests **23/23**. Full suite is byte-identical to
baseline (4 failed · 398 passed · 19 errors both with and without the change —
all in `fleet_pull_cli`, `fleet_watchdog`, `reconcile_obsidian_launcher`, none
registry-related; pre-existing, and `pyyaml` must be installed to collect at all).

> ⚠️ **The unregistered count had drifted since this was written** — it is **13**
> live Mini jobs, not the 17 implied earlier. The other **9** are pre-existing
> debt and were left alone: `45caa71d3aa0`, `32ccc4625bf2`, `a4258d37c3cb`,
> `24f30aee4a3c`, `b5ea2bd9356f`, `ff638b2bd5a8`, `8a8a24d4f45b`,
> `52340d000ecd`, `96c29c73eab5`. The last four are **`cfb-model-lab`** jobs
> (`workdir` points there) — do not touch.

One unrelated thing the run surfaced, still open:
- Validator errors include `platform_toolsets.cron no_mcp conflicts with named
  mcp field:a2a` (mini **and** vps) and `registry entry not present in live
  snapshots: a533b84bbe86`.

### ✅ `hermes-release-watch` fixed — the `watchers` skill was never restored (2026-08-21)

`b5ea2bd9356f` was failing live (`last_status: error`, `failure_streak: 1`):

```
can't open file '~/.hermes/skills/devops/watchers/scripts/watch_github.py'
```

**Root cause — B4's skills restore used a damaged source.** B4 restored
`skills/` from `~/.hermes-old-20260821/skills/`, which is the **post-incident**
old home. That copy had already lost the skill: its `skills/devops/` contains
**only `sdlc-review`**. The pre-incident backup still has
`~/.hermes/skills/devops/watchers/` intact, which is what the job was written
against.

`watchers` is an **opt-in extra** — it ships at
`hermes-agent/optional-skills/devops/watchers/` and is *never* seeded into
`skills/` by default, so no amount of runtime rebuilding would have replaced it.
Nothing was broken about the script; the skill it calls into was simply absent.

**Fixed by content, not by path.** All 5 files of the optional-skills copy are
md5-identical to the pre-incident backup's copy, so the install's own canonical
source was used (it has no stale `__pycache__`):

```bash
cp -R ~/.hermes/hermes-agent/optional-skills/devops/watchers \
      ~/.hermes/skills/devops/watchers
```

Verified: `SKILL.md` `251553a5…` · `_watermark.py` `d87028e3…` · `watch_rss.py`
`173a4b8c…` · `watch_http_json.py` `32239ea8…` · `watch_github.py` `07c13772…`
— all three copies (new / upstream / pre-incident backup) agree.

Then ran the job **through cron's exact path**
(`~/.hermes/scripts/watch_hermes_releases.sh`): **exit 0**, silent, and it
created `~/.hermes/watcher-state/hermes-agent-releases.json` seeded with **28**
release IDs. Silence is correct — `_watermark.py`'s contract is *"First run:
record all IDs from the fetched batch, emit nothing"*, so there was no
notification to consume. The watermark had **never existed** in this home,
confirming the job had not succeeded once since the cutover.

`hermes skills list` now shows `watchers · devops · local · enabled`, so the
agent-mode `pocock-skills upstream watch` job (`a4258d37c3cb`, which declares
`skills: ["watchers"]`) gets it back too — it had been reporting `ok` while
silently missing its skill.

Nothing to commit: `~/.hermes/skills/` is live-only state, untracked by
`hermes-fleet`. The job's stored `last_status` stays `error` until its next fire
(09:15) rewrites it.

> 🔴 **Bigger finding — the custom skill library was never restored, fleet-wide.**
> The same damaged-source mistake cost far more than one skill:
>
> | | pre-incident backup | live now |
> |---|---|---|
> | root `~/.hermes/skills/` | **349** skill dirs | **84** |
> | each profile's `skills/` | **~357–382** dirs | **0** — all 22 empty |
>
> The 84 live dirs are essentially the **stock bundled set**. Missing are
> Stephen's curated and hand-written skills — `devops/watchers`,
> `engineering-flows/*` (~22, incl. `writing-for-agents`, `grill-with-docs`),
> `design/*` (~15), `code-review/*`, `research/*`, `creative/*`, plus the
> `.archive/` and `.curator_backups/` history.
>
> This is recoverable — `~/agent-config-backup-2026-08-20` holds all of it — but
> it is a **large restore across 23 locations** and a deliberate call about what
> should come back (the archive probably should not). **Not done unilaterally.**
> Note `pocock-skills upstream watch` grades a local library at
> `~/.hermes/skills/engineering-flows/` that currently **does not exist**.

### ✅ Custom skill library restored (2026-08-21)

Root **85 → 285 skills** (81 builtin + 197 local), then propagated to all 22
profiles — each now matches root at 285. `hermes doctor` clean. `~/.hermes`
grew 1.1 G → 2.7 G; 178 G free.

**Additive only — nothing was overwritten or deleted.** That mattered: the live
tree already held **43 skills that postdate the backup**, including a newer
`test-driven-development` and the `session-handoff` skill. A mirror-style
restore would have been a regression, not a recovery.

Three traps in the backup's `skills/` tree, all handled:

1. **`.archive/` (89 entries) — skipped.** Archived deliberately; restoring them
   would resurrect retired skills.
2. **The nested `skills/skills/…` tree is NOT a plain duplicate.** It looks like
   a copy-into-itself artifact and the obvious move is to skip it wholesale —
   that would have silently lost **51 skills**. Of the entries missing from live:
   33 identical twins, **8 differing**, **43 unique with no twin anywhere**. It
   was flattened to `<category>/<skill>` with collisions resolved in favour of
   the live copy.
3. **One genuine conflict: `research/grounded-citations`.** Live (11,526 B) is
   *newer* than the backup (11,231 B) — it carries expanded guidance on parallel
   subagents contaminating a shared citation ledger. Live kept.

**Profiles do not inherit root skills.** The builtin sync carries only the 81
bundled skills, so the 197 custom ones were root-only and invisible to every
profile — `pocock`'s soul names `bot-factory-map`, `loop-me` and
`grill-with-docs` directly and could not have loaded any of them. Six profiles
(`curator`, `design`, `frontend`, `scoreboard`, `scout`, `workbench`) had **0**
skills entirely. The backup confirms the historical shape: real copies, ~12 MB
per profile, `engineering-flows` included. Reproduced; 4,672 skill dirs copied.

The full Pocock spine is back under `engineering-flows/` (22 skills):
`grill-with-docs` · `to-spec` · `to-tickets` · `implement` · `tdd` · `wayfinder`
· `codebase-design` · `domain-modeling` · `writing-for-agents` ·
`bot-factory-map` · `loop-me` · `ask-matt`.

### 🔴 New finding: the 8 fleet-owned skills are tracked but never deployed

`hermes-fleet` tracks 8 of its own skills — `docker-worktree-sandbox`,
`fleet-disk-cleanup`, `hermes-fleet-audit`, `hermes-fleet-maintenance`,
`obsidian-cli`, `one-thing-rule`, `squad-leader`, `study-queue`. **All 8 are
absent from the live install.**

Seven postdate the backup and exist *only* in the repo, so no restore path
reaches them. Grepping the repo found **no deploy mechanism** — nothing in
`fleet-pull.sh` or the runbook copies `skills/` into `~/.hermes/skills/`, and
the repo stores them flat while the live tree is categorised.

Left undeployed deliberately: the target category is a guess, and guessing
placement in a config-as-code tree is how drift starts. Decide the category
(`fleet/`?) and add a deploy step, rather than hand-copying once.

### Still open from B5

- ~~Push the three commits~~ — **done**, `origin/main` at `8d0e215`.
- ~~Restore the scripts symlink~~ — **done 2026-08-21**, gate green.
- ~~Prune `MEMORY.md` back toward 8,000 chars~~ — **done**, 8,065 chars, gate
  exits 0. Consider wiring the manual gate into cron so drift pages.
- ~~Add a conflict-marker guard to `commit-memory.sh`~~ — **done 2026-08-21**
  (`7b0d490` + `a9dc3c0`, pushed). It quarantines **per file**, so it works with
  `USER.md` still conflicted rather than needing to wait for it.
- Resolve `memory/USER.md` (whole-file conflict), then restore the
  `~/.hermes/memories/*` symlinks.
- ~~Add the 4 cron-registry entries~~ — **done 2026-08-21**, `a1ea878`, pushed.
- ~~Migrate `pliny` to A2A~~ — **done 2026-08-21**, `abfbb93`, pushed. It also
  carried the messaging section **twice**; both were replaced by the single
  canonical block from `19c4593`. pliny now matches its siblings exactly
  (3 Bot Chat refs, all emergency-fallback context · 8 a2a · one section).
  11,443 → 8,090 B. **All 18 personas are now on A2A.**
- ~~Restore the custom skill library~~ — **done 2026-08-21**. Root 85 → **285**
  (81 builtin + 197 local, 174 enabled), propagated to all 22 profiles. See below.

Keep `~/.hermes-old-20260821` and `~/agent-config-backup-2026-08-20`.

## Verified reference

Something wrote a 106-byte `config.yaml` to the **old** home at 22:17 on 2026-08-20 unprompted (a `model:` block pinning `gpt-5.6-luna` via `openai-codex`). Still unexplained. Re-verify state each session.

| Fact | Value |
|---|---|
| Hermes | v0.20.4 (2026.8.18), git install, latest config version 37 |
| `.env` keys | **62** (not the 243 quoted in earlier reports) |
| OAuth providers | 6 · MCP tokens: 12 |
| Snapshot source | `~/agent-config-backup-2026-08-20/Users/stephenbowman/.hermes/state-snapshots/20260819-044836-pre-update/` — 183 files, 915 MB, config v36, 27 cron jobs, **no SOUL or memory files** |
| Old home | 1,153 sessions · `state.db` 1.1 GB · `lcm.db` 643 MB · 78 memory facts |
| Memory `.md` in backup | 246 files — root pair already restored; the rest belong to profiles (B3) |
| 106 B config re-check | **unchanged**, still stamped 22:17 Aug 20 — has not recurred (re-verified Aug 21) |
| Profile snapshot | `backups/nous-cutover-20260820-124254/` — 22 profiles, real configs + souls + `.env`, pre-incident |
| `hermes curator rollback --list` | "No curator snapshots yet" — path is empty |

---

## Progress log

- [x] **B0 — Stand up and harness** — backup 1.8 GB, fleet restored, home created, isolation verified
- [x] **B1 — Credentials** — 10 items hash-verified, 62 keys, signed in
- [x] **B2 — Configuration** — v37, doctor clean, SOUL 8,690 B, 22 cron jobs, memory + holographic provider live
- [x] **B3 — Profiles (22)** — 22 built from the pre-incident snapshot, all v37, real souls, doctor clean; `ops` soul written fresh
- [x] **B4 — Cutover** — swapped, runtime rebuilt, gateway live with 0 profiles skipped; `.env` listener-key error found and fixed
- [x] **B5 — Commit back** — 3 commits, tree clean, not pushed; machine identity corrected to `mini`; souls resynced to A2A; script-symlink regression found
- [x] **Scripts symlink restored** — `~/.hermes/scripts -> scripts/mini`, deployment gate green, doctor clean
- [x] **`memory/MEMORY.md` conflict resolved** — all three facts kept, gate green, pushed `6fc8368`
- [x] **`MEMORY.md` live-only facts unioned** — 37 facts, nothing lost, pushed `da2c1c5`; now over budget
- [x] **`commit-memory.sh` conflict-marker guard** — per-file quarantine, 11 tests passing, pushed `7b0d490`+`a9dc3c0`
- [x] **`hermes-release-watch` fixed** — `watchers` skill restored from the install's own optional-skills, md5-verified, ran green; exposed that the custom skill library was never restored
- [x] **4 cron-registry entries registered** — fields derived from each script's own source, validator 39→35, pushed `a1ea878`
- [x] **`pliny` migrated to A2A** — duplicate messaging section collapsed, canonical block applied, pushed `abfbb93`; all 18 personas now on A2A
- [x] **Memory budget verified green** — 8,065 chars, `fleet/memory_budget.py` exits 0
- [x] **Custom skill library restored** — root 85→285, all 22 profiles at 285, additive only; 51 skills nearly lost to a deceptive nested tree
- [ ] **B6 — Resolve `memory/USER.md` (Stephen's editorial call), restore memory symlinks, restore the custom skill library, then retire the old home** ← next
