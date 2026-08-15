# Security Policy

## Supported Versions

Only the latest published release is supported.

## Reporting a Vulnerability

Report vulnerabilities privately through GitHub's private vulnerability
reporting flow.

Do not use public GitHub issues, pull requests, discussions, chat channels, or
other public forums for vulnerability reports.

When reporting a vulnerability, include as much of the following as possible:

- affected version, commit, or deployment identifier
- a description of the issue and the security impact
- steps to reproduce or a minimal proof of concept
- any relevant logs, output, or traces
- any suggested mitigations or fixes, if available

## Known Upstream Advisories

CVE-2026-44405 / GHSA-r374-rxx8-8654 (SHA-1 use in paramiko's `rsakey.py`) is
fixed in paramiko 5.0.0, which this project cannot yet adopt: paramiko arrives
transitively through pyinfra, and pyinfra 3.10.0 (the version locked here) caps
it at `paramiko>=2.11,<5` — and `types-paramiko<5` alongside it. No published
pyinfra release relaxes those bounds, so constraining paramiko to 5.x makes
the dependency tree unresolvable rather than fixing anything. The lockfile
resolves paramiko 4.0.0.

The exposure is limited to consumers deploying over SSH to hosts presenting
RSA host keys. This package never imports paramiko. Unit tests and the
`@local` integration tier do not use it; the opt-in `--appliance` suite does
go over SSH.

Remove this section once pyinfra admits `paramiko>=5` and the lockfile picks
up the fixed release.
