# pyinfra-vyos — wave-1 implementation plan

Executes `ARCHITECTURE.md` (same folder; authoritative — section references below are to it). Companion background: session 001 research report. Executor: coding agents; steps are ordered by dependency, each with file targets and acceptance. Steps marked **[USER]** cannot be automated and must be done by the repository owner.

Conventions used throughout: distribution `pyinfra-vyos`, import package `pyinfra_vyos`, repo `meigma/pyinfra-vyos`, Pages URL `https://meigma.github.io/pyinfra-vyos/`.

---

## Phase A — template first-setup

Do this before any domain work so every later gate runs under the real names. The sample git domain stays alive through this phase; it is the thing that keeps `moon run root:check` meaningful until Phase B replaces it.

### A1. Rename distribution and import package

- `git mv src/template_pyinfra src/pyinfra_vyos`
- `pyproject.toml` — `[project] name = "pyinfra-vyos"`, description, `[tool.mypy]` stays `files = ["src"]` (no change needed).
- `moon.yml` — `build` outputs → `dist/pyinfra_vyos-*.whl` / `dist/pyinfra_vyos-*.tar.gz`; project `title`, `description`.
- Update all `template_pyinfra` imports in `src/` and `tests/` (they still import the sample domain at this point — that is fine, just re-pointed).

### A2. Placeholder sweep

Run `rg 'template-pyinfra|template_pyinfra|TEMPLATE_PYINFRA|meigma/template-pyinfra'` and clear every hit. DELETE_ME names these as the easy-to-miss set — treat it as the checklist:

- `pyproject.toml` (name, description, authors)
- `moon.yml` (title, description, owner, build outputs — done in A1, verify)
- `docs/mkdocs.yml` (`site_name`, `site_url` → the Pages URL, `repo_url`)
- `docs/docs/index.md` and every import statement shown in it
- `release-please-config.json` — `package-name` and the `extra-files` jsonpath (`$.package[?(@.name.value=="pyinfra-vyos")].version`)
- `.github/workflows/release.yml` and `release-dry-run.yml` — artifact names, wheel smoke-test import (`import pyinfra_vyos`)
- `README.md`, `CONTRIBUTING.md` (name-level fixes only here; real rewrite is Phase D)
- `src/pyinfra_vyos/__init__.py` docstring (name-level only; real rewrite is B6)

Acceptance: the `rg` pattern returns zero hits outside `DELETE_ME.md` and `CHANGELOG` history.

### A3. Relock

- `uv lock` (the project is its own dependency in `uv.lock`; the rename is inert until this runs). Commit `uv.lock` with the rename.

### A4. Docs site decision: **keep**

Rationale: this package carries load-bearing operator contracts (secret boundary, serialization precondition, commit-verify-save pattern, wave-2 surface growth) that outgrow a README. No file changes needed beyond A2's mkdocs placeholders.

### A5. Manual setup — **[USER]**, can proceed in parallel with Phase B

