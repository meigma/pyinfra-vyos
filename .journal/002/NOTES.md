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
