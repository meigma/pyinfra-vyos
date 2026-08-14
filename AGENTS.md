<!-- BEGIN ai-protocol -->
# Agent Instructions

This repository's operating protocol lives in `.session.md`.

Before doing substantive work, read `.session.md` in full and follow it. It
covers startup context loading, session setup, session lifecycle, skill loading,
Worktrunk branching, session journaling, file schemas, architecture, and process
expectations.

If `.session.md` is missing, stop and tell the user the session protocol is not
installed correctly.
<!-- END ai-protocol -->

# Python / pyinfra Best Practices

Rules are grouped by category and numbered for reference. Cite them by ID in
reviews and feedback: "this violates A1", "apply C3 here".

## A — Architecture

- **A1**: Keep the four-layer split. `facts.py` holds public `FactBase`
  classes and nothing else; `operations.py` holds public `@operation`
  functions and nothing else; a private domain module holds the pure logic;
  `_cli.py` holds command assembly. A public module that grows parsing or
  diffing logic has taken work that belongs one layer down.
- **A2**: Domain modules are pure. No I/O, no `subprocess`, no pyinfra state,
  no `host`. They take values and return values. This is not a style
  preference — it is what makes the mock-free unit tier in T1 possible.
- **A3**: `_cli.py` is the only place a command is assembled. Nothing else
  constructs a `StringCommand` from raw parts, and nothing anywhere executes
  one directly; operations `yield` commands and let pyinfra run them.
- **A4**: `__init__.py` re-exports the public facts and operations with an
  explicit `__all__`, mirroring `pyinfra.facts.*` / `pyinfra.operations.*`
  import ergonomics. Private modules stay underscore-prefixed and unexported.
- **A5**: Facts and operations are ordinary importable modules and need no
  registration. Only connectors are discovered through entry points. Do not
  add entry points for a fact or an operation.

## C — Command Construction and Security

- **C1**: Bare `str` arguments are reserved for trusted literals the package
  itself chooses: subcommand words and flags. Every value that came from a
  caller — paths, keys, values, names — is wrapped in `QuoteString`.
- **C2**: Reject values that begin with `-`. Shell quoting stops the shell,
  not the target binary's own option parser: a correctly quoted `--global`
  still parses as an option once the shell layer is gone. The guard belongs in
  the command builder so no caller can forget it.
- **C3**: Secrets never go in argv. Anything that must stay off the process
  table and out of pyinfra's command logs travels on stdin via `_stdin`. If
  the domain has a value that cannot be kept out of argv, say so explicitly in
  the module docstring rather than leaving it implied.
- **C4**: Prefer machine-parseable, unambiguous output formats when reading
  state — NUL-separated or JSON over line-oriented text — because values can
  contain the separator you were about to split on.

## F — Facts

- **F1**: Type the fact: subclass `FactBase[T]` with the concrete return type,
  and give `command()` and `process()` real annotations. `process()` receives
  `list[str]`, not `str`; rejoin it when the format is not line-oriented.
- **F2**: Every fact defines `default()` and `requires_command()`. `default()`
  gives a host with nothing configured a sane empty value instead of an error;
  `requires_command()` lets pyinfra skip hosts that lack the binary.
- **F3**: Convert every parse failure into `FactProcessError`. One host with
  unexpected output must degrade that host, not abort the run. Wrap
  `process()` rather than sprinkling try/except through the parser.
- **F4**: pyinfra calls `command()` and `process()` on the same instance, so a
  parameterized fact may stash its arguments on `self` between the two. That
  is the supported pattern; do not thread state through globals.

## O — Operations

- **O1**: Follow the idempotency loop exactly: read current state with
  `host.get_fact(...)`, compute the difference, call `host.noop(...)` and
  return when there is nothing to do, and only then `yield` commands. An
  operation that always yields is a bug, not a simplification.
- **O2**: Never name a parameter after a pyinfra global argument. `name` is
  the common trap — pyinfra consumes it as the operation label and your
  function never sees it. Check candidate names against
  `pyinfra.api.arguments.all_argument_meta`, and keep the meta-test that
  enforces this across every exported operation.
- **O3**: Validate inputs at the top of the operation and raise
  `OperationValueError`, so failures surface during planning rather than as a
  confusing command failure on the target.
- **O4**: Operations state their effect in the imperative and stay
  single-purpose. One operation that manages one resource beats one that
  branches over three.

## T — Testing

- **T1**: Unit tests are mock-free. Assert on `str(command)` or
  `command.get_raw_value()`, feed literal output lines to `process()`, and
  call the pure domain functions directly. If a test needs a mock, the logic
  under test is in the wrong layer (see A2).
- **T2**: Integration tests drive the real pyinfra API against `@local`,
  assert `meta.will_change` and `meta.did_change()`, and verify the resulting
  state through independent `subprocess` calls rather than through the same
  fact that produced the plan.
- **T3**: Every integration test applies its deploy twice and asserts the
  second apply is a no-op. Idempotency claimed without this proof is untested.
- **T4**: Integration tests operate on throwaway state under `tmp_path` and
  never touch the developer's or the runner's real configuration. They stay
  behind the `integration` marker and the `--integration` flag.

## S — Typing and Style

- **S1**: Annotate everything in `src/`. mypy runs near-strict there; do not
  widen a type or add `# type: ignore` to silence it without a comment saying
  what upstream gap forced it.
- **S2**: Every module, public class, and public function has a docstring.
  Module docstrings for the security-bearing layers state the contract the
  file exists to hold, not a summary of its functions.
- **S3**: Use inline comments sparingly, for genuinely non-obvious code or a
  constraint the code cannot express. Do not narrate what the next line does.
- **S4**: `ruff` and `mypy` settings live in `pyproject.toml` and are not
  overridden per file. Fix the code, not the configuration.

## L — Dependencies and Tooling

- **L1**: Run everything through `uv run --locked`. A task that resolves
  dependencies at run time can pass locally and fail in CI, which defeats the
  lockfile.
- **L2**: Add dependencies with `uv add` / `uv add --dev` and commit
  `pyproject.toml` and `uv.lock` together. Never hand-edit `uv.lock`.
- **L3**: Tools come from mise (`mise.toml` + `mise.lock`, `locked = true`).
  Bumping a locked tool means `mise lock`; bootstrapping a brand-new one is
  the only case for `MISE_LOCKED=0 mise lock`, and it generates the entry
  only. Never commit a change to `locked` itself.
- **L4**: Moon is the task front door. Add a task to `moon.yml` rather than
  documenting a bare command, and wire anything that must gate a pull request
  into `root:check`.
- **L5**: Prefer the standard library and mature dependencies. The runtime
  dependency surface of a pyinfra plugin should stay close to `pyinfra`
  itself; a plugin that drags in a large tree makes every consumer pay for it.

## G — Commits

- **G1**: Use Conventional Commit subjects. Release Please reads them to build
  the changelog and choose the version, so the subject of a squashed pull
  request is release-facing text.
- **G2**: Use a non-release type (`docs:`, `ci:`, `chore:`, `test:`) for work
  that should not appear in release notes, and `feat:` / `fix:` only for
  changes a consumer of the package would notice.
