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

## 2026-08-14 21:27 — Wave 1 implemented and merged (Phases A-E complete)
Orchestrated programmer+reviewer loops per phase (max 2 review rounds, human PR approval gates). Four squash-merged PRs on main:
- #1 `chore: adopt pyinfra-vyos identity from template` (807a1c2) — Phase A rename/sweep/relock; review approve round 1.
- #2 `feat: add vyos facts and config_load operation` (5ed17a9) — Phase B; review round 1 caught a real blocker: `run` is a bash alias, `vbash -c 'source X; run …'` parses as one unit so the alias never expands (rc 127 on real appliances); fixed with newline-separated payload + VYATTA_PAGER=cat export. Round 2 approve-with-notes (reviewer executed rendered command against stubbed alias env). DESIGN-FLAW resolved per user: §3 marker guarantee scoped to Version/Configuration only (ARCHITECTURE amended 34850e5).
- #3 `test: add vyos integration tiers` (c790462) — Phase C; @local prepare-phase tier + opt-in `appliance` marker (needs --appliance + PYINFRA_VYOS_TEST_HOST). Round 1 blockers: `system description` isn't a VyOS node (now `system static-host-mapping` with .invalid/TEST-NET-1) and footerless `show configuration` capture triggers full migration chain (now reads /config/config.boot). Round 2 rejected on a one-char slice bug (`config[match.end()]` missing `:`); rounds exhausted so orchestrator applied+proved it by executing the splice. DESIGN-FLAW resolved per user: config-version footer documented, not enforced (ARCHITECTURE amended 1eca231).
- #4 `docs: rewrite project docs for the vyos domain` (a464a6f) — Phase D; README/docs with commit-verify-save canonical example + four user-facing contracts; SECURITY carries paramiko<5 advisory (pyinfra 3.10.0 pins it; lock has paramiko 4.0.0); DELETE_ME removed. Review approve round 1 + 4 minor accuracy fixes.
Phase E verification on main a464a6f: root:check green (13 tasks), test-integration green, wheel-install smoke green (scratch venv, four-name surface, rendered Version command carries newline-separated run).
Outstanding [USER] items before first publish: LICENSE, PyPI pending publisher (release.yml/pypi env), MEIGMA_RELEASE_APP_ID/KEY, configure_github_repo.py plan+apply (is_template still true).
Backlog: appliance-capture real 1.4/1.5 fixtures (tests/integration/_captures/), reviewer note on config.boot restore semantics documented in PR #3.
Next: session close when user asks; wave 2 (typed ops) unstarted by design.

## 2026-08-14 21:58 — Release prerequisites closed out
- PR #5 `chore: dual-license under Apache-2.0 and MIT` (d1949e4): LICENSE-APACHE + LICENSE-MIT, PEP 639 `license = "MIT OR Apache-2.0"` + license-files (verified in wheel METADATA: License-Expression + both License-File entries in dist-info/licenses/), README dual-license section with contribution clause, repository-settings.toml is_template flipped to false (was true; applying as-shipped would have re-templated the repo).
- User confirmed done: PyPI pending publisher + `pypi` environment (verified via API). meigma-release-please app installed org-wide.
- Repository settings applied via configure_github_repo.py (run by agent per user): general settings, immutable releases, private vuln reporting, security fixes, Pages (cert-provisioning race on first apply; second apply converged, https_enforced=true), branch ruleset "Default branch", tag ruleset "Default tags". Second plan run shows no drift. API-unsupported toggles remain manual (Archive Program, dependency submission, etc. — listed by the script).
- OPEN FLAG for user: org-level Actions variables and secrets both list empty via API — release.yml needs MEIGMA_RELEASE_APP_ID (var) and MEIGMA_RELEASE_APP_PRIVATE_KEY (secret) visible to this repo; app *installation* alone doesn't provide them. If they exist at org level but hidden from this token, ignore; otherwise first release PR will fail at token minting.
- Remaining: appliance fixture recapture (backlog). Release path is otherwise live: conventional commits → Release Please PR → merge → draft release/tag → rehearsed publish → human publishes draft.

