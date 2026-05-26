# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Notes for this repo

- As of setup, only `wontfix` (and `question`) already exist as GitHub labels
  on `BowmanStephen/hermes-agent`. The other four roles will be created by the
  `triage` skill on first use, or you can pre-create them with
  `gh label create <name> --repo BowmanStephen/hermes-agent`.
- This 5-role state machine is **separate** from the broader taxonomy in
  `.engineering/triage.yml` (which defines `types`, `statuses`, `priorities`
  for `to-issues`/PRD flows). They coexist. If you want the triage roles to
  reuse a `.engineering/triage.yml` status (e.g. map `ready-for-agent` →
  `ready`), edit the right-hand column above accordingly.
