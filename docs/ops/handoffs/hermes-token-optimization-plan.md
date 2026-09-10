> **STATUS (2026-09-09):** Archived reference — ranked cut list; first verification step (hermes tools list) not confirmed executed.

# Hermes Token Optimization — Adversarial Plan

## Executive Summary

Stephen's audit correctly identified the single largest per-turn token cost: tool schema overhead from the default `hermes-cli` toolset, which loads all 59 `_HERMES_CORE_TOOLS` on every API call. However, the audit's first-cut recommendation (disable bfl, tts, image_gen, computer_use) has a critical gap: **three of those four toolsets are already self-gating via `check_fn`** and may not be appearing in the schema at all. The plan below corrects this, separates token waste from disk waste, and ranks cuts by verified impact.

## Key Correction to the Original Audit

The audit recommends disabling `bfl`, `tts`, `image_gen`, and `computer_use` as the first cut. Source code inspection reveals:

| Toolset | check_fn | Credential present? | Actually visible in schema? |
|---|---|---|---|
| `bfl` (6 tools) | `_has_nous_credential()` | No Nous auth in critic profile | **Likely already hidden** |
| `tts` (1 tool) | `check_tts_requirements()` — edge_tts installed | edge_tts IS installed in venv | **Visible — 1 tool** |
| `image_gen` (1 tool) | `check_fal_api_key()` | FAL_KEY IS set in .env | **Visible — 1 tool** |
| `computer_use` (1 tool) | `cua_driver_binary_available()` | cua-driver IS at ~/.local/bin/ | **Visible — 1 tool** |

The bfl tools (6 schemas) are probably already absent because the critic profile has no Nous Portal credentials. Disabling bfl in config would be a no-op. The real token cost from this group is 3 tools (tts, image_gen, computer_use), not 9.

**Action**: Before cutting, verify which tools are actually in the live schema by running `hermes tools list` in a fresh session. The `check_fn` gate may have already removed bfl.

---

## Ranked Plan

### RANK 1: Disable browser toolset (13 tools, largest single overhead)

