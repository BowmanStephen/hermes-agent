# Follow-Up Goals: Thermo-Nuclear Code Quality Review

## Context

Stephen provided a proposed `thermo-nuclear-code-quality-review` Task subagent
definition. The desired behavior is a harsh maintainability audit that reviews
diffs and changed-file contents using the rubric from a
`thermo-nuclear-code-quality-review` skill in the cursor-team-kit plugin, with a
fallback rubric when that plugin skill is unavailable.

The local skill cache does not currently contain a skill named
`thermo-nuclear-code-quality-review`, so the next work should first make the
rubric available or explicitly validate the fallback behavior.

## Goal 1: Install Or Port The Rubric

Make the review rubric available to Codex before relying on the subagent.

Acceptance criteria:

- [ ] Locate the cursor-team-kit `thermo-nuclear-code-quality-review` skill, or
      confirm it is unavailable in this environment.
- [ ] If available, install or expose it through the local skill system without
      duplicating stale copies.
- [ ] If unavailable, create a local skill that captures the intended rubric:
      maintainability first, ambitious simplification, no unjustified file
      sprawl past roughly 1,000 lines, no spaghetti branching, explicit
      boundaries, and canonical layers.
- [ ] Verify the skill can be discovered by name before using it in a review.

## Goal 2: Add A Review Orchestration Pattern

Create a repeatable parent-agent flow for invoking the review subagent.

Acceptance criteria:

- [ ] Define the standard collection step: gather `git diff <base>...HEAD`,
      changed-file list, and full contents for changed source files.
- [ ] Preserve the intended two-collector model where useful: one shell-oriented
      collector for git/diff output and one explore-oriented collector for
      cross-file contents and boundaries.
- [ ] Invoke the review subagent only after both evidence payloads are present.
- [ ] Ensure the review prompt contains clearly labeled `### Git / diff output`
      and `### Changed file contents` sections.
- [ ] Make the default base branch configurable, defaulting to `main`.

## Goal 3: Run The First Audit On The GatewayRunner Refactor

Use the new review path against the current GatewayRunner decomposition work.

Acceptance criteria:

- [ ] Collect the current branch diff against `main` without reverting unrelated
      work.
- [ ] Include full contents for the changed gateway modules and focused tests.
- [ ] Run the thermo-nuclear review against that evidence.
- [ ] Convert findings into prioritized follow-up work, separating blockers from
      optional cleanup.
- [ ] Do not treat cosmetic comments as important while structural issues remain.

## Goal 4: Turn Findings Into Trackable Work

Make the review output actionable instead of leaving it as prose.

Acceptance criteria:

- [ ] For every high-severity finding, write a concrete task with owner surface,
      affected files, required behavior, and verification command.
- [ ] Group tasks by architectural boundary: routing, session lifecycle, agent
      runtime, platform lifecycle, delivery, voice, cron/watchers, tests.
- [ ] Mark tasks that are safe to parallelize.
- [ ] Identify any task that requires a new characterization test before code
      motion.

## Provided Subagent Draft

```markdown
---
name: thermo-nuclear-code-quality-review
description: Thermo-nuclear code quality audit (maintainability, structure, 1k-line rule, spaghetti, code-judo). Invoked via Task after a parent gathers diff and file contents. Loads the rubric from the `thermo-nuclear-code-quality-review` skill in the cursor-team-kit plugin.
---

# Thermo-Nuclear Code Quality Review

You are a **Task subagent**. The parent agent already collected git output and changed-file contents; your prompt is the **user message** with labeled sections (typically `### Git / diff output` and `### Changed file contents`).

## Rubric

1. Load the `thermo-nuclear-code-quality-review` skill (shipped in the cursor-team-kit plugin) and treat its `SKILL.md` as the **complete** rubric — tone, approval bar, output ordering, code-judo / 1k-line / spaghetti rules.
2. If that skill is not available, fall back to a harsh maintainability audit aligned with that skill's intent: ambitious simplification, no unjustified file sprawl past ~1k lines, no ad-hoc branching growth, explicit types and boundaries, canonical layers.

## Work

- Apply the rubric **only** to what the diff and contents show. Trace cross-file impact when the change touches module boundaries.
- Output in the **priority order** the rubric specifies. Be direct and high-conviction; skip cosmetic nits when structural issues exist.
- Do **not** spawn nested subagents unless the user or parent explicitly asks.

## Parent orchestration

Typical flow: in **one** message, run two `Task` calls in parallel — `subagent_type: "shell"` and `subagent_type: "explore"` — to collect `git diff <base>...HEAD` output and full contents of changed files (default base `main`). Then invoke this agent with `subagent_type: "thermo-nuclear-code-quality-review"` and a user prompt containing `### Git / diff output` and `### Changed file contents`.
```
