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
