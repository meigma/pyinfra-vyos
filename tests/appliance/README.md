# VyOS appliance lab (Lima)

Disposable VyOS appliance for the opt-in `--appliance` pytest tier, powered
by [Lima](https://lima-vm.io). One command builds a cached qcow2 from the
pinned official VyOS Stream ISO and boots it; the test tier then talks to it
over SSH on localhost.

```sh
tests/appliance/vyos-lab test    # build if needed, start VM, run the tier
tests/appliance/vyos-lab down    # stop and delete the VM (image cache kept)
```

Subcommands: `build`, `up`, `env` (print `PYINFRA_VYOS_TEST_*` exports for
manual runs), `test`, `down`.

## Requirements

- `lima` and `qemu` (`brew install lima qemu`)
- `expect` (ships with macOS)
- disk: ~600 MB ISO + 10 GB image under `~/.cache/pyinfra-vyos`

## How it works

Free official VyOS images are amd64 live ISOs without cloud-init, and VyOS
cannot run Lima's guest agent or standard cloud-init provisioning. The
harness therefore prepares the image once, outside Lima:

1. `build-image.sh` downloads the pinned Stream ISO (SHA-256 verified),
   boots it under QEMU with a serial console, and drives `install image`
   with `install.expect` (prompt patterns grounded in the Circinus
   `image_installer.py` source).
2. `configure.expect` boots the installed disk once and configures
   `eth0 dhcp`, SSH on port 22, and the Lima user key
   (`~/.lima/_config/user.pub`) on the `vyos` user, then `commit`, `save`,
   power off.
3. `vyos-lab up` renders `lima-vyos.yaml.in` with the image path and starts
   the instance in Lima `plain` mode; SSH is reachable through QEMU's
   hostfwd at `127.0.0.1:60022`.

On Apple Silicon this is full-system x86_64 emulation: the one-time image
build takes on the order of an hour, and VM boot takes several minutes.
The built image is cached and reused; `PYINFRA_VYOS_REBUILD=1` forces a
rebuild.

## Notes

- The image tracks VyOS Stream 2026.03 (Circinus / 1.5 lineage). Bump
  `VYOS_VERSION` and `ISO_SHA256` in `build-image.sh` together.
- The `vyos` user keeps the default password `vyos` (override at build time
  with `PYINFRA_VYOS_LAB_PASSWORD`); the VM is only reachable via localhost
  hostfwd on the loopback interface.
- The appliance tier commits and saves config on the target; that is exactly
  what this VM is for. Never point the tier at a production router.
