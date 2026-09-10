> **STATUS (2026-09-09):** Conditional — applies while B6 (memory/USER.md three-way choice) is unresolved; verify committed memory/ files before acting.

# Hermes canonical memory — three-way comparison

Generated 2026-08-21. **No files were modified.** `~/.hermes/memories/` and `memory/` are untouched.

## Why this exists

`memory/MEMORY.md` and `memory/USER.md` in `hermes-fleet` contain **committed, unresolved git merge conflict markers**, introduced by `0613fe2 auto(memory): sync canonical memory` and already pushed. `scripts/commit-memory.sh` stages both files blindly with no conflict-marker guard.

The live files at `~/.hermes/memories/` are **clean** (0 markers). Restoring the memory symlinks as-is would inject `<<<<<<< Updated upstream` into the agent's memory injection on every prompt.

Legend: **LIVE** = clean live file · **A** = `<<<<<<< Updated upstream` side · **B** = `>>>>>>> Stashed changes` side · **SHARED** = repo content outside the conflict.


---

# MEMORY.md

| version | facts |
|---|---|
| LIVE (clean) | 22 |
| SHARED (outside conflict) | 17 |
| A — Updated upstream | 2 |
| B — Stashed changes | 1 |

### in LIVE + SHARED

- **[LIVE]** CFB Model Lab commits require the four-step gate: npx tsc --noEmit, npm run lint, npm run build, npm run test. All four must pass. Preserve the relevant parent branch for dependent work.
- **[SHARED]** CFB Model Lab gate sequence: before every commit, run 4-step gate: (1) npx tsc --noEmit, (2) npm run lint, (3) npm run build, (4) npm run test. All must pass. Branching for dependent features: branch from last relevant parent (e.g., WS3 needing PickCell from WS1 branches from `hermes/ws1-trust-lane`, not master or ws2).

### ⚠️ ONLY IN A

- **[A]** vault-ops SKILL.md is stale — capture route table missing `infra` kind (routes to `20-Areas/Hermes Brain/infra/`). Was added to adapter + contract this session but skill not patched (user-owned, blocked). Recommend foreground patch.

### ⚠️ ONLY IN A

- **[A]** software-testing SKILL.md is missing the vault-test isolation pattern: set OBSIDIAN_WRITER_VAULT env to tmp_path via autouse fixture so tests never touch the real vault. Proven pattern from vault_capture test suite (user-owned skill, blocked from patching — recommend foreground patch).

### ⚠️ ONLY IN B

- **[B]** A2A auth model (verified 2026-08-16): cards at /.well-known/agent.json are public. Echo via JSON-RPC message/send to card root URL with Authorization: Bearer. a2a_agents registry keys = peer names (mini, macbook) with ${A2A_AUTH_X} env refs. CRITICAL: A2A_AUTH_MINI is Mini's outbound-TO-me token — will NOT auth me TO mini (401 -32050). Each direction has its own credential. A2A_PEER_TOKENS on VPS = inbound whitelist. Partial-echo pattern: peer returns literal template tail (_builder) or raw JSON-RPC envelope as reply text = peer's model/template generation broken, NOT unreachable — get 2+ data points before flagging.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Stephen's work style: ADHD, capture-first (GTD+Kanban), piece-by-piece with confirmation. Avoid the planning trap: research → docs with no code is failure; ship prototypes. He wants direct, unvarnished assessments, plain-language explanations, adversarial go/no-go review before building, and no permission loops when a safe fix is obvious. Check what exists first. Provider/model facts change fast; verify live. OpenRouter is emergency-only. Native Hermes features and the smallest durable solution beat custom infrastructure.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Fleet routing: three machines via Tailscale. MacBook = interactive workstation/dev/design and A2A peer (M3 Pro, 36GB); Mac Mini = always-on home gateway, Discord owner (M1, 16GB); VPS = 24/7 infra/research gateway. Telegram is inactive/decommissioned. Route sports/CFB to bookie, markets to analyst, hosts/services to sysop, voice to studio, outcomes to scoreboard, code to builder, research/vault to scribe, design to design. Use A2A for host-bound work and bot handoff for a specialist lane.

