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
