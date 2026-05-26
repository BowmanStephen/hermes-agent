# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues on the fork
`BowmanStephen/hermes-agent` (not the upstream `NousResearch/hermes-agent`
remote). Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --repo BowmanStephen/hermes-agent --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --repo BowmanStephen/hermes-agent --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --repo BowmanStephen/hermes-agent --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --repo BowmanStephen/hermes-agent --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --repo BowmanStephen/hermes-agent --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --repo BowmanStephen/hermes-agent --comment "..."`

This repo has two remotes — `origin` (upstream NousResearch) and `fork`
(BowmanStephen). Always target the fork explicitly with `--repo
BowmanStephen/hermes-agent`; don't rely on `gh`'s default remote inference,
which may resolve to upstream.

## When a skill says "publish to the issue tracker"

Create a GitHub issue on `BowmanStephen/hermes-agent`.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --repo BowmanStephen/hermes-agent --comments`.
