#!/bin/sh
# Keep the remote model private; the forward is reachable on Docker's bridge only.
set -eu
TASK_SOCKET=/run/interview-system-one-ssh.sock
if ssh -S "$TASK_SOCKET" -O check dylan@101.47.18.72 2>/dev/null; then
  exit 0
fi
TASK_KEY_DIR=$(mktemp -d /tmp/interview-system-one.XXXXXXXX)
trap 'test ! -f "$TASK_KEY_DIR/key" || unlink "$TASK_KEY_DIR/key"; rmdir "$TASK_KEY_DIR"' EXIT
install -m 600 /mnt/c/Users/maomao/.ssh/id_ed25519 "$TASK_KEY_DIR/key"
ssh -M -S "$TASK_SOCKET" -f -N -i "$TASK_KEY_DIR/key" \
  -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/mnt/c/Users/maomao/.ssh/known_hosts \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 172.17.0.1:18789:127.0.0.1:18788 dylan@101.47.18.72
