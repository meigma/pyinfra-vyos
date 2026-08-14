---
title: pyinfra-vyos
slug: /
description: Starting point for Meigma pyinfra plugin packages.
---

# pyinfra-vyos

This repository is the starting point for Meigma [pyinfra](https://pyinfra.com)
plugin packages: distributable Python packages that add custom facts and
operations to a pyinfra deploy.

Create a repository from it on GitHub with **Use this template**, then work
through `DELETE_ME.md` in the generated repository.

## What the template provides

**A working plugin package.** `src/pyinfra_vyos/` ships sample primitives
built around `git config`: facts that read repository configuration, an
idempotent operation that converges a single key, and a pure domain module the
two share. The public surface is `facts.py` and `operations.py`; everything
else is private and meant to be gutted. The sample is deliberately small and
runnable anywhere `git` is installed, so the integration tests exercise the
real pyinfra API rather than mocks.

Consumers import from the package the same way they import pyinfra's own
primitives:

```python
from pyinfra_vyos.operations import config_entry

config_entry(key="user.email", value="dev@example.com")
```

**A pinned toolchain.** [mise](https://mise.jdx.dev) pins Python, `uv`, and
`moon` with a committed lockfile and fail-closed installs. [uv](https://docs.astral.sh/uv/)
manages dependencies through a committed `uv.lock`, and every task runs with
`--locked`. [moon](https://moonrepo.dev) is the single entrypoint: `format`,
`lint`, `lock`, `typecheck`, `test`, `build`, and `scripts-test` all roll up
into `root:check`, which is what CI runs.

**A release pipeline.** [release-please](https://github.com/googleapis/release-please)
turns Conventional Commits into a release PR and a draft GitHub release;
tagging publishes to PyPI through trusted publishing, with build provenance
attested via GitHub artifact attestations. A dry-run workflow rehearses the
whole path on every release PR, so the first real release is not the first
time the pipeline runs.

**Hardened CI.** Every workflow declares `permissions: {}` at the top level and
grants scopes per job, pins actions by commit SHA, and checks out without
persisted credentials. Repository settings, branch rulesets, and Dependabot
configuration live in the repository as code.

**This documentation site.** MkDocs Material, built by `moon run docs:build`
and deployed to GitHub Pages on every push to `main`.

## Quickstart

```bash
git clone https://github.com/meigma/pyinfra-vyos.git
cd pyinfra-vyos
mise install       # Python, uv, and moon at their pinned versions
moon run root:check # format, lint, types, tests, build, docs
```

To preview this site locally, run `moon run docs:serve` and open
<http://127.0.0.1:8000>.

## Where to go next

- [Source on GitHub](https://github.com/meigma/pyinfra-vyos)
- [pyinfra documentation](https://docs.pyinfra.com) — the API these primitives
  extend

Generated projects should replace this page with project-specific
documentation: what the package does, how to install it, and a reference for
each fact and operation it exports.
