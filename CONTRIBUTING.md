# Contributing

Thank you for your interest in contributing.
This repository is a pyinfra plugin template, so changes should keep the
generated-project path simple and predictable.
For private vulnerability reporting, use [SECURITY.md](SECURITY.md) instead of
public channels.

## Reporting Bugs

Report non-security bugs through GitHub issues.
Include the following details when possible:

- package version, commit, and Python version
- the pyinfra version and the connector in use (`@local`, SSH, ...)
- steps to reproduce, ideally a minimal `inventory.py` plus `deploy.py`
- expected behavior
- actual behavior
- logs or output, with `pyinfra -v` if the failure is in a fact or operation

If you are reporting a security issue, stop and follow [SECURITY.md](SECURITY.md)
instead.

## Pull Requests

Contributors should:

1. Keep changes focused and scoped to a single problem.
2. Add or update tests when behavior changes. New facts and operations need
   unit tests; anything touching command construction or idempotency also needs
   an integration test that applies the deploy twice.
3. Update documentation when user-facing behavior changes.
4. Use Conventional Commit subjects, such as `feat: add config_entry operation`
   or `fix: rejoin null-separated fact output`.
5. Make sure `moon run root:check` passes before requesting review.

## Local Setup

```sh
mise install         # provision the pinned toolchain (Python, uv, moon)
moon run root:check
```

Useful project commands:

```sh
moon run root:format-fix   # rewrite formatting and import order
moon run root:lint
moon run root:typecheck
moon run root:test
moon run root:test-integration
moon run docs:serve
```

Dependencies are locked. Add one with `uv add` (or `uv add --dev` for tooling),
which updates `pyproject.toml` and `uv.lock` together; commit both. Every task
runs `uv run --locked`, so a lockfile that drifts from `pyproject.toml` fails
the build rather than silently resolving something new.

### Changing a Pinned Tool

`mise.toml` pins Python, `uv`, and `moon`, and `mise.lock` records a checksum
for each supported platform. `locked = true` makes installation fail closed, so
bumping a tool means regenerating the lock:

```sh
mise lock --platform linux-x64,linux-arm64,macos-x64,macos-arm64
```

Adding a **new** tool is the exception: `mise lock` cannot resolve something
that has no entry yet, so the first lock needs the setting relaxed for that one
command.

```sh
MISE_LOCKED=0 mise lock --platform linux-x64,linux-arm64,macos-x64,macos-arm64
```

Use it only to generate the missing entry. Do not change `locked` in
`mise.toml`, and do not run ordinary installs with it unset. Commit `mise.toml`
and `mise.lock` in the same change.

## Release Changes

Release Please reads Conventional Commit subjects to build the changelog and
the release pull request, so the subject line of a merged squash commit is what
ends up in the release notes. Keep release-impacting commits clear; routine
docs, CI, and maintenance commits should use a non-release type such as
`docs:`, `ci:`, or `chore:`.