- **[USER]** PyPI: create a pending trusted publisher for `pyinfra-vyos` → repo `meigma/pyinfra-vyos`, workflow `release.yml`, environment `pypi`; create the `pypi` repository environment on GitHub.
- **[USER]** Release app: install the Meigma release GitHub App; set repo variable `MEIGMA_RELEASE_APP_ID` and secret `MEIGMA_RELEASE_APP_PRIVATE_KEY`.
- **[USER]** Repository settings: `uv run .github/scripts/configure_github_repo.py plan --repo meigma/pyinfra-vyos`, read the plan, then `apply`. Confirm `is_template = false` and that the required contexts (`ci`, `integration`, `Package Release Dry Run`) match the workflows, which this plan does not rename.
- **[USER]** Add a `LICENSE` file before first publish (DELETE_ME item 9; license choice is the owner's).

### Gate 1

`moon run root:check` passes with the renamed package still carrying the sample domain. Do not proceed to Phase B on a red gate.

---

## Phase B — domain implementation

Dependency order per the architecture module map (§1). Each step replaces its module *and* its unit-test companion in the same step, so the tree is always internally consistent and `root:check` stays green (or fails for a real reason). The sample-domain files (`_gitconfig.py`, `test_gitconfig.py`) are deleted when their last dependent is replaced.

### B1. `src/pyinfra_vyos/_parse.py` — pure parsers

- New module; inherits the parse half of `_gitconfig.py`'s pattern (pure, no I/O, no pyinfra state).
- Contents per §1 row `_parse.py`: `show version` first-colon parser (label normalization, required `version` key, tolerant of unknown/missing optionals); config-JSON `json.loads` + top-level dict check; marker require-and-strip helper; streaming non-empty check (≥1 non-whitespace byte, chunked); `ConfigurationCommands` line handling (nonempty lines preserved as-is, §3 table). Own exception type(s), analogous to `GitConfigError`.
- Companion: `tests/test_parse.py` (new). Fixture-driven; see Phase C for fixture inventory.
- Acceptance: unit tests pass standalone; module imports nothing from pyinfra.

### B2. `src/pyinfra_vyos/_session.py` — session-script builder

- New module; inherits `_gitconfig.py`'s pure-domain role for the build half.
- Contents per §1 row `_session.py` and the full §2 session-script contract: script-text builder (pager export, script-template source, EXIT-trap/teardown/`builtin exit` contract, `configure` + `inSession` assert, `load` rc+output check, `sessionChanged` tri-state change gate and post-commit re-check, save block with the status-checked `showConfig` needs-save test, sentinel emission); sentinel/exit constants; 128-bit random staging-path token generator. Do not restate §2 in code comments — implement it exactly.
- Companion: `tests/test_session.py` (new). Script-text contract tests per §1 test note: assert the load-bearing lines/ordering (pager before source, `trap - EXIT` inside the trap, every terminating path is `builtin exit`, tri-state handling present for both `sessionChanged` calls, save block runs on the noop path, sentinel strings match the constants), token entropy/uniqueness, and that the staging path is never content-derived.
- After B1+B2: `_gitconfig.py`'s replacement exists in full. Delete `src/pyinfra_vyos/_gitconfig.py` and `tests/test_gitconfig.py` here only if nothing else still imports them; otherwise defer deletion to B4 (facts/operations still import `_gitconfig` until then — expect to defer).

### B3. `src/pyinfra_vyos/_cli.py` — command assembly

- Rewrites the existing `_cli.py` in place; keeps the single-assembly-point contract, drops what the architecture drops (§1 row `_cli.py`): no `_stdin` plumbing, no leading-dash rejection machinery — wave 1 has no user-supplied argv. This deviation from DELETE_ME's "do not weaken" note is architecture-sanctioned: the contract collapses to "no user input reaches argv at all".
- Contents: `vyos_op_command(*argv, marker)` (wraps bare op-mode argv in `vbash -c 'source …/script-template; run <argv…>'`, appends the `printf '\n%s\n' <marker>` emission chained with `&&`, exactly one `run`, added here); `sg_vbash(script_path)`; `sg_probe()` (the §2 preflight: `sg vyattacfg -c 'test -x /bin/vbash && test -r …/script-template' </dev/null`). `QuoteString` only on interpolated staging paths.
- Companion: `tests/test_cli.py` — rewrite the git command-builder tests into rendered-command assertions for the three builders (exact `get_raw_value()` strings incl. `</dev/null`, marker chaining, quoting of a staging path containing a space). **Keep the reserved-argument meta-test verbatim** (it introspects `pyinfra_vyos.__all__` against `all_argument_meta` and needs no changes; it goes green again at B6 when `__all__` exports `config_load`).

### B4. `src/pyinfra_vyos/facts.py` — `Version`, `Configuration`, `ConfigurationCommands`

- Replaces `GitVersion`/`GitConfig`. Keep the template patterns DELETE_ME names: `default()`, `requires_command()` (→ `"vbash"`, binary-presence gate only per §3), typed `command()`/`process()`, the `_fact_process` decorator → `FactProcessError`.
- Contents per §3: each `command()` built via `vyos_op_command()` with the package marker; `process()` requires-and-strips the marker (via `_parse`) before parsing; the three parse behaviors and defaults from the §3 table; `strip_private=True` appends the literal escaped `\|` `strip-private` op-pipe argument on `ConfigurationCommands`. Docstrings carry the §3 secret-boundary statement for `Configuration` / unredacted `ConfigurationCommands` and the not-restore-faithful note for `strip_private`.
- Companion: `tests/test_facts.py` — rewrite: command-rendering assertions (marker present, `strip_private` variant), `process()` over the Phase C fixtures (marker-wrapped success, missing-marker failure, empty-payload-with-marker fails loudly via parser, `default()` values).
- Delete `src/pyinfra_vyos/_gitconfig.py` + `tests/test_gitconfig.py` now if deferred at B2 and `operations.py` is being replaced in the same change as B5; otherwise defer to B5.

### B5. `src/pyinfra_vyos/operations.py` — `config_load`

- Replaces `config_entry`. Keeps `_guarded` → `OperationValueError`; the sample's read-fact/diff/noop idempotency loop is intentionally **not** kept — §2 marks the op `@operation(is_idempotent=False, idempotent_notice=…)` with device-side gating (D3).
- Contents per §2, in order: signature `config_load(src, *, save: bool = False)` and the exact `src` contract (deploy-dir path resolution matching `files.put`, seekable file-like); controller-side streaming non-empty check (via `_parse`), TOCTOU note; per-execution staging token from `_session`; the seven yielded commands exactly as listed (probe → `mkdir -m 700` → config upload → `chmod 600` + remote grep guard → script upload via `StringIO` → `chmod 600` → `sg vbash … ; rm -rf ; exit $rc`). Docstring must state the §2 concurrency precondition (D4), the cleanup-honesty residual, the commit-verify-save guidance for the severed-SSH risk (§6), and the save-only-run-reports-changed sentinel behavior. Parameter names `src`/`save` are safe against the reserved-argument meta-test; keep it that way for any helper params.
- Companion: `tests/test_operations.py` — rewrite: argument validation (unreadable/empty src, non-seekable file-like rejected), yielded-command sequence/ordering assertions from the prepare generator (probe first, chmod after each upload, cleanup chained onto command 7), staging-token freshness across two generator evaluations.
- Delete `_gitconfig.py` / `test_gitconfig.py` remnants now at the latest.

### B6. `src/pyinfra_vyos/__init__.py` — public surface

- Rewrite docstring (layer map: `facts` / `operations` / `_session` / `_parse` / `_cli`) and re-exports: `Version`, `Configuration`, `ConfigurationCommands`, `config_load`; matching `__all__`. `py.typed` stays.
- Acceptance: reserved-argument meta-test in `tests/test_cli.py` passes against the new `__all__`.

### Gate 2

`moon run root:check` passes on the completed domain (format, lint, lock, mypy strict, unit tier, build, scripts-test, docs build).

---

## Phase C — tests: kept / replaced / fixtures / tiers

### Kept unchanged

- `tests/conftest.py` — `--integration` flag + skip logic.
- Reserved-argument meta-test in `tests/test_cli.py`.
- `tests/integration/_helpers.py` pyinfra harness half: `new_state`, `prepare`, `apply`, `fact_value`. Delete the git-CLI half (`try_git`, `run_git`, `init_repository`, `config_value`).

### Replaced (mapped in Phase B)

| Template file | Replacement | Step |
|---|---|---|
| `tests/test_gitconfig.py` | `tests/test_parse.py` + `tests/test_session.py` | B1, B2 |
| `tests/test_cli.py` (builder tests) | VyOS builder tests, meta-test kept | B3 |
| `tests/test_facts.py` | fixture-driven fact tests | B4 |
| `tests/test_operations.py` | command-sequence tests | B5 |
| `tests/integration/test_gitconfig.py` | `tests/integration/test_vyos.py` | below |

### Fixtures (module-level constants in the test files, matching template convention — no fixtures directory)

- `show version` sample output for 1.4 (sagitta) and 1.5 (circinus), plus a variant with an unknown label and one missing an optional field. Source: research-report/branch-source examples; mark provenance in a comment — these are *not* appliance-captured (§6 fixture backlog).
- `show configuration json` sample: small valid tree; a non-dict top-level; invalid JSON.
- `show configuration commands` sample lines, incl. a `strip-private`-redacted variant.
- Marker-wrapped and marker-missing payload variants for the fact `process()` tests.
- A small valid VyOS config text + an all-whitespace file for `config_load` src checks.

### Integration tiers

- **`@local` tier** (`tests/integration/test_vyos.py`, `pytestmark = integration`, runs under `moon run root:test-integration`): what it can meaningfully cover is prepare-phase behavior and graceful degradation — `prepare(config_load, …)` renders the full seven-command sequence through the real pyinfra API without executing; src-contract rejections surface as `OperationValueError` through the real op wrapper; facts on `@local` (no `vbash`) return `default()` via `requires_command`. The template's apply-twice idempotency assertion does **not** carry over for `config_load` (is_idempotent=False by design); do not fake it.
- **Appliance tier** (new marker `appliance` in `pyproject.toml` markers + conftest skip unless `--appliance` and a target env var, e.g. `PYINFRA_VYOS_TEST_HOST`; no moon/CI wiring — deliberately opt-in and manual): real `Version`/`Configuration`/`ConfigurationCommands` values, `config_load` changed/noop/save sentinels, `sessionChanged` tri-state behavior on 1.4/1.5 (§6 unknown #1), and capture of real outputs to replace the provisional unit fixtures. Write the suite; it is expected to be run only by a human with an appliance.

---

## Phase D — docs and README

- `README.md`: what the package is (SSH-native VyOS config substrate for pyinfra), install, quickstart showing `config_load` + facts, and the **commit-verify-save pattern** as the canonical example: `config_load(src)` (no save) → verify via facts / reachability → `config_load(src, save=True)`. Must state the concurrency precondition and the secret-boundary/controller-logs caveat — these are user-facing contracts (§2, §3), not internals.
- `docs/docs/index.md`: same content shape, plus fact reference (three facts, arguments, defaults, `strip_private` caveat) and the §6 risk disclosures relevant to operators (stranded staging residual, changed-vs-device-noop reporting).
- `CONTRIBUTING.md`: verify only; template content is repo-generic.
- `SECURITY.md` — **[USER-assisted]**: replace with a real policy; before dropping the "Known upstream advisories" section, re-check whether pyinfra now admits `paramiko>=5` (carry the note forward if not).
- Delete `DELETE_ME.md`.

---

## Phase E — verification gates and smoke

1. **Gate 1** (end of Phase A): `moon run root:check` green under the new names, sample domain intact.
2. **Gate 2** (end of Phase B): `moon run root:check` green on the real domain.
3. **Final gate** (after Phases C–D): `moon run root:check` **and** `moon run root:test-integration` green.
4. **Smoke** (not a test file): build the wheel (`moon run root:build`), install it into a scratch venv, and `python -c "from pyinfra_vyos import Version, Configuration, ConfigurationCommands, config_load"` — proves the packaged artifact, not just the src tree, exposes the wave-1 surface. Optionally render `prepare(config_load, src=<sample>)` against `@local` once more from the installed package.
5. **[USER]** Appliance smoke when hardware is available: run the `--appliance` suite against a lab VyOS 1.4/1.5; feed captured outputs back into the B1/B4 fixtures (tracked as the §6 fixture backlog item).

---

## Risks / open questions for the implementer

- **`files.put` path-resolution parity** (§2): the architecture requires "the same rule `files.put` uses" for `str` src. Read the pinned pyinfra source (uv.lock: 3.10.0) for the actual deploy-dir resolution before implementing; do not guess, and pin the behavior with a unit test.
- **Marker value** (§3): architecture fixes the mechanism, not the string. Pick one package-controlled constant in `_session`/`_parse` (e.g. incorporating `PYINFRA_VYOS`), used by both facts and available to future ops; make collision-with-payload implausible, not merely unlikely.
- **`idempotent_notice` / `@operation` kwargs**: verify the exact metadata parameter names available in the pinned pyinfra (`>=3.9.2,<4`); the architecture's `is_idempotent=False, idempotent_notice=…` must map onto real API, adjusted only in spelling.
- **`FileUploadCommand` construction**: the template never uploads files; confirm the pyinfra API for yielding `FileUploadCommand` with a `StringIO` source (seek(0) behavior per §2) from an operation generator.
- **Fixture provenance**: 1.4/1.5 outputs used in unit tests are provisional until appliance-captured (§2 load-failure residual, §6). Keep the provenance comments so the backlog item stays visible.
- **Deletion timing of `_gitconfig.py`**: B2 vs B4/B5 — implementer's discretion, constrained only by "check stays meaningful at every step".
- **Appliance-tier env contract** (host var name, credentials source): implementation discretion; keep it out of CI entirely.