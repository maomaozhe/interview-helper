#!/bin/sh
# Keep the remote model private; the forward is reachable on Docker's bridge only.
set -eu
# Resolve local paths explicitly when the key lives on a Windows mount.
: "${SSH_TARGET:?Set SSH_TARGET to your SSH alias or user@host}"
TASK_SOCKET=${SSH_CONTROL_SOCKET:-/run/interview-system-one-ssh.sock}
SSH_KEY_PATH=${SSH_KEY_PATH:-"$HOME/.ssh/id_ed25519"}
SSH_KNOWN_HOSTS=${SSH_KNOWN_HOSTS:-"$HOME/.ssh/known_hosts"}
if ssh -S "$TASK_SOCKET" -O check "$SSH_TARGET" 2>/dev/null; then
  exit 0
fi
TASK_KEY_DIR=$(mktemp -d /tmp/interview-system-one.XXXXXXXX)
trap 'test ! -f "$TASK_KEY_DIR/key" || unlink "$TASK_KEY_DIR/key"; rmdir "$TASK_KEY_DIR"' EXIT
install -m 600 "$SSH_KEY_PATH" "$TASK_KEY_DIR/key"
ssh -M -S "$TASK_SOCKET" -f -N -i "$TASK_KEY_DIR/key" \
  -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$SSH_KNOWN_HOSTS" \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 172.17.0.1:18789:127.0.0.1:18788 "$SSH_TARGET"
