# Skills Guide

Read the repository operating rules first. `skills/` contains default bundled
skills; `optional-skills/` is for heavier or niche skills that require explicit
installation.

## Authoring contract

- Use `SKILL.md` frontmatter with a concise `name`, `description`, version,
  author/license, optional platform gating, and Hermes metadata. Put setup
  values under `metadata.hermes.config` so they are configured under
  `skills.config` and injected at load time.
- Keep the description to one sentence, 60 characters or fewer, ending in a
  period. It is listing text, not a miniature manual.
- Write actionable, self-contained instructions: state when to use the skill,
  prerequisites, safe procedure, expected output, and verification. Do not
  assume the model will infer missing operational steps.
- Prefer existing tools and CLI commands over new model tools. Keep files and
  examples portable across their declared platforms.
- Put a skill in `optional-skills/` when it has heavy dependencies, narrow
  appeal, or an installation burden; do not make the default bundle pay for it.