### ⚠️ ONLY IN LIVE

- **[LIVE]** A2A mesh: VPS research-dick on 9900, Mini on 9901, MacBook on 9902 through localhost SSH tunnels; tailnet ACL is SSH/HTTPS only. Mini owns Discord; other machines deliver outbound alerts through the fleet webhook. Do not add messaging gateways to MacBook or VPS. Auth: bearer tokens via A2A_AUTH_MINI/A2A_AUTH_RESEARCH_DICK/A2A_PEER_TOKENS env vars. MacBook SSH user is stephen_bowman (underscore).

### ⚠️ ONLY IN LIVE

- **[LIVE]** Research-dick is a read-only git distribution on the VPS. Pull updates there; publish by bundle/scp to Mini and push from the authorized checkout. Never push with the VPS deploy key. Check branch and unpushed commits before fleet pushes because parallel fleet sessions may be working. VPS gateway service runs under systemd as user 'hermes'; authoritative profile is /home/hermes/.hermes/profiles/research-dick. Plain `hermes status` without --profile reports from default home and produces false negatives — always pin the profile.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Hindsight: Mini proxy 8889, daemon 8890; VPS research profile reaches it through the 8891 SSH tunnel. Read-only probe surface: /health, /version, bank_id/stats, bank_id/documents?q=..., bank_id/memories/list?q=.... Never touch retain/recall/POST/DELETE.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Gateway operations: Mini/MacBook restarts use the Python launchctl indirection because shell content trips the lifecycle guard; VPS restarts use root@hermes-gateway. Multiplexed profiles need api_server.enabled=false; an API_SERVER_KEY in a profile .env can force-enable it. VPS gateway settings live in the profile config; never scp the full MacBook config to VPS.

### ⚠️ ONLY IN LIVE

- **[LIVE]** LCM silent-fallback canary: verify agent.log contains "registered context engine: lcm" and lcm.db has more than a handful of messages. If both fail, alert; a configured context.engine=lcm is not proof the plugin loaded.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Execution and verification: rows may land in either main or profile executions.db because of multiplex dispatch races; profile DBs are real. Main state DB is WAL-backed and read-only URI reads can fail during write bursts, so use a normal connection when appropriate. For infra, verify the full chain: API, tunnel, Hermes, resilience, and multimodal paths—not just a report.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Voice/audio: Fish Audio TTS is user-owned; Groq whisper-large-v3 is the preferred transcription path. Charlie Kirk private clone ID: 94d90e249b01406c9fd735494d025ad0; Charlie Marsh v2 default: 75bbe51524254650897b13d3032b267d. Do not use a real-person voice clone for threats, violence, or political rhetoric targeting real groups; offer a fictional alternative. Andy Cohen content has an absolute anti-semitism guardrail.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Refusal boundary: when Stephen escalates from appeal to command to a technical workaround, hold the legitimate safety boundary and offer one lawful alternative. Never fabricate data, citations, research, tool results, or upload success.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Config gotchas: hermes config set can serialize list keys as strings, so use Python YAML edits for lists. Resolve toolsets as enabled first, then subtract disabled_toolsets. WEBHOOK_SECRET from the environment overrides config at boot. Tailscale SSH aliases can loop (mac-mini points to itself); macbook means stephen_bowman@. auth.json credential pool has active_provider and suppressed_sources that override .env silently. MacBook root config has stale model.base_url/api_key_env pointing to ollama when provider is openai-codex. Cross-machine fallback drift exists between Mini and MacBook. Keep user preferences and fleet truth here, not transient incident logs. Plugin disabled-list fix (2026-08-17): cron_providers/chronos moved plugins.enabled→disabled on Mini — fork plugin calls PluginContext.register_cron_scheduler which the CLI lacks; cron.provider='' so it was pure boot noise. Skill patch for it was security-scanner blocked (benign content) — pending manual confirm if ever needed.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Obsidian vault: 01-Knowledge-Base-v2 uses encrypted Obsidian Sync; active reference notes belong in 20-Areas/Hermes Brain/. Before sync operations, create a ditto backup at ~/01-Knowledge-Base-v2.pre-sync-backup.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Mac Mini Discord gateway: ~/Library/LaunchAgents/ai.hermes.gateway.plist must export HERMES_HOME=/Users/stephenbowman/.hermes or gateway boots with no platforms/sockets. Inspect both user/ and gui/ domains.

