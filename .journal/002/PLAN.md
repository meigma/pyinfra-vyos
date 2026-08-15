# pyinfra-vyos — wave-2 implementation plan

Produced by the wave-2 planner agent; implements `.journal/002/ARCHITECTURE.md` (same folder; authoritative — bare §N references below are to it, DN are its decisions).

Executes the accepted wave-2 architecture over the merged substrate (`config` op, `_tree.py`, `_session.py`, `_cli.py`, three facts, three test tiers). Executor: coding agents. Each phase is one PR-sized, independently shippable unit with a Conventional Commit PR title (G1/G2); phases after the first may adapt mechanics as reality dictates, but the architecture's decisions D6–D13 are not renegotiable here. Steps within a phase are ordered by dependency; each names its file targets and acceptance.

Conventions carried from wave 1: unit tier is mock-free and fixture-driven; `@local` tier is prepare/assembly integration only (never T2/T3 evidence); appliance tier (`--appliance` + `PYINFRA_VYOS_TEST_HOST`) carries the behavioral proof and is opt-in, never CI-gating. `moon run root:check` must be green at the end of every phase; `moon run root:test-integration` at every phase that touches operations or facts.

## Phase map

| Phase | PR title (squash subject, G1) | Ships | Depends on |
|---|---|---|---|
| 1 | `feat: gate script save on commit and add config_save operation` | `_tree` root generalization, `PlannedCommand` + sensitive suppression, D13 save gating, `build_save_script`, `session_run_sequence`, `config` migration, `PendingSave`, `config_save` | merged substrate |
| 2 | `feat: add system_basics operation` | `_render.py` (Scope algebra, `schema_key`), `_plan_scopes`, `system_basics`, typed-op `@local` `vbash` fixture | Phase 1 |
| 3 | `feat: add interface operation` | `interface` | Phase 2 |
| 4 | `feat: add static_route operation` | `static_route` | Phase 2 |
| 5 | `feat: add user operation` | `user` + deletion guard | Phase 2 |
| 6 | `feat: add firewall_group operation` | `firewall_group` + group-type member-leaf table | Phase 2 |
| 7 | `feat: add firewall_ruleset operation` | `firewall_ruleset` | Phase 2 |
| 8 | `docs: document the typed operation surface` | README / docs-site update | Phases 1–7 |

Phases 3–7 are mutually independent (parallelizable across worktrees); the only shared file hotspots are `_render.py`, `operations.py`, `__init__.py`, and the test modules — coordinate merge order, rebase mechanically. The order above is the architecture's §10 cut order (`config_save` + access path first: `system_basics`, `interface`, `static_route`, `user`; firewall pair second) and is the order to follow when serializing.

§12 open-question ledger (resolved in the named step): Q1 → step 1.1; Q2 → step 1.8 (appliance rejected-commit probe); Q3 → step 2.6 (system appliance scenario); Q4 → step 3.2 (resolved-as-rejected, docstring note).

---

## Phase 1 — substrate extensions + `config_save`

