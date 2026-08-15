# pyinfra-vyos — first-wave architecture

Final (v3). Produced by software-architect + architecture-reviewer, two review passes; final verdict accept-with-notes, notes applied. Research grounding: session 001 research report (`agent://VyosOpsResearch`), constraints cited as R§1.N.

Scope: `config_load` + `Version` / `Configuration` / `ConfigurationCommands` facts over the documented `sg vyattacfg` + `/bin/vbash` + script-template substrate. Origin-agnostic primitives only; callers compose SOPS/templating/backup/verification.

## 1. Module map

`src/pyinfra_vyos/` (renamed from `template_pyinfra`; sample git domain deleted):

| Module | Responsibility | Template pattern inherited |
|---|---|---|
| `_cli.py` | Assemble target commands from trusted, library-generated argv: `vyos_op_command(*argv, marker)` → `vbash -c 'source /opt/vyatta/etc/functions/script-template; run <argv…>'` plus marker emission (exactly one `run`, added here — callers pass bare op-mode argv); `sg_vbash(script_path)`; `sg_probe()`. `QuoteString` on the few interpolated values (staging paths). No `_stdin`/leading-dash machinery: this wave has no user-supplied argv. | `_cli.py` single-assembly-point contract |
| `_session.py` | Pure domain: build the session script text (termination/trap/save contracts below), generate the sentinel/exit contract constants, high-entropy staging path token | `_gitconfig.py` — no I/O |
| `_parse.py` | Pure parsers: `show version` (split each line on the *first* colon, normalize labels, require a version field, tolerate unknown/missing optionals), config-JSON `json.loads` + top-level shape check, marker require-and-strip helper, streaming non-empty check (≥1 non-whitespace byte, chunked — never a full-memory snapshot) | `_gitconfig.py` parse half |
| `facts.py` | `Version`, `Configuration`, `ConfigurationCommands` | `default()` / `requires_command()` / `_fact_process` |
| `operations.py` | `config_load` — validate → yield loop, `_guarded` error translation | `config_entry` loop |

Tests: unit tier mock-free on `_session`/`_parse`/`_cli` (script-text contract tests, parser fixtures, marker handling); integration tier keeps the real-pyinfra-API `@local` harness for prepare-phase rendering; execution against a real appliance is a marked opt-in suite.

## 2. `config_load` flow

Signature: `config_load(src, *, save: bool = False)`, marked `@operation(is_idempotent=False, idempotent_notice="device mutation is compare-gated on the target; pyinfra always reports this operation as changed")`.

**`src` contract** (exact, not "files.put-style"): `str` = controller-local path, resolved against the deploy directory with the same rule `files.put` uses (we implement that resolution); file-like = must be readable *and seekable* (pyinfra `seek(0)`s on execute). Generated script text is uploaded via `io.StringIO`. Controller-side prepare: streaming non-empty/size check on `src`; note this is TOCTOU-gapped (file can change before SFTP reads it), so the remote non-whitespace check below is the enforcing check.

**Config-version footer** (amended per Phase C review, user decision): VyOS `load` runs its migrators on every uploaded file and treats a config without a `// vyos-config-version` footer as version 0, executing the full historical migration chain against it. The `src` contract therefore documents that callers SHOULD supply a footer-bearing config (anything saved by VyOS — `/config/config.boot`, `save <file>` output — carries it; bare `show configuration` output does not). The library does not detect or inject the footer; documented, not enforced.

**Staging**: per-invocation high-entropy path `/tmp/pyinfra-vyos-<128-bit random token>/` — never content-derived (identical-content runs must not collide; content-derived paths also leak). The token is generated when the operation generator is evaluated for execution (pyinfra re-invokes generators; it is not retained from an earlier prepare pass). Created with `mkdir -m 700` *without* `-p`, so a collision is a hard failure.