### ⚠️ ONLY IN LIVE

- **[LIVE]** VPS hermes-status: bare `hermes status` uses default home, NOT research-dick. Always use `--profile research-dick`.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Fleet Kanban direct access: the fleet board lives at ~/.hermes/kanban/boards/fleet/kanban.db (SQLite). When the `hermes kanban` CLI is runtime-gated in a child context, read directly with sqlite3 (file: URI, mode=ro). Schema: tasks table with id, title, status, priority, assignee, etc.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Docs watchers (Mini): hermes_doc_watch.py (repo repos/hermes-fleet/scripts/mini/, symlink ~/.hermes/scripts/), cron fa6819395503 09:00 + release watcher b5ea2bd9356f 09:15, read-only monitor mode. A3 hindsight checker uncommitted in worktree ~/.hermes/worktrees/hindsight-observability-a3. No second watcher/auditor profile/fleet expansion without approval.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Delegation: ollama-cloud/deepseek-v4-pro:0813/max (free). Parent: openai-codex/gpt-5.6-luna. Fallback: zai/glm-5.3 → ollama Pro → ollama Flash → openrouter. reasoning_effort=max, 40 iterations, 4 children, depth 2. NO-GO on Pro→Flash switch without A/B replay. Ship translation, not telemetry — internal diagnostics aren't human deliverables.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Fleet bot handoff via CLI: `hermes -p <agent> chat --in ~ -c "Bot Chat" -Q -q "Message from 🤖 Hermes (@hermes): <composed message>"` sends an @mention to a fleet bot. Session name "Bot Chat" is the convention. If "No session found matching 'Bot Chat'", send without -c then rename the session. Run background=true + notify_on_complete; relay replies attributed to that bot. Cap concurrent handoffs at 3.

### ⚠️ ONLY IN LIVE

- **[LIVE]** LLM-as-a-verifier pilot remains offline: do not wire it into Builder routing until a stronger logprob-capable verifier beats the frozen 20-trace baseline.

### ⚠️ ONLY IN LIVE