## 2026-08-14 22:20 — Release app credentials uploaded and proven
Resolves the 21:58 OPEN FLAG. Per user: read `meigma-release-please` item from 1Password `Development` vault via `op` — field `app_id` (3342783) set as repo variable MEIGMA_RELEASE_APP_ID, file attachment `key.pem` piped to repo secret MEIGMA_RELEASE_APP_PRIVATE_KEY (never written to disk or logs). Verified end-to-end: RS256 JWT signed with the key authenticates to GitHub as app `meigma-release-please` (1 installation). Debugging note: initial verification false-failed because `python - <<EOF` heredoc stole stdin from the key pipe — cryptography parsed an empty string (MalformedFraming); rerun with `python -c` proved the key valid. All release prerequisites now closed; only appliance fixture recapture remains on backlog.

## 2026-08-15 09:30 — Lima appliance lab built; appliance tier passes on real VyOS (PR #6)
Per user request: Lima-powered harness at `tests/appliance/` (branch test/lima-harness, PR #6 open). Research (agent, cited): free VyOS images are amd64 ISOs only (no arm64/qcow2); Lima's cloud-init can't provision VyOS (module mismatch); Lima `plain` mode + prepared qcow2 + QEMU hostfwd SSH is the workable shape. Chose VyOS Stream 2026.03 (circinus/1.5 lineage, matches fixture target).
Harness: `vyos-lab build|up|env|test|down`; build-image.sh does one-time expect-driven serial-console `install image` + config (DHCP/SSH/Lima key on vyos user) from the pinned SHA-256-verified ISO; ~4 min warm build under TCG. Readiness = own SSH probe (limactl start always times out on its cloud-init boot-script requirement; guest is healthy).
Hard-won appliance facts recorded for posterity:
- VyOS pins NIC MAC as `hw-id` at commit; a different MAC at next boot → "vyos-config: Configuration error" and no SSH. Harness strips hw-id from config.boot post-save.
- The default ISO GRUB entry already boots a serial getty; no menu interaction needed.
- Installer prompt grammar taken from vyos-1x image_installer.py + utils/io.py; naive `:`-tail expect fallbacks answer informational lines and break destructive-confirm prompts.
Two library bugs found ONLY by real hardware (fixed in PR #6, 61dd3fb):
1. conftest keywords-vs-marker: `"integration" in item.keywords` matches the tests/integration directory name → appliance tests unrunnable without --integration. Now get_closest_marker.
2. strip-private op pipe: `\|` argv is rejected by non-interactive `run` (Invalid command: [|]); real form is a shell pipeline through /usr/libexec/vyos/strip-private.py with pipefail (verified redacting on-device). ARCHITECTURE §3 table amended.
Verification: appliance tier 4/4 PASSED against the Lima VM (facts + config_load changed→noop→save cycle on VyOS 2026.03 circinus); root:check + test-integration green; real `show version` fixture captured (backlog item satisfied).
Next: PR #6 awaiting human approval; lab VM left running (pyinfra-vyos Lima instance).

## 2026-08-15 12:39 — PR #6 merged; lab VM torn down
User approved; squash-merged as `test: add lima-powered vyos appliance lab` (5973734). Lima instance deleted via `vyos-lab down` (qcow2 cache kept in ~/.cache/pyinfra-vyos for instant re-up). Worktree removed. All backlog items closed; wave 1 fully shipped and hardware-verified.

## 2026-08-15 12:43 — Wave-2 research persisted
User asked where wave-2 intent lives; the full cited research report (prioritized ops/facts with feasibility, next-wave/constrained/skip lists, 1.4/1.5 drift notes) existed only as a session artifact. Copied to `.journal/001/RESEARCH.md` with a header noting wave-1 shipped and the two on-hardware corrections (strip-private pipe, hw-id pinning). Wave-2 map now durable: RESEARCH.md (what+why) + ARCHITECTURE.md §5 (how it bolts on).
