---
id: 001
title: Bootstrap pyinfra-vyos from template
started: 2026-08-14
---

## 2026-08-14 13:39 — Kickoff
Goal for the session: bootstrap the new `meigma/pyinfra-vyos` repository — a pyinfra plugin package for VyOS — from `meigma/template-pyinfra`.
Current state of the world: public repo created from the template and cloned to `~/code/meigma/pyinfra-vyos` (single commit `5ff0e67` "Initial commit"). Session journal set up on `journal/jmgilman` (worktree `.wt/journal-jmgilman`). Template first-setup checklist in `DELETE_ME.md` is entirely unstarted: distribution/package still named `template-pyinfra`/`template_pyinfra`, sample git-config domain intact, PyPI trusted publishing and release app not configured, repository settings not applied.
Plan: follow the `DELETE_ME.md` checklist — rename distribution/import package, replace placeholders, relock, decide on docs site, configure PyPI trusted publishing + release app, apply repository settings, run `moon run root:check`, then start replacing the sample domain with VyOS facts/operations.

## 2026-08-14 13:58 — Operations research complete
Deep-research agent surveyed VyOS 1.4/1.5 docs, vyatta-cfg source, Ansible vyos.vyos 6.0, Terraform providers, NAPALM, and the VyOS HTTP API to scope facts/operations.
Key findings:
- SSH substrate: `vbash -s` + `script-template` + `cli-shell-api`; one session/one commit per operation; run under `sg vyattacfg` (never sudo); commit and save are separate decisions; config output is secret-bearing; public wrapper functions can mask child exit codes.
- 1.4 vs 1.5 schema drift is real (DHCP ISC→Kea with `subnet-id`/`option` moves, syslog `host`→`remote`), so a `Version` fact must gate typed parsers/renderers.
- Recommended first-wave operations (8): generic `configuration` (scoped set/delete diff), `system_basics`, `interfaces` (core types), `static_routes`, `firewall_groups`, `firewall_rules`, `nat_rules`, `users` (SSH keys first).
- P0 facts: Version, Configuration (show configuration json), ConfigurationCommands, ConfigExists/ConfigValue(s), Interfaces, StaticRoutes, Firewall, NAT, Users.
- Deferred: ntp/syslog/ssh_service/dns_forwarding/prefix_lists/route_maps/bgp/ospf/dhcp_server/wireguard (second wave); ipsec, config restore/merge, system_image, commit-confirm (hard, constrained workflows only); skip imperative resets/reboots as declarative ops.
Full report: session agent artifact `agent://VyosOpsResearch` (43.9KB, cited).
Next: user review of the list; then decide first-wave scope before gutting the sample domain.

## 2026-08-14 14:50 — First-wave scope locked (user decision)
User's real workflow (from `~/code/lab/infra/network/vyos`): full bracket-format `gateway.conf` in git as source of truth, SOPS secret injection at render time, backup active config, scp + native `load` + `commit`, verify connectivity, then `save`. Ansible's `vyos_config` could not parse bracket format, so the playbook shells out via `vyos_command` — pyinfra-vyos should replace that playbook outright.
Locked first wave:
- Operation `config_load`: upload rendered whole config, one vbash session `load` + `compare`; empty diff → noop (no commit); else `commit`; `save` a separate explicit decision (supports commit-verify-save workflow). Version-guarded via `Version` fact.
- Session substrate: `sg vyattacfg` + `vbash -s` + script-template session, checked exit codes (wrappers can mask failures), one session/one commit.
- Facts: `Version`, `Configuration` (`show configuration json`), `ConfigurationCommands` (canonical set-command form; backup source).
Demoted to second wave: all typed ops (generic `configuration` scoped diff, system_basics, interfaces, static_routes, firewall_groups, firewall_rules, nat_rules, users) plus previous second-wave list.
Residual risks accepted (inherent to current workflow too): bad load can sever SSH mid-apply (mitigated by commit-without-save + verify), multi-component partial commit.
Next: template first-setup (rename to pyinfra-vyos, placeholders, relock), then implement first wave in place of sample git-config domain.

## 2026-08-14 14:54 — Correction: library boundary (user decision)
Correction to the 14:50 entry: pyinfra-vyos is a generalized library, NOT an import of the lab repo's wholesale flow. The lab workflow informed the op design but its orchestration stays out.
- In library: `config_load` (pre-rendered src path/content, upload, load+compare, noop/commit, optional save flag), facts (Version, Configuration, ConfigurationCommands), session substrate. No opinions on config origin.
- Out of library (caller's bespoke deploy code): SOPS/secret injection, config templating/rendering, backup policy (caller reads fact and writes file), connectivity-verify-then-save orchestration, inventory/keys.
Analogy: vyos.vyos collection doesn't know about SOPS either; playbooks compose it. Matches template intent: "custom facts and operations for other people's deploys to import."

## 2026-08-14 15:20 — Architecture finalized
Ran software-architect + architecture-reviewer combo (two review passes, per user instruction). Pass 1 verdict: reject — 3 blockers (script-template aliases `exit` so numeric statuses are lost → must use `builtin exit`; proposed `--show-cfg-file` selector doesn't exist in cli-shell-api → `--show-cfg1 @ACTIVE --show-cfg2 /config/config.boot`; content-hash staging paths collide under concurrency) plus majors (FileUploadCommand has no mode param, pyinfra skips fact process() on empty stdout, double-`run` contradiction, missing sg preflight, secret-logging overclaim). Architect revised (v2). Pass 2 verdict: accept-with-notes — 7 wording-level fixes (per-host mutation serialization wording, preflight also checks vbash+script-template, remote non-whitespace grep instead of test -s, fact marker via printf on own line, sessionChanged rc tri-state handling, staging token generated at generator evaluation, dropped false test -s claim). Notes applied by me into final v3.
Final document: `.journal/001/ARCHITECTURE.md`. Key shape: modules `_cli.py`/`_session.py`/`_parse.py`/`facts.py`/`operations.py` inheriting template patterns; config_load yields preflight→staging→uploads+chmods→one sg/vbash session script with builtin-exit/trap/sessionChanged-gated commit and status-checked save block; facts use marker-guarded op-mode commands; op marked is_idempotent=False.
Next: template first-setup (rename, placeholders, uv lock), then implement per ARCHITECTURE.md.

## 2026-08-14 16:31 — Implementation plan added
Planner agent produced `.journal/001/PLAN.md` (adjacent to ARCHITECTURE.md, which it treats as authoritative). Five phases: A template first-setup (rename → pyinfra_vyos, placeholder sweep per DELETE_ME, uv lock, keep docs site, [USER] steps: PyPI trusted publisher / release app / repo settings / LICENSE) → Gate 1 root:check green with sample domain under real names; B domain implementation in dependency order (_parse → _session → _cli → facts → operations → __init__), each module with its unit-test companion in the same step, _gitconfig deleted when last dependent replaced → Gate 2; C test mapping (keep conftest --integration + reserved-arg meta-test + pyinfra harness half; provisional 1.4/1.5 fixtures with provenance comments; @local prepare-phase tier + new opt-in `appliance` marker, no CI wiring); D docs/README with commit-verify-save as canonical example, delete DELETE_ME.md; E gates + wheel-install smoke + [USER] appliance smoke to recapture fixtures.
One correction applied to planner output: pinned pyinfra is 3.10.0 (uv.lock, reviewer-verified), not 3.9.x.
Next: execute Phase A.