- **[LIVE]** MacBook system-audit sacred set: caffeinate/no-sleep policy, Tailscale, 1Password, Hermes gateway, A2A SSH tunnels on 127.0.0.1:9900/9901, ~/.ollama, /private/var/vm/sleepimage, and agent-config-backup. Never sleep while plugged in; never kill or disable these without explicit approval.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Hermes Desktop Bot Mode is active as a bundled plugin: Settings → Plugins shows Desktop plugins → Bots, bundled, enabled; the BOTS tab and live roster render. The old ~/.hermes/desktop-plugins/hermes-bots checkout is archived/redundant; do not delete it without approval.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Stephen's work style: ADHD, capture-first (GTD+Kanban), piece-by-piece with confirmation. Planning trap: research → docs with no code = failure, ship prototypes instead. Wants direct unvarnished assessments, not condescension. Adversarial go/no-go gate before building. Native Hermes features over custom infra. Obsidian vault: ~/01-Knowledge-Base-v2/. OpenRouter emergency-only. Provider/model facts change fast — verify live. Technical self-assessment: not technical, doesn't understand backend, often breaks things when exploring Hermes. Wants autonomous fixer agent to keep system healthy and drift-free. Strong preference: CHECK WHAT EXISTS FIRST (docs, skills, GitHub) before building custom. Grants authority to fix issues without permission loops ("it's all good"). Uncertain if current config is correct, wants validation. Calibration pattern: when Dick declares a premature verdict and Stephen pushes back with domain context, stop, acknowledge, and research properly before doubling down. Surface metrics can be wrong; adversarial literature search reveals the real criterion (e.g., ATS accuracy vs calibration in betting). Stephen's domain instincts are reliable. /learn command: explicit skill extraction trigger from conversation.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Mini MCP (9 enabled, 2026-08-06): zai-vision/web-search, obsidian-kb, shadcn-mcp, bowman-pool-readonly, code-review-graph, github, bible-mcp, mobbin (OAuth PKCE, api.mobbin.com/mcp, 3 tools, paid plan). zai-web-reader disabled. `hermes tools list` shows all configured servers regardless of enabled flag — check config. Gotcha: `hermes mcp add --auth oauth` non-interactive skips `auth: oauth` (mcp_config.py:508) — patch config, then `hermes mcp login` works. Config key = mcp_servers. A2A timeout for desktop control: 300s via hermes config set gateway.platforms.a2a.extra.timeout 300.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Fleet routing: all = ollama-cloud/deepseek-v4-flash:0731. Fallbacks: MacBook/VPS zai/glm-5.2→gemini-2.5-flash; Mini gemini-2.5-flash only. MacBook MLX: Qwen3.8-27B-4bit, ~/.mlx-venv, port 8090, provider mlx-local.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Bitwarden encrypted cache enabled (86400s stale tolerance). 18 secrets injected on boot (verify with hermes config get model.default | grep applied). Redundant plaintext keys commented out in .env — keep BWS_ACCESS_TOKEN bootstrap + config values. Note: env vars land in agent shell but NOT the gateway process env (Bitwarden injects per-layer) — check caller shell auth via os.environ, not /proc/<gw>/environ.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Hindsight: Mini daemon launchd port 8890, LLM zai/glm-4.5-flash. Runs from `uv tool install hindsight-api` (~/.local/share/uv/tools). VPS: local_external → 127.0.0.1:8891 via SSH tunnel.

### ⚠️ ONLY IN SHARED

- **[SHARED]** SSH creds drift both ways: 2026-08-16 VPS→Mini rejected, 2026-08-18 OK (stephenbowman@stephens-mac-mini-1, default key, Tailscale DNS name); VPS→MacBook rejected → hop via Mini (stephen_bowman@100.95.145.37). Probe A2A agent-card on 9901 for liveness.

### ⚠️ ONLY IN SHARED

- **[SHARED]** research-dick profile = git distribution (BowmanStephen/hermes-research-dick, v1.2.0 since 2026-08-05). Deploy key (hermes_research_dick_deploy, SSH alias github.com-hermes-research-dick) is READ-ONLY — VPS pulls updates, never pushes. Publish path: commit on VPS → git bundle → scp to Mini → gh clone/push. Remote must use alias host or fetch fails.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Kanban boards are PER-MACHINE (~/.hermes/kanban/boards/); crown-ops lives on the Mini. Worker profiles pruned 2026-08-05 — recreate via

### ⚠️ ONLY IN SHARED

- **[SHARED]** LCM silent fallback detection pattern: Hermes 2.0 can configure context.engine: lcm but gateway may silently fall back to compressor if plugin fails to load. Canary probe in fleet-watchdog.sh: check grep "registered context engine: lcm" in agent.log AND sqlite3 lcm.db message count (<10 = problem). If both fail → alert. All 3 machines on LCM since 2026-08-05 (Mini, MacBook, VPS research-dick) — plugin = standalone repo stephenschoettler/hermes-lcm cloned into runtime ~/.hermes/plugins/ at v0.20.0 (NOT in source tree). MacBook enable: clone + config edit (venv python) + file-shipped launchctl kickstart (guard blocks direct restart).

### ⚠️ ONLY IN SHARED

- **[SHARED]** MacBook MCP (8 enabled, 2026-08-05): github, obsidian-kb, zai-vision, zai-web-search, shadcn-mcp, desktop-commander, playwright-mcp, bowman-pool-readonly(disabled). Disabled: zai-web-reader, zai-zread, comfy-cloud, figma, linear. MacBook config edits need venv python (~/.hermes/hermes-agent/venv/bin/python3).

### ⚠️ ONLY IN SHARED

