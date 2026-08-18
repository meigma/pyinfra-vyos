---
id: 002
title: Wave 2 — typed VyOS operations
started: 2026-08-15
---

## 2026-08-15 12:54 — Kickoff
Goal for the session: implement wave 2 as documented in session 001 — typed
operations for pyinfra-vyos, per the seam in `.journal/001/ARCHITECTURE.md` §5
and candidates in `.journal/001/RESEARCH.md`.
Current state of the world: wave 1 shipped and hardware-verified on `main`
(config_load op, Version/Configuration/ConfigurationCommands facts, vbash
session substrate, @local + appliance test tiers, Lima lab, release pipeline
live; release PR #7 for 0.1.0 open awaiting human decision).
Plan: review ARCHITECTURE.md §5 seam and RESEARCH.md wave-2 candidates, scope
the typed-operation set with the user, then design/implement/verify in an
isolated implementation worktree.

## 2026-08-15 13:05 — Configuration op: semantics settled, prototype started
Decision: ARCHITECTURE §5 is a seam, not a spec; per agile stance we prototype
the generic scoped op first and write the wave-2 doc from learned decisions.
Op signature: `config(path, values=None, *, replace=False, present=True,
save=False)` in worktree `feat/configuration-op`.
- values convention mirrors `show configuration json` leaf shapes; desired and
  active normalize identically (leaves -> list[str]); diff is tree-to-tree.
- merge default (sets only, omitted = unmanaged); replace opt-in (deletes for
  extra active keys/values, deletes ordered before sets); present=False =
  whole-path delete.
- Two-layer idempotency: controller diff vs scoped Configuration fact ->
  host.noop() on empty delta; device sessionChanged gate stays authoritative
  (normalization mismatch degrades to truthful noop sentinel).
- save persists only when this op commits; empty delta + save=True is a noop.
- Plan: new pure `_tree.py` (validate/select/diff), `_session.py` refactored
  into shared prologue/commit-gate/epilogue + `build_commands_script`
  (commands embedded shlex-quoted in the 0600 uploaded script, off argv per
  C3), op named `config` (imperative-adjacent, avoids clashing with the
  `Configuration` fact; family: config_load, config).

## 2026-08-15 13:35 — Scoped config op shipped to PR #8, hardware-verified
Prototype complete in `feat/configuration-op`; PR #8 open.
What was built: `_tree.py` (pure validate/select/diff, argv token safety per
C2), `_session.py` refactor (shared prologue/commit-gate/epilogue +
`build_commands_script`), `config` op, README/docs sections, 3 test tiers.
Verification: unit 158 pass; @local 11 pass; appliance tier 5/5 against real
VyOS 2026.03 — merge-create -> controller noop -> multi-value merge append ->
replace-prune -> present=False delete -> delete-noop, sentinels and
independent op-mode reads all correct, first attempt.
Learned (wave-2 doc material):
- The two-layer idempotency split works on hardware: controller diff decides
  whether/what to send (honest host.noop), device sessionChanged stays the
  truth gate. `config` is honestly idempotent in pyinfra terms, unlike
  config_load.
- Desired-values convention mirroring `show configuration json` shapes
  (str/list/dict, {} valueless) diffs cleanly after normalizing both sides'
  leaves to list[str]; static-host-mapping inet renders as a JSON list even
  with one entry, confirming the normalize-both-sides approach.
- Multi-value ordering deliberately unmanaged (set-equality); no appliance
  pushback in this domain. Revisit if an order-significant node (name-server
  etc.) bites.
- Session skeleton generalized with zero appliance surprises — wave-1's
  script contract is domain-independent; typed ops can be pure renderers
  onto _tree diff + build_commands_script.
- C3 subtlety worth keeping: per-command failure diagnostics must be
  ordinal-only (values can be secrets); captured device output remains the
  diagnostic.
Open: wave-2 architecture doc (write from these learned decisions once PR #8
lands); typed ops (system_basics, static_routes, ...) as renderers; scoped
facts (ConfigExists/ConfigValue) only if the full-tree fact proves too heavy.

## 2026-08-15 14:20 — PR #8 merged; wave-2 architecture accepted
PR #8 squash-merged to main (bf1e9a9); feat worktree pruned.
Ran software-architect + architecture-reviewer pipeline (two review rounds as
capped): draft -> revise (round-1 verdict: revise) -> final amendments
(round-2 verdict: accept-with-notes, notes applied). Result committed as
`.journal/002/ARCHITECTURE.md`.
Headlines: 7 ops in wave 2 (system_basics, interface, static_route,
firewall_group, firewall_ruleset, user, config_save); nat_rule deferred
(pure `config` replace at a numbered key). Typed ops are pure renderers ->
Scope algebra (Absent/Exact/Merge + sensitivity carrier) over shipped
diff/session substrate (D6/D8). Full-tree Configuration fact retained, new
PendingSave fact (tri-state, fails closed) for config_save (D7/D13). Thin
version gate schema_key() seam, fail-closed on unknown versions (D9).
Shallow-typed + open-body models with typed-key collision rejection (D10).
user op: encrypted-hash-only, sensitive-scope output suppression, deletion
guard on remote identity (D11). Save gated on did_commit in
build_commands_script — fixes a real shipped gap where a canonicalization-
degraded run could persist unrelated unsaved state (D13). Decisions numbered
D6-D13 continuing wave-1.
Next: implement per §1 cut order (config_save + access path first, firewall
pair second).

## 2026-08-15 14:40 — Implementation plan landed
Planner agent produced `.journal/002/PLAN.md` implementing the accepted
wave-2 architecture verbatim (first attempt returned a JSON summary; re-ran
with a document schema). 8 PR-sized phases following the architecture cut
order: (1) substrate extensions + config_save (incl. §12 Q1 fact-cache
verification and Q2 rejected-commit appliance probe), (2) _render seam +
_plan_scopes + system_basics, (3) interface, (4) static_route, (5) user,
(6) firewall_group, (7) firewall_ruleset, (8) docs. Each phase carries
file-level targets, tests per the §11 contract, and risks; §12 open
questions mapped to resolving steps (Q1->1.1, Q2->1.8, Q3->2.6, Q4->3.2).

## 2026-08-15 16:05 — Phase 1 implemented; PR #9 open for review
Orchestrated 2 waves of programmer agents + 1 tight review pass in
`feat/config-save-substrate`; PR #9 open, CI green, awaiting human review.
- Wave A (parallel): 1.1 fact-cache verdict, 1.2 _tree Node|None root,
  1.3 _session PlannedCommand/D11/D13/build_save_script, 1.4 _cli
  session_run_sequence, 1.6 PendingSave chain. One retry: 1.7/1.8 agent
  died before editing; respawn completed clean.
- Review (approve-with-fixes) caught 2 P1s: (a) leaf-root + empty desired +
  replace planned a destructive, non-convergent bare delete (main treated it
  as noop) — fixed by gating shape-flip clears on nonempty replacement;
  (b) D11 suppression was stdout-only, device stderr leaked — sensitive
  captures now 2>&1. Plus: PendingSave probe now trims whitespace before
  wc -c (probe/script needs-save semantics now agree); appliance save test
  verifies /config/config.boot independently; Q2 probe handles list-shaped
  rule nodes and persists its observation to the capture dir.
- Q1 resolved: pyinfra 3.9.2..3.10.0 cache-free (api files byte-identical;
  empirical 3 calls = 3 executions). No floor change.
- Q2 lab data point (VyOS 2026.03): refused commit left NO partial active
  state (fully absent). Recorded in capture + test docstring; contract
  language stays conservative.
- Appliance tier 7/7 including migrated-config behavior preservation on
  hardware.

## 2026-08-15 17:35 — Phase 2 implemented; PR #10 open for review
Same orchestration pattern in `feat/system-basics`; PR #10 open, CI green.
- Wave A: _render.py (Scope algebra, RenderError, schema_key, coerce_token)
  + @local vbash shim fixture. Captured the real Version literal from the
  live lab first (`VyOS 2026.03` — no patch component; plan's sketch assumed
  2026.03.x) per the phase's named risk.
- Wave B: _plan_scopes, system_basics (+@local tests), appliance scenario.
  Two incidents: (a) concurrent operations.py edits clobbered _plan_scopes
  and its tests; agents had also split work across repo root and worktree —
  consolidated the superset into the worktree, restored root to pristine;
  (b) restoration agent recovered the planner verbatim from the dead
  agent's transcript.
- Review (approve-with-fixes) P1: schema_key was fail-OPEN — qualified-
  rolling match not anchored to token start; 2027.01.1/9999.99.1/abcdefg.5
  silently mapped to 1.5. Fixed + pinned. Plus @local noop coverage and
  P3 cleanups (error labels no longer say `values`, assert_disjoint skips
  unrelated scopes).
- HARDWARE LESSON (recorded in appliance suite): committing blackholed
  TEST-NET `system name-server` entries deterministically breaks SSH auth
  for subsequent sessions (2x reproduced, fresh VM each; reboot recovers,
  boot config untouched). Appliance tier must never mutate the management-
  path resolver; name-server semantics stay unit/@local. Scenario reworked
  around time_zone + search_domains.
- Q3 resolved by observation: domain-name + domain-search together ACCEPTED
  on VyOS 2026.03 (q3-domain-interaction.txt). Canonicalization hotspots
  clean: domain-search set order preserved, time-zone form verbatim.
- Appliance tier 8/8.

## 2026-08-15 19:10 — Phase 3 implemented; PR #11 open for review
Same pattern in `feat/interface`; PR #11 open, CI green.
- Single 3-agent wave (disjoint file sets — no clobbering this time):
  render_interface, interface op (+@local), appliance dummy cycle. One
  mid-flight contract query answered over hub.
- Review (approve-with-fixes): P2 — present=False validation ran after the
  Version read, violating §4 ordering; hoisted into shared
  require_absent_args_unset helper (reusable by phases 4-7). P3 —
  disabled=True added to the lockout docstring.
- INCIDENT: first corrective agent died and overwrote tests/test_render.py
  with an elided read-view (literal `…` markers). Root checkout clean;
  restore agent recovered the 114-passing file and applied both fixes.
  Process change adopted: commit the worktree immediately after each green
  gate so restores are `git restore`, not transcript archaeology.
- Appliance 9/9: dummy full cycle (create → noop → address prune → disable
  transitions → delete → delete-noop). Canonicalization clean: device
  echoes 192.0.2.65/32, description, mtu '1400' verbatim. Ethernet grammar
  fixture-asserted only — NOT hardware-verified (management path never
  touched); recorded per plan risk table.
- Q4 recorded in the op docstring: interface_type explicit by design.

## 2026-08-15 21:20 — Phase 4 implemented; PR #12 open for review
Same pattern in `feat/static-route`; PR #12 open, CI green. Green-state
commits after each gate (process change from phase 3) — no restore drama.
- Single 3-agent wave, disjoint files, no incidents. Op agent hoisted the
  destination parse pre-Version by importing the renderer-owned helper
  (phase-3 lesson institutionalized).
- Review (approve-with-fixes, 5 findings): P2 bare-host destination passed
  strict ip_network as implicit /32 then never round-trips (device stores
  /32, select_subtree misses, present=False falsely noops) — now rejected;
  P2 total-body prune had appliance-only evidence — host-free planner test
  added; P2 v6 canonicalization probe submitted already-canonical tokens
  (structurally unfalsifiable) — now submits expanded/uppercase.
- Appliance 10/10. v6 HARDWARE LESSON (inverse of the expected risk): the
  device does NOT canonicalize route tag-node keys — expanded/uppercase
  IPv6 stored VERBATIM, second apply noops. Hazard is two textual forms
  creating two distinct route nodes; documented in the op docstring
  (standardize on compressed lowercase). Plan's risk table had assumed
  compression; observation recorded in phase4-route-canon.txt.
- Total-body prune observed on hardware (2 hops -> 1).

## 2026-08-15 23:15 — Phase 5 implemented; PR #13 open for review
Same pattern in `feat/user-op`; PR #13 open, CI green. Clean run — no
worktree incidents (green-state commits + cwd discipline holding).
- 3-agent wave: render_user (secret validation), user op (deletion guard),
  appliance cycle. Renderer agent took 28m (largest test surface so far).
- Review (approve-with-fixes, 5): P2 ssh_keys={} rendered Exact({}) ->
  non-convergent bare set on the public-keys tag node (succeeds once,
  hard-fails re-runs) — now Absent like sibling [] semantics; P2 appliance
  guard probe's bare assert would print the LAB'S REAL HASH into CI logs
  via pytest assertion rewriting on failure — compares into a boolean now.
  P3s: redacted assertion message, narrowed docstring never-echo claim,
  diff-level omitted-key removal test.
- Appliance 11/11: disposable-user cycle; hash round-trips stable-text
  (idempotent comparison confirmed on hardware); public-key body echoed
  VERBATIM (no canonicalization); guard probe: self-deletion of connecting
  user failed at planning, device untouched (verified by independent read).
- Secret discipline held end-to-end: hash only in capture lines of the
  script, never diagnostics; no hash in capture files.

## 2026-08-16 01:05 — Phase 6 implemented; PR #14 open for review
Same pattern in `feat/firewall-group`; PR #14 open, CI green.
- INCIDENT (contained): renderer agent hung (killed via hub cancel); the
  op agent had crossed its file boundary and landed the pinned renderer
  itself — correct code, verified against VyOS docs; a third agent wrote
  the missing 6.1 test surface. No corruption (dead agent never wrote);
  green-state commit discipline meant zero recovery work.
- Review verdict: CORRECT — first phase with no functional defect. 3
  hardening findings applied: planner pin for the Exact({}) empty-group
  contract (the phase-5 ssh_keys={} class, now regression-tested on the
  exact diff_tree line), port-group re-apply idempotency assertion on the
  canonicalization hotspot (capture was recorded but untested), uniform
  member element error.
- Reviewer verified in vyos-1x source that memberless static groups only
  WARN (bare presence set commit-valid) — the Exact({}) reasoning holds.
- Appliance 13/13: address-group cycle with member+description prune;
  port-group canonicalization CLEAN (8080 + 8000-9000 echoed verbatim,
  re-apply noops); referenced-delete probe: commit refused with diagnostic
  surfaced, group intact (consistent with Q2 atomicity data point).
- One op remains (firewall_ruleset, phase 7) then docs (phase 8).

## 2026-08-16 03:40 — Phase 7 implemented; PR #15 open for review
Same pattern in `feat/firewall-ruleset`; PR #15 open, CI green. Split the
renderer's test surface into its own agent (4-agent wave) after phase 6's
stall — all four held file boundaries, zero incidents.
- Review (approve-with-fixes) P1: rules={10:..., "10":...} collapsed to one
  token after int coercion -> TWO Exact scopes on the identical rule path;
  planner emitted two deletes and ZERO sets, stripping the rule to nothing
  (silently dropped one body under replace_rules=True). Rejected in the
  shared helper now. P2: rules={} with replace_rules=False silently owned
  nothing while claiming a noop — contradicted the package's own
  empty-collection convention; rejected, names replace_rules=True as the
  prune-all form. P3s: renderer enforces its documented present=False /
  replace_rules exclusion (was op-only); docstring op count fixed.
- rules={} + replace_rules=True renders Absent at the rule node (NOT
  Exact({})): `set <chain> rule` is invalid for a tag node needing a tag
  value — differs from phase-6 groups whose path carries its value.
  Reviewer confirmed both directions independently.
- Appliance 14/14 on custom chain PYINFRA_P7: whole-rule replace with leaf
  prune (unlisted rule untouched), single-rule {n: None} delete, prune-all,
  delete-noop. Canonicalization: int rule keys echo as STRINGS — renderer's
  coerce_token(str) matches, so T3 noop holds; action/protocol/port forms
  echoed verbatim.
- Wave-2 op set COMPLETE: 9 ops (config_load, config, config_save + six
  typed) + 4 facts. Only phase 8 (docs) remains.

## 2026-08-16 04:05 — Wave-2 learned reference (plan 8.3)
Consolidated record for future waves. Sources: appliance captures in
`tests/integration/_captures/`, per-phase review findings, hardware runs.

### Architecture open questions — answered
- Q1 (fact cache): pyinfra `Host.get_fact` is cache-free across the whole
  supported range; `api/facts.py` + `api/host.py` byte-identical between
  3.9.2 and 3.10.0; empirically 3 calls = 3 executions. No floor change.
  Consequence: execute-time planning always sees post-prior-op state.
- Q2 (rejected commit): on VyOS 2026.03 a refused commit left NO partial
  active state (chain and rule both absent). Lab-release data point only;
  contract language stays conservative.
- Q3 (domain-name + domain-search): ACCEPTED together by the device.
  Answered by observation; never encoded as controller validation.
- Q4 (interface_type inference): kept explicit by design; name-prefix
  inference rejected as magic. Revisit only with user-friction evidence.

### Canonicalization forms learned (VyOS 2026.03)
Every observed form was VERBATIM except one, which was the inverse of the
expected risk:
- interface: address `192.0.2.65/32`, description, mtu `1400` verbatim.
- static_route: route tag-node keys stored verbatim — expanded/uppercase
  IPv6 round-trips unchanged. The plan's risk table assumed compression.
  Real hazard is the inverse: two textual forms of one prefix create two
  distinct route nodes. Documented; standardize on compressed lowercase.
- user: public-key body verbatim; encrypted-password hash round-trips as
  stable text (this is what makes hash comparison idempotent).
- firewall_group: port `8080` and range `8000-9000` verbatim.
- firewall_ruleset: int rule numbers echo as STRINGS — renderer's
  coerce_token already emits strings, so T3 noop holds.
- system_basics: domain-search order preserved; time-zone verbatim.
Net: no normalization code was ever needed. Learned forms live in
docstrings and here, per the architecture's §10 handling.

### Hardware lesson (destructive, worth carrying)
Committing blackholed TEST-NET `system name-server` entries deterministically
breaks SSH auth for every subsequent session (2x reproduced, fresh VM each;
reboot recovers because boot config is untouched). The appliance tier must
never mutate the management-path resolver. Name-server Exact-list semantics
stay covered at unit/@local only.

### Empty-collection semantics — three different right answers
A recurring hazard class; each resolved by the node's own grammar:
- `ssh_keys={}` -> Absent at the child leaf (phase-5 review: Exact({}) was
  non-convergent, hard-failing every re-run).
- `firewall_group members=[]` -> Exact({}) is fine: the group node itself is
  a valid leaf-less node (memberless groups only WARN in vyos-1x).
- `firewall_ruleset rules={}` -> Absent at the `rule` node, and rejected
  entirely unless replace_rules=True: `set <chain> rule` is invalid for a
  tag node needing a tag value.
Lesson for later waves: decide empty-collection intent per node grammar,
and always pin it with a planner-level convergence test.

### Review value (7 phases, 1 tight pass each)
Caught 4 defects that unit tests would not have: the destructive leaf-root
delete (phase 1), the fail-OPEN schema_key prefix match (phase 2), the
bare-host route destination that never round-trips (phase 4), the duplicate
rule-number collision planning two deletes and zero sets (phase 7). Plus a
lab-hash leak via pytest assertion rewriting (phase 5). Verdicts: 6
approve-with-fixes, 1 correct.

### Process lessons (orchestration)
- Commit the worktree at every green gate. Phase 3 lost a test file to a
  dying agent's elided-read overwrite; recovery was transcript archaeology.
  Later phases had zero recovery cost.
- Give each parallel agent a DISJOINT file set and say so explicitly; two
  phases saw agents cross boundaries or split work across the repo root and
  the worktree when a sibling stalled.
- Split a large renderer's test surface into its own agent (phase 7) —
  removes the stall-then-boundary-cross pattern seen in phase 6.
- Capture hardware literals BEFORE writing fixtures that depend on them
  (phase 2: the real Version string is `VyOS 2026.03`, no patch component).

### Carried debt / deferred (triggers named in ARCHITECTURE)
- `config_load` inline command assembly stays out of `_cli.py` (A3 debt,
  §2) — deliberately out of wave.
- Fixture provenance: `show version` unit fixtures remain synthesized;
  appliance captures exist and could replace them.
- Deferred ops: `nat_rule` (pure `config` replace at a numbered key),
  `ntp`, `syslog`, `ssh_service`, `dns_forwarding`, `dhcp_server`,
  composite interfaces, dynamic/remote firewall groups, prefix lists /
  route maps / BGP / OSPF, wireguard, ipsec, image management,
  commit-confirm.
- Deferred infrastructure: scoped facts (`ConfigExists`/`ConfigValue`) on a
  measured cost trigger; typed convenience facts; cross-op batching.

## 2026-08-16 04:35 — Phase 8 implemented; PR #16 open. WAVE 2 COMPLETE.
Two technical-writer agents (README, docs site — disjoint files) + one
conformance specialist + one fix agent. Journal reference (8.3) written by
the orchestrator, not delegated.
- Conformance verdict FAIL initially, 5 P1s. Two were PRE-EXISTING, not
  introduced by phase 8:
  (a) both docs described `strip_private` as VyOS op-pipe tokens passed as
      argv — a stale wave-1 claim that session-001 hardware testing had
      ALREADY superseded (real behavior: shell pipeline through
      /usr/libexec/vyos/strip-private.py under pipefail). The doc outlived
      the correction because wave-1 docs were never re-read against the
      amended architecture.
  (b) `facts.py` module docstring claimed every fact runs via
      vyos_op_command — false since PendingSave landed in phase 1. Source
      docstring fixed.
  Lesson: when a hardware finding amends an architecture decision, re-grep
  the consumer docs for the superseded claim in the SAME phase.
- Other P1s: README overgeneralized per-field ownership; rules={} described
  as own-and-empty (it is rejected unless replace_rules=True); config_save
  described as diffing Configuration (it reads PendingSave); count seven ->
  six typed; evaluative wording -> observable; config_load added to the
  operation reference.
- Final smoke (plan line 373) PASSED: wheel built, installed into an
  isolated venv, all 13 exports importable.
- Wave-2 delivery: 8 phases, 8 PRs (#9-#16), 9 operations + 4 facts,
  570 unit tests, appliance tier 14/14 on VyOS 2026.03.
Next: session-close when the user is ready (PR #16 pending review; release
PR #7 for 0.1.0 still open from session 001 and now carries the whole
wave-2 surface — worth a look before cutting).

## 2026-08-16 05:10 — CHANGELOG seed removed; release PR regenerated clean
Release-readiness check found the 0.1.0 release PR proposing a CHANGELOG.md
ending in a stray `## Changelog` heading. Cause: the template's initial
commit seeded the file with a bare `# Changelog`, which Release Please
treats as pre-existing content and preserves BELOW its generated section.
- Fix: deleted the seed (PR #17, `chore:`). Nothing consumed the file — it
  ships in neither wheel nor sdist; the only references were release-
  please's own `changelog-path` and a workflow comment.
- MECHANICS LESSON: merging a `chore:` commit does NOT refresh an open
  release PR — Release Please only rewrites the release branch when
  RELEASABLE (non-hidden) commits land. The old PR #7 stayed on its
  pre-removal base. Hand-editing that branch would violate the
  release-please-owns-it rule, so the native recovery is: delete the
  release branch (this closes the PR), then `gh workflow run
  release-please.yml`; Release Please regenerates from current main.
  It is idempotent — everything derives from git history + the manifest.
- Result: PR #7 CLOSED (superseded); PR #18 open with a clean 16-line
  CHANGELOG.md created from scratch (header + 0.1.0 + nine feat entries,
  no trailing heading). Package Release Dry Run passes on #18.
- Release readiness: GO. pyproject/uv.lock already 0.1.0 so #18 touches
  only the manifest and changelog; no v* tags exist; `pypi` environment
  present; release.yml is tag-triggered (rebuild -> trusted publish ->
  attest -> human publishes the draft).

## 2026-08-16 05:35 — 0.1.0 released to PyPI; draft awaits human publish
Merged release PR #18 (`0fe1773`). Pipeline executed and validated end to end.
- Release Please: created tag `v0.1.0` (points at 0fe1773) + DRAFT GitHub
  release with the nine-feature notes.
- release.yml (tag-triggered) all green: Resolve Release -> Publish to PyPI
  -> attest-artifacts/Attest -> Release Inspection Summary.
- PyPI: pyinfra-vyos 0.1.0 live; wheel 46,507 B
  sha256 ac17afa2...58b2fa7; sdist 258,803 B sha256 917190da...f07cfd0c;
  requires-python >=3.11; license MIT OR Apache-2.0.
- VALIDATED from PyPI (not a local build): fresh venv install of
  `pyinfra-vyos==0.1.0`, all 13 exports present, 9 ops carry `_inner`,
  4 facts subclass FactBase, py.typed shipped, firewall_ruleset signature
  intact.
- PROVENANCE: `gh attestation verify` exits 0 for BOTH published files.
  One SLSA provenance v1 statement covers both subjects; digests match the
  PyPI bytes exactly. Signer: attest.yml, ref refs/tags/v0.1.0, commit
  0fe1773, github-hosted runner, Actions OIDC issuer. This is the SLSA L3
  claim actually holding.
- GAP FOUND (not a blocker): PyPI's PEP 740 endpoint
  (`/integrity/.../provenance`) returns 404 for both files, because the
  upload step is `uv publish --trusted-publishing always`, and uv does not
  send PEP 740 attestations (that is a pypa/gh-action-pypi-publish/twine
  feature). Consequence: provenance is verifiable via GitHub's attestation
  store but NOT visible where a PyPI consumer would look. Options for a
  later release: keep uv publish and document `gh attestation verify` as the
  verification path, or switch the upload step to gh-action-pypi-publish
  with attestations enabled. Recorded as backlog, not fixed mid-release.
- Release assets are empty by design: distribution is PyPI, and
  checksums.txt is deliberately written outside dist/ to feed attestation
  subjects rather than to be uploaded.
- REMAINING HUMAN STEP (by design, release.yml header): publish the draft
  release after inspection — `gh release edit v0.1.0 --draft=false`.
