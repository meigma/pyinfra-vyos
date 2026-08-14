# template-pyinfra

`template-pyinfra` is the reusable repository starter for Meigma
[pyinfra](https://pyinfra.com) plugin packages — distributions that ship custom
facts and operations for other people's deploys to import.

It provides a working package on day one: a src-layout Python distribution with
a small sample domain (repository-local `git config`), Moon task orchestration
over a mise-pinned toolchain, hardened CI, a MkDocs Material docs site, and a
full release path to PyPI with build provenance. The sample primitives are
meant to be gutted and replaced; everything around them is meant to be kept.

If you just created a repository from this template, start with
[DELETE_ME.md](DELETE_ME.md).

## Local Bootstrap

Prerequisites:

- [mise](https://mise.jdx.dev) — provisions every pinned tool from `mise.toml` +
  `mise.lock`: the Python interpreter, `uv`, and `moon`. Run `mise install`
  once; there is nothing else to install by hand.

Everything else — `ruff`, `mypy`, `pytest`, `pyinfra` — comes from the `uv` dev
dependency group and is resolved by `uv.lock`, not by mise.

Tool versions live in `mise.toml`; `mise.lock` records a per-platform download
URL and checksum for each. `mise install` runs with `locked = true`, so it
**fails closed** if a tool lacks a pre-resolved, checksummed entry for the
current platform. Moon runs every task against these tools as `system` binaries
on PATH and manages no toolchain itself.

To bump an already-locked tool, edit its version in `mise.toml`, then:

```sh
mise lock --platform linux-x64,linux-arm64,macos-x64,macos-arm64
```

Commit `mise.toml` and `mise.lock` together.

Adding a **new** tool is the one case where `locked = true` gets in the way:
`mise lock` cannot resolve a tool that has no lockfile entry yet, so the first
lock has to run with the setting relaxed.

```sh
MISE_LOCKED=0 mise lock --platform linux-x64,linux-arm64,macos-x64,macos-arm64
```

That escape hatch is for generating the entry only. Never commit a change to
`locked` itself, and never run day-to-day installs with it unset.

## Common Tasks

Moon is the standard task front door:

```sh
moon run root:format      # ruff format --check + import-order check
moon run root:format-fix  # rewrite formatting and import order in place
moon run root:lint        # ruff check
moon run root:lock        # uv lock --check
moon run root:typecheck   # mypy over src/
moon run root:test        # pytest, unit tier only
moon run root:build       # uv build (sdist + wheel)
moon run root:check       # everything above, plus the docs build
```

CI runs the same aggregate check:

```sh
moon ci --summary minimal
```

The docs site has its own project:

```sh
moon run docs:serve       # live-reload preview
moon run docs:build       # render to docs/build
```

## Sample Primitives

The package exports two facts and one operation over `git config`. They are
deliberately small and deliberately real: `git` is on every machine, needs no
daemon, and has natural idempotency, so the integration tests and the example
below actually run.

Everything stays inside a repository's own `.git/config` — the fact and the
operation both pass `--local`, so nothing touches the user's global config.

```python
# inventory.py
hosts = ["@local"]
```

```python
# deploy.py
from pyinfra import host

from template_pyinfra import GitConfig, GitVersion, config_entry

version = host.get_fact(GitVersion)
config = host.get_fact(GitConfig, path="/srv/checkout")

config_entry(
    key="user.email",
    value="release-bot@example.com",
    path="/srv/checkout",
)

config_entry(
    key="core.hooksPath",
    present=False,
    path="/srv/checkout",
)
```

```sh
pyinfra inventory.py deploy.py
```

Run it a second time and every operation reports as a no-op: `config_entry`
reads `GitConfig` first, compares against the desired entry, and calls
`host.noop(...)` when there is nothing to change.

The same primitives work over SSH without any changes — facts and operations
run the `git` CLI through whatever connector the inventory selects:

```python
# inventory.py
hosts = [("build01.example.net", {"ssh_user": "deploy"})]
```

Four files hold the sample, and they are the four to replace:

| File | Role |
| --- | --- |
| `src/template_pyinfra/facts.py` | Public `FactBase` classes only |
| `src/template_pyinfra/operations.py` | Public `@operation` functions only |
| `src/template_pyinfra/_gitconfig.py` | Pure domain logic: parse, diff, build commands |
| `src/template_pyinfra/_cli.py` | Command assembly, quoting, and option-lookalike rejection |

`_cli.py` is the layer to keep. It holds the security contract every operation
depends on: user values are wrapped in `QuoteString`, values that begin with
`-` are rejected outright because shell quoting does not stop the target binary
from parsing them as options, and secrets travel on stdin rather than argv.
Only the binary name and its flags should change when the domain does.

## Testing

Tests come in two tiers.

**Unit** is the default tier and is mock-free. It asserts on rendered commands,
feeds literal output lines to a fact's `process()`, and calls the pure domain
functions directly:

```sh
moon run root:test
```

**Integration** drives the real pyinfra API against `@local`, applies each
deploy twice to prove idempotency, and verifies the resulting state with
independent `subprocess` calls against a throwaway repository in `tmp_path`.
It never touches the machine's real git configuration. Integration tests are
skipped unless the flag is passed, so they never slow the default loop:

```sh
moon run root:test-integration
```

`root:check` runs the unit tier only. The integration tier has its own CI
workflow.

## CI and Security

The CI workflow keeps `permissions: {}` at the top level and grants scopes per
job, pins every external action by commit SHA, disables checkout credential
persistence, and delegates the actual work to `moon ci root:check`. Dependency
caching is keyed on `uv.lock` and `docs/uv.lock`. A separate workflow runs the
integration tier, and the docs workflow builds the site on pull requests and
deploys `docs/build` to GitHub Pages from the default branch.

A weekly security scan runs OSV-Scanner against both committed lockfiles and
zizmor against the workflow files, uploading both results to GitHub code
scanning. Dependabot covers GitHub Actions, the root uv project, and the docs
uv project.

Repository settings are code, not clicks. They live in
`.github/repository-settings.toml` and are applied by

```sh
uv run .github/scripts/configure_github_repo.py plan --repo OWNER/REPO
uv run .github/scripts/configure_github_repo.py apply --repo OWNER/REPO
```

The defaults are squash-only merges, signed commits, linear history, required
status checks, protected tags, immutable releases, and private vulnerability
reporting.

## Release Layer

Release automation is enabled in the template itself so the sample package
proves the whole path before generated projects inherit it.

- Release Please opens and maintains the release pull request from Conventional
  Commit subjects, then creates a **draft** GitHub release and a `vX.Y.Z` tag
  after merge.
- The release dry run rehearses the full publish on every release pull request:
  `uv build --no-sources`, metadata validation, a wheel smoke test in a clean
  environment, and `uv publish --dry-run`.
- The tag build re-runs `moon run root:check`, rebuilds the artifacts, checks
  the package metadata against the tag, smoke-tests the wheel, and publishes to
  PyPI through [trusted publishing](https://docs.pypi.org/trusted-publishers/)
  — no long-lived API token, scoped to the `pypi` environment.
- GitHub artifact attestations for the sdist and wheel checksums are generated
  by a separate, isolated reusable workflow (`attest.yml`). Keeping the signing
  identity unreachable from build steps is the SLSA Build L3 isolation
  requirement; verify with
  `gh attestation verify --signer-workflow .../attest.yml`.
- Publishing the draft release stays a human decision.

Before the first release, a generated repository must configure PyPI trusted
publishing for its own project name and install the release GitHub App. See
[DELETE_ME.md](DELETE_ME.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines, local setup
expectations, and pull request workflow.

## Security

See [SECURITY.md](SECURITY.md) for supported versions and the private
vulnerability reporting path.

## License

Add the repository license before publishing a project generated from this
template.