- **[SHARED]** prime-agent v0.7.2 fleet-wide (2026-08-15): package PULLED from npm (404 all versions) — upgrades now happen by rsyncing the self-contained bundle (~264MB) from a machine that has it: lib/node_modules/prime-agent/. Paths: Mini ~/.n8n-global/bin, MacBook ~/.local/bin, VPS /home/hermes/.npm-global/bin. VPS Node 24 user-local (system 20 untouched); --version prints STDERR. Watchdog drift probe 30m (EXPECTED_PRIME_AGENT in fleet-watchdog.sh).

### ⚠️ ONLY IN SHARED

- **[SHARED]** Ollama Cloud fleet refresh (2026-08-14): 9 live models, aliases updated fleet-wide. Pattern: probe catalog → test → backup → yaml edit via python (NO sed) → config check → chat test → propagate.

### ⚠️ ONLY IN SHARED

- **[SHARED]** (exec rows can land in EITHER main or profile executions.db — check BOTH; profile dbs NOT vestigial. Main db WAL+1000-row cap; ro-uri reads fail during write bursts — plain rw connect works)

### ⚠️ ONLY IN SHARED

- **[SHARED]** Verification expectation: "test everything end to end to make sure it works" — wants full-chain validation, not partial checks. For infra work, exercise every path (API, tunnel, Hermes, resilience, multimodal) before calling it done.

### ⚠️ ONLY IN SHARED

- **[SHARED]** Mini agent-browser 0.34.0 via brew (Chrome 152 CfT in ~/.agent-browser/browsers). Gotcha: ~/.n8n-global/bin precedes /opt/homebrew/bin in Mini PATH — npm-global copies shadow brew; check `which -a` after installing both. Tool has own skills: `agent-browser skills get core`; use named sessions per task.

### ⚠️ ONLY IN SHARED

- **[SHARED]** 2026-08-15: hermes-agent auto-updated on all 3 machines; the 3 carried defensive fixes LANDED UPSTREAM — patch set retired, manifests re-pinned, gates GREEN (30946b0). Gotchas: multiplexed profiles need explicit `api_server: enabled: false` in config.yaml (API_SERVER_KEY in profile .env force-enables it); fleet repo replicates Mini→GitHub→pull on remotes; ssh 'macbook' = stephen_bowman@, 'mac-mini' from Mini loops to itself.


---

# USER.md

| version | facts |
|---|---|
| LIVE (clean) | 17 |
| SHARED (outside conflict) | 0 |
| A — Updated upstream | 8 |
| B — Stashed changes | 15 |

### in A + B + LIVE

- **[LIVE]** Sports betting: straight bets only, no parlays/boosts, research first.
- **[A]** Sports betting: straight bets only; no parlays or boosts; research first.
- **[B]** Sports betting rule: straight bets only. No parlays or boosted odds. Research first.

### in A + LIVE