**PR**: `feat: gate script save on commit and add config_save operation`. This PR is user-visible behavior (`config`'s save block stops persisting on canonicalization-degraded runs; a new op and fact ship), hence `feat:` not `refactor:`.

Everything the typed ops stand on lands here, so Phases 2–7 never touch the session machinery again. The phase is shippable on its own: D13 closes a real gap in the shipped `config`, and `config_save` completes the verify-then-persist workflow (§7) independently of any typed op.

### 1.1 Verify the pyinfra fact-cache premise (resolves §12 Q1)

- The §5 coherence model requires `Host.get_fact` to be cache-free on **every** supported pyinfra (`>=3.9.2,<4`). The pinned 3.10.0 is verified in the architecture; the 3.9.x floor is not.
- Do: install `pyinfra==3.9.2` into a scratch venv (`uv venv` + `uv pip install`), read its `pyinfra/api/host.py` / `api/facts.py` `get_fact` path. If any supported release caches fact values, raise the floor in `pyproject.toml` to the first cache-free release and `uv lock` — never depend on private state (L2, §10). If cache-free, record the finding in the PR description and change nothing.
- Targets: `pyproject.toml` + `uv.lock` (only on a raised floor).
- Acceptance: a one-line verdict with the inspected source location, in the PR description. This is a dependency-floor decision, not a design change.

### 1.2 `_tree.py` — generalize the diff root to `Node | None`

- `select_subtree(config, path) -> Node | None`: a **leaf** at the exact path now returns its normalized `list[str]` (today it returns `{}`); a subtree still returns the normalized `dict`; an absent path still returns `None`. This makes leaf state visible to `Exact` leaf scopes (§3 — the root cause the old `fields` mode papered over).
- `diff_tree(active: Node | None, desired: Node | None, path, *, replace)`: root accepts leaf, subtree, or absent on **both** sides. Semantics: leaf-vs-leaf compares as unordered value sets at `path` (emitting `[*path, value]` sets/deletes); leaf-vs-dict and dict-vs-leaf clear-then-set under `replace` exactly as `_diff_node` already does one level down; `desired=None` is not used by callers (absence is the planner's `Absent` intent) — reject it or exclude it from the signature, implementer's choice, but do not invent a delete mode here (§2: "no new diff modes"). Dict-rooted calls must be byte-identical in output to today.
- The existing `active is None and empty delta → bare presence set` special case is unchanged.
- Targets: `src/pyinfra_vyos/_tree.py` (`select_subtree`, `diff_tree`, `Node` alias in `__all__` if the planner needs it), companion `tests/test_tree.py`.
- Tests: new root cases — leaf-at-path selection, leaf-root diff (set-only, delete-only, mixed, no-change), absent root, leaf/subtree shape flips at the root; **existing dict-rooted cases stay untouched and green** (they are the behavior-preservation guard the architecture names).
- Dependencies: none. Acceptance: `test_tree.py` green; no other module changed.

### 1.3 `_session.py` — `PlannedCommand`, sensitive suppression, D13 save gating, `build_save_script`

Three bounded extensions (§2), one step because they all live in this module and its test file:

1. **`PlannedCommand`**: frozen dataclass `(argv: list[str], sensitive: bool = False)`, exported. Defined here (not `_render.py`) because `_session` consumes it and must stay import-clean of the renderer layer; `_plan_scopes` (Phase 2) produces it. `build_commands_script(staging_dir, commands: Sequence[PlannedCommand], *, save)` replaces the `Sequence[Sequence[str]]` signature — clean cutover, no compatibility overload; the only caller (`config`) migrates in step 1.5 of this same PR. Keep the existing nonempty / `set`|`delete`-verb validation against `command.argv`.
2. **Sensitive suppression (D11 carrier, §3/§8)**: in the per-command failure branch, when `command.sensitive`, print the existing ordinal/verb line unchanged and then a fixed literal — `device output suppressed (sensitive command)` — instead of `"$_cmd_out"`. Non-sensitive commands keep today's captured-output diagnostic. No layer may re-derive sensitivity from argv text.
3. **D13 save gating + `build_save_script`**: wrap `build_commands_script`'s save block in `if [ "$did_commit" -ne 0 ]` (mechanically: give `_epilogue` a `gate_save_on_commit: bool` or split a gated variant — implementer's choice; the load path must be provably untouched). `build_load_script` keeps its unconditional idempotent save (`config_load` documents save-only runs). New `build_save_script(staging_dir) -> str`: `_prologue` (the configure session is required — `save` is a config-mode word under script-template, and the EXIT trap owns teardown/staging removal) + the existing needs-save/save block **ungated** + sentinel emission + `builtin exit 0`. No commands, no commit gate; sentinel truthful (`did_save` drives `SENTINEL_CHANGED`).

- Targets: `src/pyinfra_vyos/_session.py` (`PlannedCommand`, `build_commands_script`, `_epilogue` or successor, `build_save_script`, `__all__`), companion `tests/test_session.py`.
- Tests (script-text contract, per §11): commands-script save block gated on `did_commit` (present when `save=True`, gate line present, absent when `save=False`); load-script save block **ungated** (separate test — both behaviors asserted independently); sensitive-command failure branch prints the suppression literal and never `$_cmd_out`, non-sensitive branch unchanged; `build_save_script` load-bearing lines — pager before source, trap installed, `inSession` assert, needs-save check, no `commit`, no `set`/`delete`, sentinel strings match constants, every terminating path `builtin exit`.
- Dependencies: none (parallel with 1.2). Acceptance: `test_session.py` green; `build_commands_script` has exactly one signature.

### 1.4 `_cli.py` — `session_run_sequence`

- New `session_run_sequence(staging: str, script_text: str) -> list[StringCommand | FileUploadCommand]` assembling the shared five-command tail (A3): `sg_probe()` → `StringCommand("mkdir", "-m", "700", QuoteString(staging))` → `FileUploadCommand(StringIO(script_text), f"{staging}/session.sh")` → `StringCommand("chmod", "600", QuoteString(script_path))` → `sg_vbash_run(script_path, staging)`. The script path is derived here, not passed in — one derivation point. Add the `FileUploadCommand`/`StringIO` imports; the module remains the single command-assembly point and still executes nothing.
- Used by `config` (1.5), `config_save` (1.7), and every typed op (Phases 2–7). **`config_load` is untouched** — its seven-command flow stays inline in `operations.py`; that inline assembly is recorded pre-existing A3 debt, out of wave (§2). Do not "clean it up" in passing.
- Targets: `src/pyinfra_vyos/_cli.py`, companion `tests/test_cli.py`.
- Tests: returned sequence length/order/types; rendered strings for the mkdir/chmod commands (quoting of a staging path containing a space); upload `src.getvalue()` round-trips `script_text`; dest paths end `/session.sh`. The reserved-argument meta-test stays verbatim.
- Dependencies: none. Acceptance: `test_cli.py` green.

### 1.5 `operations.py` — migrate `config` onto the new seams (behavior-preserving)

- `config` builds `list[PlannedCommand]` (all `sensitive=False`) instead of `list[list[str]]`, calls `build_commands_script`, and replaces its inline five-yield tail with `yield from session_run_sequence(staging, script_text)`. No signature, docstring-contract, or ordering change — except the docstring's save sentence, which now truthfully matches the script (D13): keep "save=True persists only when this run commits" as-is; it was already documented, only the script lagged.
- Targets: `src/pyinfra_vyos/operations.py` (`config` only).
- Verification: **the existing `@local` tests in `tests/integration/test_vyos.py` are the guard** — `test_config_prepare_renders_the_five_command_sequence`, delete-before-set ordering, bare-path creation, rejection surfacing, save propagation must pass unmodified (save-propagation asserts may need the gate line added to their expected script text — that is the one intentional behavior change, adjust the assertion to assert the gate, not around it).
- Dependencies: 1.2, 1.3, 1.4. Acceptance: `root:test-integration` green with at most the D13-related assertion update.

### 1.6 `facts.py` + `_parse.py` + `_cli.py` — `PendingSave`

- `_cli.py`: new builder (e.g. `pending_save_probe(marker)`) wrapping the cli-shell-api active-vs-`/config/config.boot` comparison in `vbash -c` with the package marker. The comparison command string (`_NEEDS_SAVE` today) stays defined in `_session.py` (pure module, no pyinfra import) and is imported by `_cli.py` — one constant, two consumers. The device-side pipeline must reduce the diff to a **marker value** (e.g. `| wc -c` byte count) so config text — potentially secret-bearing — never transits or lands in fact logs (§2).
- `_parse.py`: parser turning the marker-stripped payload into `bool` (nonzero count → `True`); malformed payload raises (→ `FactProcessError` per `_fact_process`).
- `facts.py`: `PendingSave(FactBase[bool | None])` — `default()` returns `None`; `requires_command` → `"vbash"`; `command()` via the new `_cli` builder; `process()` marker-strip + parse. Contract (F1–F3, A5): **any** failure path — missing `vbash`, command failure, unparseable output — must end in `None`, never `False`; `False` is only ever a successful "comparison ran, no diff". Docstring states the tri-state and that `config_save` fails closed on `None`.
- Targets: `src/pyinfra_vyos/_cli.py`, `_parse.py`, `facts.py`; companions `tests/test_cli.py`, `tests/test_parse.py`, `tests/test_facts.py`.
- Tests: builder rendering (marker present, reduction pipeline present, no raw `showConfig` output path to stdout); parser truth table incl. malformed → raise; fact `default() is None`, command/process wiring over marker-wrapped fixtures.
- Dependencies: 1.3 (constant location). Acceptance: unit green.

### 1.7 `operations.py` + `__init__.py` — `config_save`

- `@operation()` `config_save()` — no arguments (§4): fetch `PendingSave`; `None` → `OperationValueError` stating saved-state could not be established (never a clean noop, A5); `False` → `host.noop("configuration already saved")`; `True` → `staging_dir()` + `yield from session_run_sequence(staging, build_save_script(staging))`. Docstring carries verbatim the §4 device-global-persistence paragraph, the §7 verify-then-persist workflow (this op is its second phase), and the D4 concurrency precondition.
- `__init__.py`: export `config_save` and `PendingSave`; extend the docstring layer map. The reserved-argument meta-test now covers `config_save` automatically via `__all__` (it has no arguments — trivially clean).
- Targets: `src/pyinfra_vyos/operations.py`, `src/pyinfra_vyos/__init__.py`; companions `tests/test_operations.py`, `tests/integration/test_vyos.py`.
- `@local` tests: `prepare(config_save)` on `@local` (no `vbash`) → `PendingSave` is `None` → `OperationValueError` surfaces through the real op wrapper — this **is** the fail-closed test; the five-command prepared sequence cannot be asserted on `@local` without a fake probe, so the sequence assertion lives at unit level against `session_run_sequence` + `build_save_script` composition (already covered by 1.3/1.4) — do not fake a `PendingSave=True` path through private state.
- Dependencies: 1.3, 1.4, 1.6. Acceptance: unit + `@local` green.

### 1.8 Appliance additions (opt-in; resolves §12 Q2)

- `tests/integration/test_appliance.py`, marked `appliance`:
  1. **`config_save` scenario**: dirty the active config via `config(..., save=False)` on a scratch path → `config_save()` reports changed (sentinel) → boot file reflects it (independent read of `/config/config.boot` or the needs-save probe) → second `config_save()` noops (`meta.will_change` false / noop reported) → clean up scratch path with `config(present=True→False, save=True)`.
  2. **Rejected-commit probe (Q2)**: one session via `config` whose commit the device refuses (e.g. two `set`s where the second creates a commit-invalid combination on a scratch subtree), then independent op-mode reads asserting whether **any** partial active change persisted. Record the observation in the test's docstring and in `.journal/002/NOTES.md` as a **lab-release data point, not a guarantee** — docstring language elsewhere stays conservative regardless of outcome (§7, §11).
- Dependencies: 1.7. Acceptance: suite runs green against the lab appliance when hardware is available; not CI-gating.

### Phase 1 verification

- Unit: steps 1.2–1.7 companions; `moon run root:check` (format, lint, lock, mypy strict, unit, build, docs).
- `@local`: full `tests/integration/test_vyos.py` under `moon run root:test-integration` — the pre-existing `config`/`config_load` tests are the behavior-preservation proof for the migration.
- Appliance (opt-in): 1.8 scenarios; additionally re-run the existing `config` scoped cycle to confirm the migrated op against hardware.

### Phase 1 risks

| Risk | Mitigation |
|---|---|
| `config` migration silently changes prepared output | The `@local` sequence tests are strict on ordering/content and run pre-change; only the D13 gate assertion may change, and that diff is reviewed as the intentional behavior change |
| D13 gate accidentally applied to `build_load_script` | Separate unit tests assert gated (commands) and ungated (load) independently (§7: "both behaviors are unit-tested separately") |
| `PendingSave` device pipeline leaks config text into fact output | Unit test asserts the rendered command reduces to a count before anything reaches stdout; appliance scenario eyeballs captured fact output once |
| 3.9.x floor caches facts (Q1) | Step 1.1 resolves it first; contingency is a floor raise, one line + relock |
| `PlannedCommand` cutover misses a caller | `build_commands_script` accepts only the new type; mypy strict fails any un-migrated caller at `root:check` |

---

## Phase 2 — renderer seam + `system_basics`

**PR**: `feat: add system_basics operation`. Carries the typed-op scaffolding every later phase reuses: `_render.py`, `_plan_scopes`, the version gate, and the `@local` `vbash` fixture. `system_basics` is deliberately the pilot — smallest surface (five `Exact` leaf scopes, no secrets, no `present=False`, no `values`).

### 2.1 `src/pyinfra_vyos/_render.py` — new pure module (A2)

- Contents (§2, §3): `Scope` frozen dataclass `(path: list[str], intent, sensitive: bool = False)`; intent types `Absent` (marker), `Exact(node: Node)` (normalized leaf `list[str]` or subtree `dict`), `Merge(subtree: dict)`; a module-local `RenderError(ValueError)` added to `operations._DOMAIN_ERRORS` in 2.3; `schema_key(version_string) -> str` mapping `1.4.*` → `"1.4"`, `1.5.*` → `"1.5"`, qualified rolling `2026.03.*` → `"1.5"`, everything else (bare Stream labels, later rolling, garbage, empty) → `RenderError` naming the version string and the `config`/`config_load` escape hatches (D9, appendix B item 3); int→`str` coercion helper for ergonomic MTU/rule numbers applied **before** token rules; per-op renderer functions land in their own phases.
- All tokens renderers emit flow through `_tree`'s validation (`normalize_tree(strict=True)` / `_require_token` equivalents) so C2 holds without a second validator. Secret-field validation errors name the field, never the value (used from Phase 5; the redaction convention is established here in the module docstring).
- Targets: `src/pyinfra_vyos/_render.py`, new companion `tests/test_render.py`.
- Tests: `schema_key` full mapping table — `1.4.x`, `1.5.x`, qualified `2026.03.x`, and fail-closed cases: bare `"circinus"`/`"stream"`, unqualified `2027.01`, empty, junk (§11). A shared `assert_disjoint(scopes)` test helper implementing the §3 invariant — (a) `Exact`/`Absent` paths pairwise non-prefix, (b) ≤1 `Merge` per resource at the root with top-level keys disjoint from every exact-child next token — reused by every later phase's renderer tests.
- Dependencies: Phase 1 (Node from `_tree`). Acceptance: `test_render.py` green; module imports nothing from pyinfra.

### 2.2 `operations.py` — `_plan_scopes`

- Private planner `_plan_scopes(host, scopes: list[Scope]) -> list[PlannedCommand] | None` (§2): fetch `Configuration` **once** per call; per scope in order — `Absent`: `select_subtree` at path, delete when present; `Exact`: `diff_tree(active_at_path, node, path, replace=True)`; `Merge`: `diff_tree(active_at_path, subtree, path, replace=False)` — with deletes-before-sets **within each scope** and scope order preserved across scopes; every `PlannedCommand` inherits its scope's `sensitive`; return `None` on a globally empty delta.
- Guard against the `Merge({})` + `diff_tree` bare-presence special case: `Merge({})` on an existing node must plan empty, on an absent node must plan the presence `set` (this is the "ensure bare node" semantics `interface`/`user` rely on) — pin with a unit test now.
- Targets: `src/pyinfra_vyos/operations.py`; companion `tests/test_operations.py` (planner tested through op-level `@local` prepare where possible; direct unit tests may drive it with a stub host only if the `@local` path cannot reach a branch — prefer the real pipeline).
- Dependencies: 2.1, Phase 1. Acceptance: planner behavior pinned for all three intents, ordering, sensitivity inheritance, empty → `None`.

### 2.3 `operations.py` — `system_basics`

- `@operation()` `system_basics(*, hostname=None, domain_name=None, name_servers=None, search_domains=None, time_zone=None, save=False)` (§4): validation order per §4 — (1) schema-independent: all-`None` → `OperationValueError`; (2) `Version` fact → `schema_key` (default/empty `Version` → the same fail-closed `OperationValueError` naming the escape hatches); (3) renderer: one `Exact` leaf scope per provided kwarg (`host-name`, `domain-name`, `name-server`, `domain-search`, `time-zone`), `[]` on the list fields → `Absent` at the leaf (own-and-empty), multi-value set-equality; (4) `Configuration` + `_plan_scopes` → `host.noop` labelled with resource identity only ("system basics already match") or `yield from session_run_sequence(staging, build_commands_script(staging, planned, save=save))`.
- Docstring: per-field `None`-unmanaged semantics; scalar-field removal out of model with `config(path=…, present=False)` as the escape hatch; D4 concurrency precondition; save/D13 semantics; the §12 Q3 caveat (domain-name/domain-search interactions are device-validated, commit output is the diagnostic).
- Renderer function `render_system_basics(schema, ...) -> list[Scope]` lives in `_render.py` (targets there too).
- Targets: `src/pyinfra_vyos/_render.py`, `operations.py`, `__init__.py` (`system_basics` export — reserved-argument meta-test now covers it: `hostname` et al. verified clean against `all_argument_meta`, §1).
- Dependencies: 2.1, 2.2.

### 2.4 `@local` `vbash` fixture (appendix B item 4)

- New pytest fixture (in `tests/integration/_helpers.py` or a conftest local to `tests/integration/`): writes an executable `vbash` shim into a temp dir and prepends it to `PATH` via `monkeypatch.setenv`. The shim inspects its `-c` payload: `show version` → canned marker-wrapped payload whose `version` field maps through `schema_key` (use the qualified-rolling lab form so the mapping the appliance relies on is exercised end-to-end); `show configuration json` → `{}` payload with marker. Everything else → nonzero exit.
- Fixture is shared infrastructure for Phases 2–7. Substrate tests keep exercising the no-`vbash` defaults — do not autouse it.
- Targets: `tests/integration/_helpers.py` (or sibling conftest).
- Acceptance: with fixture, `fact_value(Version)` returns the canned mapping through the real pyinfra pipeline; without, existing default tests still pass.

### 2.5 `@local` prepare/assembly tests for `system_basics`

- In `tests/integration/test_vyos.py`: with the fixture — prepared five-command sequence (tail identical in shape to `config`'s); empty active tree → all provided fields plan as sets inside the uploaded script; second scenario where desired equals the fixture's active tree → noop. Without the fixture — the op raises the fail-closed unknown/default-Version `OperationValueError` (separate test, per appendix B item 4). All-`None` rejection surfaces as `OperationValueError` through the real wrapper.
- Dependencies: 2.3, 2.4.

### 2.6 Appliance scenario (resolves §12 Q3 as observed behavior)

- `test_appliance.py`: session-002 shape — set hostname+name-servers → independent op-mode read (`show configuration json` subtree / `show host name`) → second apply noops (T3) → mutate one field → restore original values. Exercise `domain_name` and `search_domains` together once and record the device's verdict (accepts or commit-rejects) in the test docstring and `.journal/002/NOTES.md` — Q3 is answered by observation, never encoded as controller validation.
- Canonicalization hotspots to probe: time-zone string forms, name-server ordering (set-equality must hold).
- Dependencies: 2.3.

### Phase 2 verification

- Unit: `test_render.py` (schema_key table, disjointness helper, system renderer scope emission incl. `[]` → `Absent`, all-`None` rejection at renderer or op layer), `test_operations.py` planner cases, per-schema emitted-grammar fixture asserting the `system` leaf paths match the R§2 modern baseline for both schema keys (§11).
- `@local`: 2.5. Gates: `root:check` + `root:test-integration`.
- Appliance (opt-in): 2.6.

### Phase 2 risks

| Risk | Mitigation |
|---|---|
| `Version` fact's `version` field format mismatches `schema_key` expectations (e.g. `"VyOS 2026.03…"` vs `"2026.03.x"`) | Check the appliance capture from wave 1 / session 002 before writing the mapping; unit fixtures use the captured literal form; the `@local` fixture uses the same literal |
| `Merge({})`/`diff_tree` presence special case emits spurious sets on existing nodes | Pinned by a dedicated planner unit test in 2.2 before any op relies on it |
| Version gate breaks `config`/`config_load` | They remain ungated by construction (D9) — no shared code path; existing tests prove it |
| Fixture shim diverges from real device output shape | Shim payloads copied from appliance captures, provenance-commented (wave-1 fixture convention) |

---

## Phase 3 — `interface`

**PR**: `feat: add interface operation`.

### 3.1 `_render.py` — `render_interface` + interface-type table

- Per-schema table `{"1.4": {ethernet, loopback, dummy}, "1.5": {…same…}}` — identical today, keyed anyway (D9). Unknown `interface_type` → `RenderError` naming allowed types.
- Scopes (§4): `Exact` at `address` (exact set; `[]` = own-and-empty → `Absent` at the leaf, mirroring `system_basics` list semantics), `Exact` at `description`, `Exact` at `mtu` (int coerced); `disabled` tri-state — `True` → `Exact({})` at `disable`, `False` → `Absent` at `disable`, `None` unmanaged; `values` → `Merge` at the interface path with typed-key collision rejection (`address`, `description`, `mtu`, `disable`); all typed args `None` + no `values` → `Merge({})`; `present=False` → single `Absent` at the interface path with every desired arg required unset (uniform §4 rule).
- Targets: `src/pyinfra_vyos/_render.py`, `tests/test_render.py`.

### 3.2 `operations.py` — `interface` op (resolves §12 Q4)

- Signature per §4; validation order per §4 (schema-independent → Version/schema → render → Configuration/plan). Noop label: interface identity only (`"interface ethernet eth0 already matches"`).
- Docstring: per-field ownership (device-owned leaves like `hw-id` survive — never whole-subtree replace); management-interface address changes are a lockout class — commit is immediate, `save=False` limits reboot persistence only, verify-then-`config_save` workflow, out-of-band recovery assumption (§7); **Q4 recorded**: `interface_type` is explicit by design — name-prefix inference rejected for wave 2 as magic, revisit only with user-friction evidence.
- Targets: `operations.py`, `__init__.py` export.
- Dependencies: 3.1, Phase 2.

### 3.3 Tests

- Unit: disjointness across the full kwarg matrix (typed fields + `values` + `disabled` simultaneously — the round-1 counterexample class); tri-state `disabled`; collision rejection; `Merge({})` fallback; `present=False` + any desired arg → error; int MTU coercion; emitted-grammar fixture for `interfaces <type> <name>` paths under both schema keys.
- `@local` (fixture from 2.4): prepared sequence; `present=False` on empty active tree → noop; typed-arg validation errors surface as `OperationValueError`.
- Appliance: full cycle on a **`dummy` interface** (`dum0` — no cable, no lockout risk): create with address+description+mtu → independent op-mode read → second-apply noop → change address set (probe canonicalization hotspot: CIDR form the device echoes) → `disabled=True/False` transitions → `present=False` → delete-noop. Learned canonical address/description forms go in the docstring, not normalization code (§10).

### Phase 3 risks

| Risk | Mitigation |
|---|---|
| Address canonicalization (device rewrites CIDR/derives forms) degrades to perpetual re-emission | Same truthful degradation as `config`; appliance test observes the actual echo on `dummy`; document canonical form |
| Ethernet scenarios risk management lockout on the lab box | Appliance tests use `dummy` only; ethernet path is grammar-identical (fixture-asserted) and explicitly *not* hardware-verified for wave 2 — stated in NOTES |
| `disable` node shape differs from `Exact({})` expectation | The 1.2 leaf-root generalization handles leaf-vs-`{}` shape flips; unit case pinned; appliance observes |

---

## Phase 4 — `static_route`

**PR**: `feat: add static_route operation`.

### 4.1 `_render.py` — `render_static_route`

- AF dispatch via `ipaddress.ip_network(destination)` (invalid → `RenderError`, schema-independent so it runs before fact reads): v4 → `protocols static route <dest>`, v6 → `route6`. Note `ip_network` rejects host-bit-set prefixes by default — decide `strict=False` + re-render vs reject, and pin the choice with a unit test; recommend reject (caller states intent exactly), documented.
- Whole-object total body (§4, appendix B item 6): `next_hops` list form → `{"next-hop": {addr: {}}}`; dict form → `{"next-hop": {addr: per_hop_subtree}}`; merged with `values`; `values["next-hop"]` → typed-key collision rejection; body must be nonempty when `present=True` (`next_hops` nonempty or `values` keyed) — both empty → planning error; one `Exact` subtree scope at the route path; `present=False` → `Absent`, desired args unset.
- Targets: `_render.py`, `tests/test_render.py`.

### 4.2 `operations.py` — `static_route` op

- Signature/validation order per §4. Docstring: total-body pruning semantics (undeclared active next-hops are removed); blackhole/reject/interface routes ride in `values`; route changes can sever SSH — lockout class, §7 workflow, out-of-band recovery assumption.
- Targets: `operations.py`, `__init__.py`.
- Dependencies: 4.1, Phase 2.

### 4.3 Tests

- Unit: AF dispatch (v4/v6/garbage/host-bits); list-vs-dict `next_hops` normalization; collision; empty-body rejection; total-body prune diff (active extra hop → delete); emitted-grammar fixture for `route`/`route6` paths.
- `@local`: prepared sequence; rejections through the real wrapper.
- Appliance: cycle on documentation prefix `192.0.2.0/24` (unroutable, no lockout): create two next-hops → read → noop → drop one hop (prune observed) → dict-form per-hop attribute (e.g. `distance`) → delete → delete-noop. Canonicalization hotspots: destination normalization, v6 compression (`::` forms) — probe one v6 route.

### Phase 4 risks

| Risk | Mitigation |
|---|---|
| IPv6 textual canonicalization mismatch (device compresses addresses) | Appliance v6 probe; learned form documented; degradation stays truthful |
| Total-body prune removes an operator's manually added hop | That is the documented whole-object contract; docstring states it prominently with `config` merge as the alternative |

---

## Phase 5 — `user`

**PR**: `feat: add user operation`.

### 5.1 `_render.py` — `render_user` + secret validation (D11)

- Scopes (§4): `Exact` at `full-name`; `Exact` at `authentication encrypted-password` — **`sensitive=True`**; `Exact` subtree at `authentication public-keys` (exact set of `dict[key_id, {"type": …, "key": …}]`); `None` unmanaged; all-`None` → `Merge({})` at the user path; `present=False` → `Absent` with desired args unset.
- `encrypted_password` validation: must start `$` or be `!`/`*` lock markers; rejection error names the field and accepted forms and **never echoes the value** (unit-asserted by substring absence). No plaintext mode exists.
- Targets: `_render.py`, `tests/test_render.py`.

### 5.2 `operations.py` — `user` op + deletion guard (§8)

- Signature/validation order per §4. `present=False`: fetch `pyinfra.facts.server.User` (consumed, not defined here); reported identity equals target → planning error; empty/undeterminable → fail-closed `OperationValueError` stating the connected identity could not be established, naming `config` as the escape hatch.
- Docstring: hash-only contract with controller-side hashing pointers (`mkpasswd`, passlib); sensitive-output suppression semantics and the forwarded-commit-output residual exposure (§8 verbatim in substance); guard limits — `$USER` semantics, no last-admin detection, no remote-auth modeling, `config` overrides both; user deletion is a lockout class (out-of-band recovery assumption). Noop/error labels carry the username only.
- Targets: `operations.py`, `__init__.py`.
- Dependencies: 5.1, Phase 2 (sensitivity plumbing already proven by Phase 1 unit tests).

### 5.3 Tests

- Unit: hash acceptance table (`$6$…`, `$y$…`, `!`, `*`, plaintext-rejected-without-echo); sensitivity propagation — renderer scope → every derived `PlannedCommand` (both the delete-old-hash and set-new-hash commands) → suppression branch in the script text (ties Phase 1's `_session` tests to a real producer); ssh-key exact-set diff; disjointness (`full-name` / `authentication …` nested paths — the round-1 nested-key counterexample); emitted-grammar fixture for `system login user` paths.
- `@local` (fixture): prepared sequence; `present=False` with fixture-provided `$USER` matching target → planning error surfaces; without determinable identity → fail-closed error. (The `server.User` fact runs `echo $USER` through the connector — on `@local` it reports the test runner's user; use that real value for the match case, and an env-manipulated shim for the empty case if reachable; otherwise the empty branch is unit-covered.)
- Appliance: create `pyinfra-test` user with hash + key → independent read → noop → rotate hash (suppression path not triggered on success — correctness of suppression is unit territory) → `present=False` (connected as a different user) → delete-noop. Guard probe: attempt self-deletion of the connecting user → planning error, device untouched. Canonicalization hotspot: public-key body whitespace/format echo.

### Phase 5 risks

| Risk | Mitigation |
|---|---|
| Hash echo in any error path | Unit tests assert the literal hash never appears in rendered errors or script failure lines; suppression branch pinned in `test_session.py` since Phase 1 |
| `$USER` misleading under connector privilege transformation | Documented guard limits (§8); fail-closed on empty; `config` escape hatch named in the error |
| Deleting the lab's only admin during appliance runs | Scenario creates its own disposable user; guard probe targets the connecting user and must *fail at planning* |

---

## Phase 6 — `firewall_group`

**PR**: `feat: add firewall_group operation`.

### 6.1 `_render.py` — `render_firewall_group` + member-leaf table

- Per-schema table mapping `group_type ∈ {address, ipv6-address, network, ipv6-network, port, interface, mac, domain}` → path segment (`<type>-group`) and member-leaf name (`address`/`network`/`port`/`interface`/`mac-address`/…) — identical under both keys today, keyed anyway. Unknown type → `RenderError`.
- One `Exact` subtree scope at `firewall group <type>-group <group>` (§4): `present=True` requires `members` (`[]` = own-and-empty); body total — `description=None` prunes an active description; `present=False` requires `members` and `description` both `None` → `Absent`. Port members: int coercion for numerics; ranges pass as strings. Static groups only.
- Targets: `_render.py`, `tests/test_render.py`.

### 6.2 `operations.py` — `firewall_group` op

- Signature/validation order per §4. Docstring: total-body semantics; deleting a referenced group fails at device commit — commit output is the diagnostic (D12); dynamic/remote groups out of scope (`config` escape hatch).
- Targets: `operations.py`, `__init__.py`.
- Dependencies: 6.1, Phase 2.

### 6.3 Tests

- Unit: full `group_type` table (path + member leaf per type per schema key, fixture-asserted against the R§2 baseline); `members` requiredness matrix; `[]` own-and-empty; description prune; `present=False` arg rejection; port int coercion.
- `@local`: prepared sequence; validation surfacing.
- Appliance: address-group cycle (create members → read → noop → change member set (prune observed) → delete → delete-noop); one referenced-group deletion probe (group referenced by a scratch ruleset rule via `config`) asserting the commit-failure diagnostic path surfaces device output. Canonicalization hotspot: port-range forms (`8000-9000`).

### Phase 6 risks

| Risk | Mitigation |
|---|---|
| Member-leaf table wrong for a rarely used type (`mac`, `domain`) | Fixture-checked against the research baseline; appliance probes at least `address` + `port`; remaining types stay fixture-verified with the §6 honesty note |
| Referenced-group delete leaves partial state on commit failure | Phase 1's Q2 probe already characterized rejected-commit behavior; this scenario reuses that knowledge, asserts only the diagnostic |

---

## Phase 7 — `firewall_ruleset`

**PR**: `feat: add firewall_ruleset operation`. The largest renderer; lands last per the cut order.

### 7.1 `_render.py` — `render_firewall_ruleset`

- Chain path `firewall <af> <*chain>`, `af ∈ {ipv4, ipv6}`, `chain` an open token list validated by C2 only (base/custom/1.5-only chains pass through; §6).
- Scopes (§4): `default_action`/`description` → `Exact` leaf scopes (`None` unmanaged); `rules: dict[int|str, dict|None]` with `replace_rules=False` → one `Exact` subtree scope per rule number (int keys coerced; `{n: None}` → `Absent` at rule *n*; each non-`None` body must be a nonempty mapping — empty bodies rejected before fact reads; body is total — whole-rule replace); `replace_rules=True` → one `Exact` scope at the `rule` node (requires `rules`, may be `{}` = prune all; `None` entries rejected); `values` → `Merge` at the chain path (keys `default-action`, `description`, `rule` rejected); owns-nothing call (`present=True`, `replace_rules=False`, all of `default_action`/`description`/`rules`/`values` `None`) → error; `present=False` → `Absent` at the chain, desired args unset.
- Disjointness note the tests must pin: per-rule `Exact` scopes at `… rule <n>` are non-prefix siblings; leaf scopes (`default-action`, `description`) are non-prefix to the `rule` subtrees; `replace_rules=True` may not combine with per-rule scopes (single scope at `rule` replaces them) — the round-1 parent/child counterexample class.
- Targets: `_render.py`, `tests/test_render.py`.

### 7.2 `operations.py` — `firewall_ruleset` op

- Signature/validation order per §4. Docstring: whole-rule replace keying (R§1.1/R§5); `replace_rules=True` destructive semantics (Ansible-`overridden` analog) incl. the management-access warning; base-chain `default_action` is a lockout class (§7 workflow, out-of-band recovery); NAT users pointed at `config` (§1); cross-op ordering obligation for group references (D12).
- Targets: `operations.py`, `__init__.py` (completes the seven-op export set; `__init__` docstring layer map final).
- Dependencies: 7.1, Phase 2 (Phase 6 not required, but appliance scenario is richer after it).

### 7.3 Tests

- Unit: the §4 matrix — per-rule scope emission, `{n: None}` deletion, empty-body rejection, int rule-number coercion, `replace_rules` requiredness + `{}` prune + `None`-entry rejection, `values` collision triple, owns-nothing rejection, `present=False` arg rejection; disjointness across `default_action` + `description` + `rules` + `values` combined; emitted-grammar fixture for the modern `firewall <af>` chain model under both schema keys.
- `@local`: prepared sequence for a multi-rule call (multiple scopes → one script, one commit); owns-nothing and empty-body rejections surface pre-fact (assert no `Configuration` fetch needed — the fixture-less variant errors on the *validation* message, not the version gate, for schema-independent failures).
- Appliance: on a **custom named chain** (`["name", "PYINFRA_TEST"]` — never a base chain, no lockout): create chain with `default_action="accept"` + two rules → read → noop → mutate one rule (whole-rule replace observed: dropped leaf pruned) → `{n: None}` single-rule delete → `replace_rules=True` with `{}` (prune-all observed) → `present=False` → delete-noop. Canonicalization hotspots: rule-number echo as strings, action-value case, port/protocol forms inside rule bodies.

### Phase 7 risks

| Risk | Mitigation |
|---|---|
| `replace_rules=True` on a base chain removes management access | Explicit opt-in flag + destructive docstring (§10); appliance never touches base chains |
| Whole-rule replace fights device-inserted rule leaves | Appliance mutate step observes; any device-owned leaf discovered is documented as a canonical-form caveat, not special-cased |
| Scope explosion on large rulesets slows planning | Planning is pure controller-side tree walking; the doubled `Configuration` fetch (§5) is the accepted cost and the profiling trigger for scoped facts — no action this wave |

---

## Phase 8 — docs

**PR**: `docs: document the typed operation surface` (G2 — no consumer-visible package change).

### 8.1 `README.md`

- Extend the quickstart to the typed surface: one `system_basics`/`interface` example, then the canonical **verify-then-persist** workflow rewritten around `config_save` — `op(..., save=False)` → verify reachability/facts → `config_save()` — replacing any lingering "call again with save=True" phrasing (withdrawn, §7). State: commit is immediate / `save=False` limits reboot persistence only; save is device-global; concurrency precondition; secret boundary (controller logs sensitive; `encrypted_password` is hash-only).

### 8.2 `docs/docs/index.md`

- Operation reference for the eight ops (seven typed + `config_save`): signatures, ownership model (per-field vs whole-object, total-body pruning), `values` pass-through + collision rule, version gate + `config` escape hatch, lockout classes with the out-of-band recovery assumption, `PendingSave` fact reference (tri-state, fail-closed consumption).

### 8.3 Journal + backlog

- `.journal/002/NOTES.md`: record the Q1 verdict, the Q2 probe observation, the Q3 device verdict, canonicalization forms learned per op, and the carried debt items — `config_load` inline assembly (A3 debt, §2), fixture provenance (appliance captures replacing provisional payloads), deferred ops list (§1) with their triggers.
- Delete nothing else; wave-1 docs stand.

### Phase 8 verification

- `moon run root:check` (docs build included). Changelog handling is automatic: release-please consumes the phase PR subjects (G1) — verify the accumulated `feat:` entries read sensibly as release notes before cutting a release.

---

## Cross-phase verification summary (§11 contract)

| Tier | Where | What it proves |
|---|---|---|
| Unit (T1, mock-free) | `tests/test_render.py`, `test_tree.py`, `test_session.py`, `test_cli.py`, `test_parse.py`, `test_facts.py`, `test_operations.py` | Renderer tables + scope emission, disjointness invariant, omitted/`None`/empty matrices, int coercion, secret redaction, sensitivity propagation, collision rejection, `schema_key` mapping incl. fail-closed cases, generalized diff roots, D13 gated vs ungated save, suppression branch, `build_save_script`, per-schema emitted-grammar fixtures (R§2 baseline) |
| Prepare/assembly (`@local`) | `tests/integration/test_vyos.py` + `vbash` fixture | Rendering + assembly through the real pyinfra pipeline: prepared sequences, planning-time errors, noop on empty/matching state, fail-closed Version gate without the fixture. **Never T2/T3 evidence** |
| Appliance (opt-in) | `tests/integration/test_appliance.py` | Per op: create → controller noop (T3) → mutate → replace/delete → delete-noop, verified by independent op-mode reads (T2); `config_save` dirty/clean cycle; the rejected multi-command commit probe (Q2); per-op canonicalization hotspots (interface address forms, v6 route compression, description quoting, public-key echo, port ranges, rule-number/action forms) — learned forms recorded in docstrings and NOTES, never in normalization code |

Final smoke (end of Phase 7, repeated after Phase 8): build the wheel (`moon run root:build`), install into a scratch venv, `python -c "from pyinfra_vyos import Configuration, ConfigurationCommands, PendingSave, Version, config, config_load, config_save, firewall_group, firewall_ruleset, interface, static_route, system_basics, user"` — the packaged artifact exposes the full wave-2 surface.

## Standing risks for the implementer (whole wave)

- **Version string reality**: `schema_key`'s input is whatever the `Version` fact's `version` field actually contains on hardware — pin the literal from appliance captures before Phase 2 unit fixtures are written; a wrong assumption here fails every typed op closed (annoying) or open (unacceptable).
- **Shared-file merge pressure** (`_render.py`, `operations.py`, `__init__.py`, `test_render.py` across Phases 3–7): keep per-op renderer functions and tests in clearly separated blocks; rebases are then mechanical. Split `_render.py` per domain only when a per-version table actually diverges (§2 trigger) — not preemptively.
- **Appliance tier is evidence, not a gate**: phases merge on green unit + `@local`; appliance scenarios must exist in-tree at phase merge time and be run when hardware is available, with observations fed back into fixtures and NOTES (the wave-1 fixture-provenance backlog convention).
- **Do not extend scope**: `nat_rule`, scoped facts, typed convenience facts, commit-confirm, batching constructs, and `config_load` refactoring are all explicitly deferred (§1, §5, §7); the deferral triggers are named in the architecture — leave them there.
