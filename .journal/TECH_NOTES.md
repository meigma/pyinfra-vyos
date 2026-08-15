# Technical Notes

- Wave-1 surface: `config_load(src, *, save=False)` + `Version` /
  `Configuration` / `ConfigurationCommands`. Design authority:
  `.journal/001/ARCHITECTURE.md`; wave-2 candidates: `.journal/001/RESEARCH.md`.
- VyOS scripting landmines (all hardware-verified in session 001):
  `script-template` aliases `exit` → use `builtin exit`; `run` is an alias →
  must be on its own line inside `vbash -c`; wrapper exit codes untrustworthy →
  gate on `cli-shell-api sessionChanged`; redaction = pipeline through
  `/usr/libexec/vyos/strip-private.py` (interactive `| strip-private` is
  unavailable non-interactively); `hw-id` pins NIC MACs — strip when moving
  images between VMs; footerless configs trigger the full migration chain on
  `load`.
- Tests: `moon run root:check` (unit) / `root:test-integration` (@local tier);
  appliance tier via `tests/appliance/vyos-lab test` (Lima VM, VyOS Stream
  2026.03, image cached in `~/.cache/pyinfra-vyos`). Gate tiers with pytest
  `get_closest_marker`, never `item.keywords` (directory names leak in).
- Release: Conventional Commits → Release Please PR → squash-merge → draft
  release + tag → trusted publish to PyPI → human publishes draft. App
  credentials live in 1Password `Development` / `meigma-release-please`.