- **[LIVE]** Identity: UX designer → AI Product Engineer, 7+ yrs (United, Google, J&J, CA DHCS); AI Workstream Lead at pharma enterprise (Power Platform stack). Builds with Figma, React, Python, Rust, Obsidian, Vercel, agent tooling. GitHub: BowmanStephen. Lake Bluff, IL. bowman.stephen92@gmail.com. Telegram persona @Dick_Trickle_Bot (named for NASCAR's Dick Trickle).
- **[A]** Identity: UX designer -> AI Product Engineer. Builds with Figma, React, Python, Rust, Obsidian, Vercel, and agent tooling. GitHub: BowmanStephen. Telegram persona: @Dick_Trickle_Bot.

### in A + LIVE

- **[LIVE]** Vault: correct PARA taxonomy required — new active reference notes go 20-Areas/Hermes Brain/. Catches misfiles immediately.
- **[A]** Vault: correct PARA taxonomy is required; new active reference notes go in 20-Areas/Hermes Brain/. Preserve raw evidence, verified state, generated candidates, and historical observations as separate surfaces.

### ⚠️ ONLY IN A

- **[A]** Work style: terse, direct, capture-first (GTD+Kanban), timeboxed, piece-by-piece. Turn broad goals into precise task specs, manage subagent logging, and independently verify claims. Prefer execution over permission loops. Use explicit GO/NO-GO gates; defer out-of-scope work to named cards.

### ⚠️ ONLY IN A

- **[A]** Engineering: prefer native Hermes and the smallest durable solution. Check what exists first. Use paid providers; OpenRouter is emergency-only. Verify live facts. Preserve intentional work/backups. Never reveal credentials. Broad deletion, service exposure, or external writes require explicit scope and verification.

### ⚠️ ONLY IN A

- **[A]** Fleet: MacBook is A2A-only and vault writer; Mini owns Discord; VPS runs the active research-dick profile. Keep cross-host work named and deterministic. Same-machine parallel work uses delegate_task; cross-machine work uses named a2a_call. Keep local specialist profiles non-gateway unless explicitly owned.

### ⚠️ ONLY IN A

- **[A]** Communication: plain-language and scannable. Surface decisions and blocks, not transient waves/counts. Capture source log and confidence matrix before narrative findings. Wants full-chain verification before completion claims.

### ⚠️ ONLY IN A

- **[A]** Sensitive content: use confident declarative language and adversarial review. Andy Cohen voice-clone guardrail: never produce antisemitic content; preserve the user's approved sports framing.

### ⚠️ ONLY IN B

- **[B]** Name: Stephen. Active Hermes persona is Dick on Discord (bot Dick_Trickl3, Mini-owned); named after NASCAR's Dick Trickle, not a host identity. Telegram identity @Dick_Trickle_Bot is retired (Telegram decommissioned fleet-wide).

### ⚠️ ONLY IN B

- **[B]** Email: bowman.stephen92@gmail.com. Based in Lake Bluff, IL. Baby #2 due June 2026. Mac ecosystem on Tailscale: Mac mini, MacBook Pro, iPhone, Windows PC.

### ⚠️ ONLY IN B

- **[B]** Professional: UX Designer, 7+ years. Fortune 500 clients include United, Google, J&J/Janssen, and CA DHCS. Currently an AI Workstream Lead at a pharma/biotech enterprise. Domain context includes BRMS, FMV, O2R, MSL Navigator, Medical OPT, Microsoft Power Platform, SharePoint, Power Apps, Power BI, Power Automate, and Teams.

### ⚠️ ONLY IN B

- **[B]** Builder mode: Figma, React, AI prototypes, Python for agents, Obsidian for PKM, Vercel for deployments. GitHub Pro account is BowmanStephen. Sites and identity lanes: bowmanstephen.com, LinkedIn /in/bowmanstephen, X @BowmanStephen for tech/AI, X @UnmoggedAmerica as a separate satire lane, YouTube @UX_with_Stephen_Bowman.

### ⚠️ ONLY IN B

- **[B]** Communication: teacher mode plus caveman mode when technical. Minimal thinking output; preserve tokens for action. No analogies unless explicitly useful. Action over deliberation. Generic responses and long reasoning dumps frustrate him.

### ⚠️ ONLY IN B

- **[B]** Ralph loops: loose description means agent plans and executes autonomously. YOLO mode is acceptable for local work after scope is understood. "Finish the feature" means scan active context, identify the most important unfinished item, recommend one path, then execute on confirmation.

### ⚠️ ONLY IN B

- **[B]** Git/GitHub knowledge is limited; explain git operations in plain English and handle git decisions autonomously. Do not assume he understands stash, merge, upstream, or branch jargon.

### ⚠️ ONLY IN B

- **[B]** Model/provider preferences are volatile. As of the 2026-08-05 audit the fleet runs ollama-cloud/deepseek-v4-flash:0731 with gemini-2.5-flash fallback; always check live config before making routing claims.

### ⚠️ ONLY IN B

- **[B]** Interested in sports betting research, straight bets, value picks, upset candidates, sports analytics, college football, political extremism coverage with specific sourced details, AI/LLM engineering, vibe coding, multiplayer apps, and open-source agent patterns.

### ⚠️ ONLY IN B

- **[B]** When user says "use Matt Pocock skills in a loop", execute: diagnose -> triage -> to-prd -> to-issues -> tdd.

### ⚠️ ONLY IN B

- **[B]** Task decomposition preference: one machine, one tool, one repo per chunk. No decision points mid-execution. Clear done state in under 10 words. Use concrete numbered status for multi-service installs.

### ⚠️ ONLY IN B

- **[B]** Stephen likes copying/adapting open-source agent community patterns, especially SOUL.md + STYLE.md + SKILL.md stacks, security hardening, and options-not-recommendations analysis patterns. He wants deep inspection, not surface summaries.

### ⚠️ ONLY IN B

- **[B]** He prefers periodic thread audits: scan history for unfinished or loose-end issues he may have forgotten and close old threads before moving on.

### ⚠️ ONLY IN B

- **[B]** When generating files or media for Stephen, surface the result in the response. On Telegram include `MEDIA:/absolute/path/to/file` when relevant. On CLI, provide the path.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Work style: Terse/direct ADHD capture-first; values small wins, piece-by-piece progress, and real verified results. Wants Dick to curate complex ecosystems into 3–5 ranked plain-language picks, clear now/later calls, and one next action—not raw inventories or dissertations unless depth is requested.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Tech: Native Hermes + smallest durable solution over custom infra. Parallel agents for independent work. Providers he pays for (not OpenRouter). Git knowledge limited — handle git decisions autonomously.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Never reveal credentials. Preserve intentional local work and backups. Broad deletion, Kanban GC, service exposure, external writes need explicit scope and verification.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Attended American School in London (ASL). Fish Audio TTS voice details in MEMORY.md.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Broadcast: plain-language, scannable, no jargon when observing. Squad trials: tables, no raw FSM/commands, surface only decisions/blocks. Autopilot ("go"/"whatever"): surface only on decisions/blocks/final-approval. Never report transient counts, exact commands, or "dispatching X" waves.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Operational prefs: task chunks = one machine/tool/repo, no mid-execution decision points, done state <10 words. Surface generated files (Telegram: MEDIA:/abs/path; CLI: path). Likes periodic thread audits closing forgotten loose ends. Investigation: 'zoom out' = stop synthesizing, return to systematic capture; confidence matrix + source log BEFORE narrative findings. "Matt Pocock skills in a loop" = diagnose → triage → to-prd → to-issues → tdd.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Portfolio deployment pattern (2026-08-05): When Vercel production alias goes wrong (hijacked or broken), Stephen wants immediate rollback and full fix. Don't just diagnose — execute the repair (check deployment history, rollback to known-good, verify live site, then fix root cause and redeploy). CLI token approach works when `vercel api` lacks the verb (use curl directly).

### ⚠️ ONLY IN LIVE

- **[LIVE]** Strong preference: when generating content (especially with real-person personas on sensitive topics), use confident declarative language, no hedging. 'I believe' and 'I think' weaken the voice — use direct statements instead.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Andy Cohen voice-clone: anti-semitic guardrail ABSOLUTE (never). Trans-sports stance reversed 2026-08-06 by Stephen directly for TikTok audio: Andy opposes biological males in women's sports (protect-female-athletes framing).

### ⚠️ ONLY IN LIVE

- **[LIVE]** Expects adversarial review before shipping sensitive content — wants me to catch harmful readings first, not discover them after the fact.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Stephen wants Dick to communicate with every local Hermes bot and provide live, detailed bot capability/status information, routing work to the right specialist and comparing independent responses when useful.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Stephen wants Dick to operate as the 'White Morpheus': a strategic guide and orchestrator who helps Stephen work with the full Hermes bot council, translating intent into briefs, pairing specialists, cross-examining outputs, and surfacing the clearest next action.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Stephen prefers substantive frontier-AI, agent, and infrastructure research findings to be captured as sourced Obsidian vault notes, with related hub notes/backlinks updated rather than leaving the research only in chat.

### ⚠️ ONLY IN LIVE

- **[LIVE]** Visual learner: when text explanation doesn't land, generate explainer graphics (comparison panels, role cards, analogy closers) via Pillow before re-explaining in prose. Deliver the image first, then a one-line analogy.

