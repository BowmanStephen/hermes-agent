# Test Guide

Read the repository operating rules first.

## Running tests

Run Python tests through `scripts/run_tests.sh`, never bare `pytest`. It
matches CI by clearing credentials, isolating `HOME`/`HERMES_HOME`, setting UTC
and a stable locale, and running each test file in a fresh subprocess.

```bash
scripts/run_tests.sh
scripts/run_tests.sh tests/gateway/
scripts/run_tests.sh tests/agent/test_foo.py -k test_x
```

Treat pass-on-retry as a flaky bug, not success. Use event-based synchronization
and realistic timing bounds; do not write negative timing races.

## Test behavior, not implementation shape

- Assert observable behavior and relationships between data, not current model
  names, configuration-version literals, registry counts, or catalog snapshots.
- Do not read source files and regex their contents. Extract a pure seam or
  dependency-injected helper and execute it for real.
- Keep tests with the artifact they cover. JavaScript/package/config assertions
  belong in Vitest, not a Python test that CI may skip for a JS-only change.
- Test real resolution chains for configuration, profiles, remote backends,
  file/network I/O, and plugin discovery. Use a temporary `HERMES_HOME`; never
  write to a user's `~/.hermes`.

## Platform behavior

Do not fake the host OS by patching `sys.platform`. Use `linux_only`,
`macos_only`, or `windows_only` markers and run host-specific behavior on that
host. Do not substitute bare `skipif` or skipped parametrized rows: the CI
classifier can otherwise omit the test on every platform. Use the live Windows
E2E lane for process-topology behavior that mocks cannot prove.
