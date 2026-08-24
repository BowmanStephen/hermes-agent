# CLI, Configuration, and Update Guide

Read the repository operating rules first. This directory owns CLI commands,
configuration, profiles, setup, and the update pipeline.

## Commands and configuration

- Register interactive slash commands in `COMMAND_REGISTRY`; do not maintain
  duplicate help, autocomplete, or platform maps. Add gateway dispatch only
  when the command is truly available there.
- Add behavioral configuration to `DEFAULT_CONFIG`. Bump `_config_version`
  only for a migration of existing user data, not for an additive key.
- Add credentials to `OPTIONAL_ENV_VARS`; all other user-facing settings stay
  in `config.yaml`. Confirm which loader is used by the CLI and gateway before
  declaring a configuration change complete.
- CLI menu pickers use curses. Do not use ANSI erase-to-EOL (`\033[K`) in
  spinners or display code.

## Profiles and paths

- Profile selection sets `HERMES_HOME` before imports. Use
  `get_hermes_home()` for profile state and `display_hermes_home()` in output.
- Profile management is intentionally HOME-anchored so an active profile can
  still enumerate all profiles. Do not change it to use the active home.

## Updating

Maintain the update sequence: `plan → snapshot → apply → restart → verify →
report`. A successful update cannot leave a mixed-version gateway fleet.

- The ZIP fallback is only for a git failure; do not use it after a dependency
  failure. Refuse dirty trees and preserve the built Desktop release during a
  staged source swap.
- Restarts for systemd/launchd are fleet-wide and drain first. Verify running
  `code_sha`/`code_version` after restart and write a receipt for every outcome,
  including an early refusal or failure.
- Process classification must use parser-derived canonical matchers, not
  substring matching or hand-maintained flag lists.
