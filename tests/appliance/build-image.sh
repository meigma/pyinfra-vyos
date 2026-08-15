#!/usr/bin/env bash
# Build a Lima-ready VyOS qcow2 from the pinned official Stream ISO.
#
# The free official VyOS images are amd64 live ISOs without cloud-init, and
# Lima's own cloud-init user-data cannot provision a VyOS guest (VyOS runs
# only its own cloud-init modules). This script therefore automates the
# serial-console path once: boot the ISO under QEMU, drive `install image`
# with expect, boot the installed disk, configure DHCP + SSH + the Lima
# public key on the `vyos` user, then save and power off. The resulting
# qcow2 is what the Lima template boots.
#
# Requires: qemu-system-x86_64, expect (ships with macOS), curl, shasum.
# The build runs x86_64 full-system emulation on Apple Silicon: expect the
# two boots to take several minutes each. The result is cached; rebuilds
# only happen when the cache is deleted or PYINFRA_VYOS_REBUILD=1.
set -euo pipefail

VYOS_VERSION="2026.03"
ISO_URL="https://community-downloads.vyos.dev/stream/${VYOS_VERSION}/vyos-${VYOS_VERSION}-generic-amd64.iso"
# Pinned SHA-256 of the ISO above. Update together with VYOS_VERSION.
ISO_SHA256="56151a536e4a70c1a3f9202d8e6e59e7dd308cc84e2c50b633884a1376a39010"

CACHE_DIR="${PYINFRA_VYOS_CACHE:-$HOME/.cache/pyinfra-vyos}"
ISO_PATH="$CACHE_DIR/vyos-${VYOS_VERSION}-generic-amd64.iso"
IMAGE_PATH="$CACHE_DIR/vyos-${VYOS_VERSION}-lab.qcow2"
WORK_IMAGE="$IMAGE_PATH.building"
LIMA_HOME="${LIMA_HOME:-$HOME/.lima}"
LIMA_KEY="$LIMA_HOME/_config/user"
VYOS_PASSWORD="${PYINFRA_VYOS_LAB_PASSWORD:-vyos}"

log() { printf '>>> %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v qemu-system-x86_64 >/dev/null || die "qemu-system-x86_64 not found (brew install qemu)"
command -v expect >/dev/null || die "expect not found"

mkdir -p "$CACHE_DIR"

if [[ -f "$IMAGE_PATH" && "${PYINFRA_VYOS_REBUILD:-0}" != 1 ]]; then
  log "image already built: $IMAGE_PATH (set PYINFRA_VYOS_REBUILD=1 to rebuild)"
  exit 0
fi

# Lima authenticates with $LIMA_HOME/_config/user; bake its public key into
# the guest so `limactl start` can reach SSH. Generate the pair the same way
# Lima would if it is not there yet.
if [[ ! -f "$LIMA_KEY" ]]; then
  log "generating Lima user key at $LIMA_KEY"
  mkdir -p "$LIMA_HOME/_config"
  ssh-keygen -t ed25519 -q -N "" -C lima -f "$LIMA_KEY"
fi
read -r KEY_TYPE KEY_B64 _ < "$LIMA_KEY.pub"
[[ "$KEY_TYPE" == ssh-ed25519 ]] || die "expected an ssh-ed25519 Lima key, got $KEY_TYPE"

if [[ ! -f "$ISO_PATH" ]]; then
  log "downloading $ISO_URL"
  curl -fL --progress-bar -o "$ISO_PATH.part" "$ISO_URL"
  mv "$ISO_PATH.part" "$ISO_PATH"
fi

log "verifying ISO checksum"
echo "$ISO_SHA256  $ISO_PATH" | shasum -a 256 -c - >/dev/null || die "ISO checksum mismatch"

log "creating disk image"
rm -f "$WORK_IMAGE"
qemu-img create -q -f qcow2 "$WORK_IMAGE" 10G

QEMU_ARGS=(
  -machine q35
  -accel tcg,thread=multi,tb-size=512
  -cpu max
  -smp 4
  -m 4096
  -display none
  -serial mon:stdio
  -device virtio-net-pci,netdev=net0
)

log "phase 1/2: installing VyOS from ISO (several minutes under emulation)"
expect -f "$(dirname "$0")/install.expect" -- \
  qemu-system-x86_64 "${QEMU_ARGS[@]}" \
  -netdev user,id=net0 \
  -drive "file=$WORK_IMAGE,if=virtio,discard=on" \
  -drive "file=$ISO_PATH,format=raw,media=cdrom,readonly=on" \
  -boot order=d

log "phase 2/2: configuring installed system"
expect -f "$(dirname "$0")/configure.expect" -- \
  "$KEY_B64" "$VYOS_PASSWORD" \
  qemu-system-x86_64 "${QEMU_ARGS[@]}" \
  -netdev user,id=net0 \
  -drive "file=$WORK_IMAGE,if=virtio,discard=on"

mv "$WORK_IMAGE" "$IMAGE_PATH"
log "built $IMAGE_PATH"
