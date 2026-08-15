---
id: 001
title: Bootstrap pyinfra-vyos from template
date: 2026-08-15
status: complete
repos_touched: [pyinfra-vyos]
related_sessions: []
---

## Goal
Create `meigma/pyinfra-vyos` — a pyinfra plugin package (facts + operations
for VyOS routers over SSH) — from `meigma/template-pyinfra`, and ship a
working, verified first wave.

## Outcome
Goal met, and hardware-verified. `main` holds: the renamed package
(`pyinfra_vyos`), the wave-1 domain (`config_load` operation + `Version` /
`Configuration` / `ConfigurationCommands` facts on a hardened vbash session
substrate), two test tiers (`--integration` @local, opt-in `--appliance`),
a Lima-powered disposable VyOS lab, rewritten docs, dual MIT/Apache-2.0
licensing, and a fully configured release pipeline (PyPI trusted publishing,
release app credentials verified, repository settings applied). The
appliance tier passed 4/4 against a real VyOS 2026.03 VM, including the
config_load changed→noop→save sentinel cycle. Release Please opened the
0.1.0 release PR (#7) — left for the human release decision.

## Key Decisions
- Whole-config `config_load` first, typed ops deferred to wave 2 → user's
  real workflow is git-managed configs applied via native `load`; every
  automation ecosystem converges on a generic config primitive first.
- Library boundary: no SOPS/templating/backup/verify orchestration →
  origin-agnostic primitives only; callers compose (like vyos.vyos).
- Device-side idempotency (`sessionChanged` gate) over controller-side
  diffing → controller canonicalization is the documented idempotency trap;
  op is `is_idempotent=False` with truthful device sentinels.
- `builtin exit` + EXIT-trap session contract → script-template aliases
  `exit`, silently discarding exit statuses (review-caught blocker).
- Commit/save split with explicit `save` flag → enables commit→verify→save
  against the severed-SSH risk.
- Marker guarantee scoped to content-requiring facts (user decision) →
  empty `ConfigurationCommands` payload legitimately equals `default()`.
- Config-version footer documented, not enforced (user decision) →
  footerless configs trigger VyOS's full migration chain on `load`.
- Lima harness prepares the image outside Lima (expect-driven serial
  install) → free VyOS images are amd64 live ISOs without cloud-init, and
  VyOS cannot run Lima's cloud-init provisioning or guest agent.

## Changes
All in `pyinfra-vyos`, squash-merged PRs #1–#6:
- `src/pyinfra_vyos/` — `_parse.py`, `_session.py`, `_cli.py`, `facts.py`,
  `operations.py`, `__init__.py` (sample git domain removed)
- `tests/` — unit tiers per module; `tests/integration/test_vyos.py`
  (@local prepare-phase), `tests/integration/test_appliance.py` (live
  device), marker-based conftest gating
- `tests/appliance/` — Lima lab: `vyos-lab`, `build-image.sh`,
  `install.expect`, `configure.expect`, `lima-vyos.yaml.in`, README
- `README.md`, `docs/`, `SECURITY.md`, `LICENSE-APACHE`, `LICENSE-MIT`,
  release-please/workflow/pyproject metadata
- Repository settings applied via `configure_github_repo.py`; PyPI pending
  publisher + `pypi` environment; `MEIGMA_RELEASE_APP_ID`/private key set
  from 1Password and JWT-verified

## Open Threads
- PR #7 (`chore(main): release 0.1.0`) open — human decides when to cut
  0.1.0; on merge the pipeline publishes to PyPI and a human publishes the
  draft release.
- Wave 2 (typed operations) unstarted by design; source material in
  `.journal/001/RESEARCH.md`, seam in `ARCHITECTURE.md` §5.
- Unit fixtures for `show version` are synthesized; a real capture from the
  lab exists (`tests/integration/_captures/` pattern) and could replace them.
- GitHub-API-unsupported repo toggles (Archive Program, dependency
  submission, etc.) remain manual if wanted.

## Lessons
- VyOS `script-template` aliases `exit`; any script needing exit statuses
  must use `builtin exit`. Wrapper rc values are generally untrustworthy —
  gate on `cli-shell-api sessionChanged` / explicit postconditions.
- `run` is a bash alias: `vbash -c 'source X; run …'` parses as one unit
  and never expands it (rc 127); source and run must be on separate lines.
- The interactive `| strip-private` op pipe is interactive-runner grammar;
  non-interactive redaction is a shell pipeline through
  `/usr/libexec/vyos/strip-private.py` with `pipefail`.
- VyOS pins NIC MACs as `hw-id` at commit; images moved between VMs need
  the binding stripped or boot config load fails.
- pytest `item.keywords` contains parent collector names — a directory
  named `integration` gives every test the `integration` keyword; gate by
  `get_closest_marker`.
- pyinfra skips fact `process()` on empty stdout (silent `default()`);
  marker-wrapping fact commands restores loud failure for content-requiring
  parsers.

## References
- PRs: meigma/pyinfra-vyos #1–#6 (merged), #7 (release, open)
- `.journal/001/ARCHITECTURE.md` — accepted wave-1 architecture (amended
  ×3 during implementation)
- `.journal/001/PLAN.md` — executed implementation plan
- `.journal/001/RESEARCH.md` — full cited VyOS operations research
  (wave-2 source material)
