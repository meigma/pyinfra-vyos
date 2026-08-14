# Security Policy

This template expects generated projects to use GitHub private vulnerability
reporting.
Replace this file with the actual support policy before publishing a generated
repository.

## Supported Versions

Do not claim support windows or release lines until the generated project
actually maintains them.
For a brand-new project, a short policy such as "only the latest release is
supported" is usually enough.

## Reporting a Vulnerability

Report vulnerabilities privately through GitHub's private vulnerability
reporting flow when it is enabled for the generated repository.

Do not use public GitHub issues, pull requests, discussions, chat channels, or
other public forums for vulnerability reports.

When reporting a vulnerability, include as much of the following as possible:

- affected version, commit, or deployment identifier
- a description of the issue and the security impact
- steps to reproduce or a minimal proof of concept
- any relevant logs, output, or traces
- any suggested mitigations or fixes, if available

If the project has a documented disclosure timeline, add it here.
If not, keep the policy short and avoid inventing guarantees.

## Known Upstream Advisories

CVE-2026-44405 / GHSA-r374-rxx8-8654 (SHA-1 use in paramiko's `rsakey.py`) is
fixed in paramiko 5.0.0, which this project cannot yet adopt: paramiko arrives
transitively through pyinfra, and pyinfra caps it at `paramiko>=2.11,<5` — and
`types-paramiko<5` alongside it. No published pyinfra release relaxes those
bounds, so constraining paramiko to 5.x makes the dependency tree
unresolvable rather than fixing anything.

The exposure is limited to consumers deploying over SSH to hosts presenting
RSA host keys. This package never imports paramiko, and its tests run against
`@local`, which does not use it.

Remove this section once pyinfra admits `paramiko>=5` and the lockfile picks
up the fixed release.