**Evidence**: The default `hermes-cli` toolset includes 13 browser tools (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`, `browser_vision`, `browser_console`, `browser_cdp`, `browser_dialog`, `browser_exec`). These have NO `check_fn` gate — they are always in the schema when the `hermes-cli` toolset is active. The browser toolset is the single largest tool-category contributor to per-turn token cost.

**Expected impact**: High. 13 tool schemas removed from every API call. At roughly 500-800 tokens per tool schema, this is approximately 6,500-10,400 tokens per turn.

**Confidence**: High. The tools are unconditionally present in `_HERMES_CORE_TOOLS`. No check_fn gates them.

**Tradeoffs**: Browser automation becomes unavailable. If Stephen uses browser tools via Discord or CLI, this breaks that workflow. Mitigation: re-enable on specific profiles that need it (e.g., a dedicated research profile) rather than globally.

**Verification**: `hermes tools list` before and after. Confirm browser tools absent. Start a new session and check token count on first turn.

**Scope**: Per profile (config.yaml `toolsets` list) or per platform (`platform_toolsets`).

---

### RANK 2: Disable kanban tools (14 tools, second largest overhead)

**Evidence**: 14 kanban tools are in `_HERMES_CORE_TOOLS`. They DO have a `check_fn` gate (`HERMES_KANBAN_TASK` env var or explicit `kanban` toolset enable), so they may already be hidden in normal sessions. But if any profile has `kanban` in its toolsets list or platform_toolsets, all 14 schemas load.

**Expected impact**: Medium-High. 14 tool schemas if currently visible. Zero if already gated.

**Confidence**: Medium. Depends on whether the check_fn is actually hiding them. The previous critic config had `kanban` in `disabled_toolsets`, which would hide them. But the clean-slate reset removed that config.

**Tradeoffs**: Multi-agent work queues become unavailable on affected profiles.

**Verification**: `hermes tools list` — check if kanban tools appear. If yes, disable. If no, skip this cut.

**Scope**: Per profile.

---

### RANK 3: Disable homeassistant tools (4 tools)

**Evidence**: 4 HA tools (`ha_list_entities`, `ha_get_state`, `ha_list_services`, `ha_call_service`) are in `_HERMES_CORE_TOOLS`. They appear to have no `check_fn` gate in the core tools list — they are always in the schema.

**Expected impact**: Medium. 4 tool schemas removed per turn.

**Confidence**: High that they are in the core list. Medium that they lack a check_fn (the registration code was not found in the expected file; they may be registered elsewhere with a gate).

**Tradeoffs**: Smart home control becomes unavailable. If Stephen does not use Home Assistant on the critic profile, this is pure waste.

**Verification**: `hermes tools list` — check if HA tools appear. If yes and unused, disable.

**Scope**: Per profile.

---

### RANK 4: Disable tts, image_gen, and computer_use (3 tools combined)

**Evidence**: These three tools are in `_HERMES_CORE_TOOLS` with `check_fn` gates. All three gates pass on this machine: edge_tts is installed, FAL_KEY is set, cua-driver is installed. So all three schemas are present in every API call.

**Expected impact**: Low-Medium. 3 tool schemas removed per turn. Roughly 1,500-2,400 tokens.

**Confidence**: High. All three check_fn requirements are met on this machine.

**Tradeoffs**: Text-to-speech, image generation, and desktop GUI control become unavailable on affected profiles. If Stephen uses these via Discord (voice messages, image generation), they should remain enabled on the Discord platform toolset but be disabled on CLI and cron.

**Verification**: `hermes tools list` before and after.

**Scope**: Per profile or per platform.

---

### RANK 5: Disk waste — state.db and lcm.db (NOT a token issue)

**Evidence**: `state.db` is 1.1 GB at the root Hermes home. `lcm.db` is 643 MB. Profile databases total 1.8 GB across 23 profiles, with `critic` (208 MB), `swarm-forge` (198 MB), `sysop` (112 MB), and `bookie` (80 MB) as the largest. The hermes-agent source tree is 6.8 GB, of which `.git` is 1.8 GB, `venv` is 2.5 GB, `node_modules` is 354 MB, and `apps` is 514 MB.

**Expected impact on tokens**: Zero. Disk waste does not affect per-turn token cost.

**Expected impact on disk**: Reclaiming 1.7+ GB is possible by pruning old session data. The `sessions.auto_prune` and `sessions.retention_days` settings control this.

**Confidence**: High that disk is unrelated to tokens. Medium on the safety of pruning — Stephen's handoff says "do not delete or prune sessions during the token audit."

**Tradeoffs**: Pruning deletes session transcripts. VACUUM after prune reclaims space but requires no active sessions.

**Verification**: `hermes sessions stats` to see session counts and sizes. `hermes sessions prune --older-than 30` to prune (when Stephen approves).

**Scope**: Global (root home) and per profile.

**NO-GO**: Do not prune during this audit. Do not delete the .git directory (it is the source checkout). Do not delete the venv (Hermes needs it to run). The source tree size is a disk concern, not a token concern.

---

## Gaps in the Original Audit

1. **check_fn gating was not verified.** The audit recommends disabling bfl, tts, image_gen, and computer_use, but did not check whether `check_fn` already hides some of these. bfl is likely already hidden (no Nous credentials on critic profile). The real first cut should target tools that are unconditionally visible: browser (13 tools) and homeassistant (4 tools) have no effective gate.

2. **Browser tools were not mentioned.** At 13 tools, the browser toolset is the single largest unconditional schema overhead. It was absent from the audit's recommendations entirely.

3. **Kanban tools were not mentioned.** At 14 tools, kanban is the second largest. If the check_fn is not gating them on a clean-slate config (no `disabled_toolsets` entry), they are all visible.

4. **Skills are indexed, not injected.** The audit asked whether all 82 bundled skills are injected. Source inspection confirms `build_skills_system_prompt()` builds a compact index (name + truncated description), not full skill content. The 48 KB `.skills_prompt_snapshot.json` is a disk cache of this index, not a per-turn payload. Skills contribute roughly 3-5K tokens to the system prompt as an index, not 82 full documents.

5. **SOUL.md is tiny.** At 513 bytes, it is negligible. The 83 KB `AGENTS.md` in the hermes-agent source tree is larger, but only loads when the cwd is the hermes-agent repo.

6. **No config.yaml exists.** The clean-slate reset removed all config files. Hermes is running on pure `DEFAULT_CONFIG` defaults, which means the `toolsets` list is `["hermes-cli"]` — the full 59-tool bundle. Creating a minimal config.yaml with a narrowed toolset list is the single most impactful action.

---

## Recommended Cut Order

| Step | Action | Tools removed | Est. tokens saved/turn | Risk |
|---|---|---:|---:|---|
| 1 | Verify live schema with `hermes tools list` | 0 | 0 | None (read-only) |
| 2 | Create config.yaml with narrowed toolsets | depends | depends | Low (reversible) |
| 3 | Disable browser (if unused on this profile) | 13 | ~6,500-10,400 | Medium (breaks browser automation) |
| 4 | Disable kanban (if already gated, skip) | 0-14 | 0-7,000 | Low |
| 5 | Disable homeassistant (if unused) | 4 | ~2,000-3,200 | Low |
| 6 | Disable tts, image_gen, computer_use on CLI/cron | 3 | ~1,500-2,400 | Low-Medium |
| 7 | Address disk waste separately (not a token issue) | 0 | 0 | None |

## One Concrete Next Action

Run `hermes tools list` in a fresh session on the critic profile to verify which of the 59 core tools are actually visible in the schema. This single read-only command tells you exactly which tools are costing tokens right now and which are already hidden by `check_fn`. Every cut decision depends on its output.
