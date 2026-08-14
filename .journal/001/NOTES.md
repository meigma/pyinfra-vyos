---
id: 001
title: Bootstrap pyinfra-vyos from template
started: 2026-08-14
---

## 2026-08-14 13:39 — Kickoff
Goal for the session: bootstrap the new `meigma/pyinfra-vyos` repository — a pyinfra plugin package for VyOS — from `meigma/template-pyinfra`.
Current state of the world: public repo created from the template and cloned to `~/code/meigma/pyinfra-vyos` (single commit `5ff0e67` "Initial commit"). Session journal set up on `journal/jmgilman` (worktree `.wt/journal-jmgilman`). Template first-setup checklist in `DELETE_ME.md` is entirely unstarted: distribution/package still named `template-pyinfra`/`template_pyinfra`, sample git-config domain intact, PyPI trusted publishing and release app not configured, repository settings not applied.
Plan: follow the `DELETE_ME.md` checklist — rename distribution/import package, replace placeholders, relock, decide on docs site, configure PyPI trusted publishing + release app, apply repository settings, run `moon run root:check`, then start replacing the sample domain with VyOS facts/operations.
