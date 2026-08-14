# Welcome to the Meigma pyinfra Template

This repository was generated from `template-pyinfra`, the standard starter for
Meigma [pyinfra](https://pyinfra.com) plugin packages: distributions that ship
custom facts and operations for other people's deploys to import.

It is meant to give a new repository a working baseline on day one — a
publishable package, a task runner, pinned CI, repository security defaults,
and a release pipeline that the template itself has already exercised.

Delete this file after you finish the first-setup checklist below.
It is only here to orient the initial project owner.

## What This Template Provides

- A src-layout Python distribution named `template-pyinfra`, importable as
  `template_pyinfra`, built with `hatchling` and locked with `uv`.
- A sample domain — repository-local `git config` — as two facts
  (`GitVersion`, `GitConfig`) and one operation (`config_entry`), plus the pure
  domain module and command-builder layer they sit on.
- Two test tiers: a mock-free unit tier and an integration tier that drives the
  real pyinfra API against `@local`.
- Moon tasks for `format`, `format-fix`, `lint`, `lock`, `typecheck`, `test`,
  `build`, `scripts-test`, `test-integration`, and the aggregate `check`.
- A mise-pinned, checksum-locked toolchain (Python, `uv`, `moon`) that fails
  closed on unlocked installs.
- CI that delegates to `moon ci root:check` with pinned actions, dependency
  caches, and minimal token permissions, plus a separate integration workflow.
- A weekly security scan: OSV-Scanner over both lockfiles and zizmor over the
  workflow files, both reporting to GitHub code scanning.
- Dependabot coverage for GitHub Actions, the root uv project, and the docs uv
  project.
- MkDocs Material docs scaffolding under `docs/`, with GitHub Pages as the
  publishing target.
- Repository settings as code: signed commits, squash-only merges, linear
  history, required status checks, protected tags, immutable releases, and
  private vulnerability reporting.
- A release path to PyPI: Release Please, a rehearsal dry run, trusted
  publishing, and GitHub artifact attestations.

## How The Layers Fit

Moon is the entrypoint for local development and CI:

```sh
moon run root:check
```

That aggregate check runs formatting, linting, the lockfile check, typing, the
unit tests, the package build, the repository-script tests, and the docs build.
CI runs the same path through `moon ci root:check --summary minimal`. The
integration tier is a separate task and a separate workflow, so it never slows
the default gate.

Underneath Moon, mise supplies the tools and `uv` supplies the dependencies.
Nothing invokes a tool that is not pinned: `mise.lock` covers Python, `uv`, and
`moon`; `uv.lock` covers everything else; and every task runs
`uv run --locked`, which fails if the lockfile has drifted from
`pyproject.toml`.

The release layer separates versioning from publication, deliberately:

- Release Please turns Conventional Commit subjects into a release pull request,
  and on merge creates a **draft** release plus a `vX.Y.Z` tag.
- The dry run rehearses the entire publish on the release pull request, so the
  first real publish is never the first attempt.
- The tag build re-verifies, publishes to PyPI through trusted publishing, and
  attests the artifact checksums from an isolated reusable workflow.
- A human publishes the draft release.

## First Setup Checklist

1. **Rename the distribution and the import package.**

   The distribution name is `template-pyinfra`; the import package is
   `template_pyinfra`. Both must change, and they are spelled differently:

   ```sh
   git mv src/template_pyinfra src/YOUR_PACKAGE
   ```

   Then update `[project] name` in `pyproject.toml` to the distribution name
   (hyphens), and the build outputs in `moon.yml` (`dist/YOUR_PACKAGE-*.whl`,
   `dist/YOUR_PACKAGE-*.tar.gz`) to the wheel's normalized name (underscores).

2. **Replace every remaining placeholder.**

   ```sh
   rg 'template-pyinfra|template_pyinfra|TEMPLATE_PYINFRA|meigma/template-pyinfra'
   ```

   Work through every hit. The ones that are easy to miss:

   - `pyproject.toml` — name, description, authors, `[tool.mypy] files`.
   - `moon.yml` — project title, description, owner, and the `build` outputs.
   - `docs/mkdocs.yml` — `site_name`, `site_url`, `repo_url`. The Pages URL is
     usually `https://OWNER.github.io/REPO/`.
   - `docs/docs/index.md` and every import shown in it.
   - `release-please-config.json` — including the `extra-files` jsonpath that
     bumps the package's own version inside `uv.lock`.
   - `.github/workflows/release.yml` and `release-dry-run.yml` — artifact names
     and the wheel smoke-test import.
   - `README.md` and `CONTRIBUTING.md`.
   - `src/YOUR_PACKAGE/__init__.py` — the module docstring names the sample
     domain and the layer map.

3. **Relock after the rename.**

   ```sh
   uv lock
   ```

   The project is its own dependency in `uv.lock`, so the rename does not take
   effect until this runs. Commit `uv.lock` with the rename.

4. **Decide whether to keep the docs site.**

   Keep it if the package will have more than a README. To drop it, remove
   `docs/`, `.github/workflows/docs-pages.yml`, the `docs` entry in
   `.moon/workspace.yml`, the `docs:build` dependency in `moon.yml`'s `check`
   task, the `/docs` entry in `.github/dependabot.yml`, the `docs/uv.lock`
   lockfile argument in `.github/workflows/security-scan.yml`, the
   `docs/uv.lock` cache key in `.github/workflows/ci.yml`, and the `[pages]`
   table in `.github/repository-settings.toml`.

5. **Configure PyPI trusted publishing.**

   Create a [pending publisher](https://docs.pypi.org/trusted-publishers/)
   on PyPI for the new distribution name, pointing at this repository, the
   workflow filename `release.yml`, and the environment `pypi`. Then create a
   repository environment named `pypi`. No API token is stored anywhere.

6. **Install the release GitHub App.**

   Release Please runs as a GitHub App so its commits are signed and can bypass
   the tag protection ruleset. Install the app on the repository and set:

   - repository variable `MEIGMA_RELEASE_APP_ID`
   - repository secret `MEIGMA_RELEASE_APP_PRIVATE_KEY`

   The app slug is referenced as a tag-ruleset bypass actor in
   `.github/repository-settings.toml`; change it there if you use a different
   app.

7. **Apply the repository settings.**

   ```sh
   uv run .github/scripts/configure_github_repo.py plan  --repo OWNER/REPO
   uv run .github/scripts/configure_github_repo.py apply --repo OWNER/REPO
   ```

   Read the plan before applying. Set `is_template = false` first unless the new
   repository is itself a template. If you renamed or removed a workflow, update
   the required `contexts` list — it defaults to `ci`, `integration`, and
   `Package Release Dry Run`.

8. **Run the full local check.**

   ```sh
   mise install
   moon run root:check
   moon run root:test-integration
   ```

9. **Rewrite the project-facing docs.**

   - Rewrite `README.md` for the actual package.
   - Review `CONTRIBUTING.md`.
   - Replace `SECURITY.md` with a real policy. Before dropping its "Known
     upstream advisories" section, re-check whether pyinfra now admits
     `paramiko>=5`; if it still does not, carry the note forward.
   - Add a `LICENSE` file before publishing the repository.

10. **Delete this file.**

    ```sh
    rm DELETE_ME.md
    ```

## Gutting The Sample Domain

The sample exists so the repository is testable and demonstrable from the first
commit, not because `git config` is interesting. Replace it in this order — each
step leaves the tree in a state where `moon run root:check` still tells you
something useful.

1. **`src/YOUR_PACKAGE/facts.py`** — start here, because facts are read-only and
   have no idempotency logic to preserve. Replace `GitVersion` and `GitConfig`
   with your own `FactBase` subclasses. Keep the patterns, not the content: a
   `default()` staticmethod so a missing target degrades instead of raising,
   `requires_command()` so pyinfra skips hosts without the binary, typed
   `command()`/`process()` signatures, and the `_fact_process` decorator that
   converts any parse failure into `FactProcessError`.

2. **`src/YOUR_PACKAGE/operations.py`** — replace `config_entry`. Keep the
   idempotency loop exactly as it is shaped: read the current state with
   `host.get_fact(...)`, diff it against the desired state, call `host.noop(...)`
   and return when there is nothing to do, and only then `yield` commands. Keep
   `_guarded` so domain errors surface as `OperationValueError`. Do not name a
   parameter after a pyinfra global argument — `name` in particular is reserved
   for the operation label and never reaches your function.

3. **`src/YOUR_PACKAGE/_gitconfig.py`** — rename it after your domain and
   replace its contents. This module is pure: it parses output, computes
   changes, and builds commands, with no I/O and no pyinfra state. Keeping it
   pure is what lets the unit tests run without a single mock.

4. **`src/YOUR_PACKAGE/_cli.py`** — **keep this file.** Change the binary name
   and its flags; do not weaken the contract. Every user-supplied value stays
   wrapped in `QuoteString`, values beginning with `-` stay rejected (shell
   quoting does not stop the target binary from parsing them as options), and
   anything secret travels on stdin rather than argv.

5. **`src/YOUR_PACKAGE/__init__.py`** — update the re-exports, `__all__`, and
   the module docstring's layer map.

6. **`tests/`** — replace `test_gitconfig.py`, `test_facts.py`,
   `test_operations.py`, and `tests/integration/`. Keep `tests/conftest.py`
   (the `--integration` flag and skip logic), keep the reserved-argument
   meta-test in `tests/test_cli.py` — it introspects every exported operation
   against `pyinfra.api.arguments.all_argument_meta`, so it keeps working for
   your operations for free — and keep the integration harness helpers and the
   apply-twice idempotency assertion.

7. **`README.md` and `docs/`** — rewrite the usage example last, once the API
   it demonstrates has settled.