**Concurrency precondition** (explicit, documented on the op): the caller/orchestrator MUST serialize all `config_load` mutations per host — at most one mutation session may run at a time, including runs from the same controller. VyOS has no documented session-locking contract (R§1.7, research unknown #2), pyinfra only orders ops within one `State`, and a remote lock would be an invented protocol on an appliance — rejected for wave 1. Overlapping mutation runs are out of contract.

**Yielded commands**, in order:
1. `StringCommand` — preflight `sg vyattacfg -c 'test -x /bin/vbash && test -r /opt/vyatta/etc/functions/script-template' </dev/null` (R§1.2: remote-auth users must be probed, and it must happen *before* secrets are staged; the same probe verifies the vbash/script-template substrate exists). `</dev/null` also prevents a group-password prompt from hanging.
2. `StringCommand` — `mkdir -m 700 <staging>` (no `-p`).
3. `FileUploadCommand` — config → `<staging>/config`.
4. `StringCommand` — `chmod 600 <staging>/config && LC_ALL=C grep -q '[^[:space:]]' <staging>/config` (FileUploadCommand has no mode parameter; SFTP does not set 0600 — explicit chmod; the grep enforces the same ≥1 non-whitespace-byte invariant as the controller check and closes the TOCTOU gap).
5. `FileUploadCommand` — script (`StringIO`) → `<staging>/session.sh`.
6. `StringCommand` — `chmod 600 <staging>/session.sh` (vbash only reads it; `/tmp` `noexec` is harmless because the interpreter is explicit).
7. `StringCommand` — `sg vyattacfg -c "/bin/vbash <staging>/session.sh" </dev/null; rc=$?; rm -rf <staging>; exit $rc` — outer cleanup runs on script failure too.

**Cleanup honesty**: the trap plus command 7 cover every path that reaches session execution. A failed upload (pyinfra stops at the first failed command) or connector loss strands `<staging>` — a real residual `/tmp` exposure, 0600/0700-protected, documented rather than denied. `/tmp` may be tmpfs; a large config can hit capacity and an interrupted upload can leave a partial file — documented.

**Session script contract** (built by `_session`; one vbash, one session, ≤1 commit — R§1.1):

- `export VYATTA_PAGER=cat` *before* sourcing anything (wrapper output/exit codes otherwise route through an inherited pager).
- `source /opt/vyatta/etc/functions/script-template`. From this point `exit` is an *alias* that tears down the session without honoring a status — **every terminating path uses `builtin exit "$rc"`**; the `exit`/`discard` aliases are never called from cleanup.
- EXIT trap: capture `rc=$?`; `trap - EXIT`; if `/bin/cli-shell-api inSession` → `/bin/cli-shell-api teardownSession` (discards the candidate); `rm -rf <staging>`; if any cleanup step fails and `rc` was 0, set `rc=1` (cleanup failure fails a success but never masks a prior failure); `builtin exit "$rc"`.
- `configure`; then assert `/bin/cli-shell-api inSession` (wrapper rc untrusted — R§1.6) or `builtin exit 1`.
- `load <staging>/config`: check child rc directly and capture output; nonzero → output to stderr, `builtin exit 1`. Residual limitation, stated: without 1.4/1.5 fixtures we do not define output failure-markers, so a hypothetical rc-0 silent load failure degrades to a noop, not corruption (`sessionChanged` would show no candidate diff). Flagged for appliance fixtures.
- **Change gate**: `/bin/cli-shell-api sessionChanged` with its rc captured explicitly and interpreted as a tri-state — 0 = candidate differs, 1 = unchanged, anything else = error → `builtin exit 1` (a plain `if` would misread an exec/crash status as noop/success). Differs → `out=$(commit)`; post-condition: re-run `sessionChanged` with the same tri-state handling; still-differs → emit captured commit output to stderr (validation errors are the necessary diagnostic; no diff is printed), `builtin exit 1`; unchanged → `did_commit=1`. No difference at the first gate → skip commit. `compare` is not used at all — commit's own captured output is the failure diagnostic.
- **Save block** (runs whenever `save=True`, on both changed and noop paths): needs-save test is `/bin/cli-shell-api --show-cfg1 @ACTIVE --show-cfg2 /config/config.boot --show-commands showConfig`, *status-checked*: before save, a failing check (absent/unreadable boot file) means "save required"; nonempty output means "save required". If required → `save`; re-run the same check; now a failing check or nonempty output → stderr + `builtin exit 1`; else `did_save=1`.
- Sentinel: `PYINFRA_VYOS changed` if `did_commit || did_save`, else `PYINFRA_VYOS noop`; `builtin exit 0`. A save-only run therefore reports `changed`, never `noop`.

Every wrapper/renderer invocation in the script has an explicit checked status before its output (including empty output) is interpreted.

**Failure surfacing**: nonzero script exit fails the op via pyinfra's normal path; stderr carries command/status context and captured commit/save output only.

## 3. Facts

All run bare op-mode argv through `vyos_op_command()` (pyinfra executes via `sh -c`, where op-mode commands don't exist; script-template's `run` is the documented non-interactive entry). All use `_fact_process` → `FactProcessError`, and `requires_command() → "vbash"` — a *binary-presence gate only* (returns `default()` when absent); it does not establish VyOS-ness or command compatibility.

**Empty-stdout guard**: pyinfra skips `process()` entirely on empty stdout, silently returning `default()` — so every fact command emits a package-controlled trailing marker on success, forced onto its own line with `printf '\n%s\n' <marker>` (a bare `echo` could concatenate onto payload lacking a final newline); the real command's rc propagates through `&&`. `process()` requires and strips the trailing marker before parsing. For the two content-requiring facts (`Version` needs a version field, `Configuration` needs valid JSON), a successful-but-empty payload fails loudly through the parser instead of masquerading as "no vbash". The guarantee is scoped to those two facts: `ConfigurationCommands` parses an empty marked payload to `[]`, which is indistinguishable from its `default()` — accepted (amended per review round; a truly empty command rendering of a live config is implausible, and raising would misfire on legitimately minimal configs).

| Fact | Op-mode argv | Parse (`_parse`) | `default()` |
|---|---|---|---|
| `Version` | `show version` | first-colon split per line; require `version` key | `{}` |
| `Configuration` | `show configuration json` | `json.loads`, top-level dict check; raw tree, no normalization | `{}` |
| `ConfigurationCommands` | `show configuration commands` (+ literal escaped `\|` `strip-private` argument — a VyOS op pipe, not a shell pipeline — when `strip_private=True` on `command()`) | device-rendered nonempty lines preserved as-is (no normalization without fixtures) | `[]` |

**Secret boundary, honestly stated**: `Configuration` and unredacted `ConfigurationCommands` are secret-bearing (R§1.5). The library cannot enforce "never log": returned fact values, verbose fact output, failed-fact combined output, and operation failure diagnostics can all reach controller logs. The contract is: the library keeps failure output minimal and never prints config diffs; callers must treat controller logs as sensitive. `strip_private` output is documented as not restore-faithful.

## 4. Key decisions

- **D1 — script as uploaded file, `sg vyattacfg -c "/bin/vbash <path>"`**: the documented config-script pattern (R§1.2); avoids multiline argv quoting. Never sudo.
- **D2 — `sessionChanged` as the change/postcondition gate, `builtin exit` termination**: direct cli-shell-api booleans instead of wrapper rc or output parsing (R§1.6); the sourced template's `exit` alias makes `builtin exit` mandatory for any trustworthy status contract.
- **D3 — no prepare-time diff**: controller-side canonicalization of a rendered whole config is the documented idempotency trap; the device's load + `sessionChanged` is the authority. Consequence: pyinfra's `did_change()` (nonempty executed command list) is necessarily pessimistic; disclosed via `is_idempotent=False` metadata plus truthful device-side sentinels.
- **D4 — caller-serialized mutations instead of an invented remote lock** (see §2 concurrency precondition).
- **D5 — no version gate in `config_load`**: commit validation on-device is the schema authority for a whole-config load; `Version` serves callers and future dispatch.

## 5. Wave-2 seam

Second-wave typed/scoped operations reuse the same substrate — `_session` gains sibling script builders, `_parse` gains tree selection — with no wave-1 assumption blocking that; nothing more is designed now.

## 6. Risks

| Risk | Handling |
|---|---|
| Silent rc-0 `load` failure | Degrades to noop (no candidate diff), not corruption; rc+output checked; appliance-fixture backlog item. |
| Stranded staging files on pre-session upload failure / connector loss | Accepted residual: 0600/0700 + random path; documented, not denied. `/tmp` tmpfs capacity noted. |
| pyinfra reports changed on device-noop | Structural (nonempty executed command list); disclosed via metadata + docstring; sentinels give the truthful device answer. |
| Bad full config severs SSH | Out of scope (no commit-confirm); docstring points callers at verify-then-`config_load(save=True)`. |
| Concurrent sessions | Caller-serialized-mutation precondition; overlapping mutation runs out of contract. |
| `sessionChanged`/selector behavior on specific point releases | Branch-source-backed but appliance-unverified (research unknown #1); marked integration suite is the verification path. |
| User lacks vbash/vyattacfg/script-template | Preflight probe (group + vbash + script-template) fails before any secret is staged. |
