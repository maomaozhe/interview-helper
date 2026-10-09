#!/bin/sh
# Separate private bridge forward; keeps the existing Laya service intact.
set -eu
: "${QWEN_SSH_HOST:?Set QWEN_SSH_HOST to the SSH destination}"
: "${QWEN_SSH_IDENTITY_FILE:?Set QWEN_SSH_IDENTITY_FILE to the private key path}"
TASK_SOCKET=${QWEN_SSH_CONTROL_SOCKET:-$HOME/.ssh/interview-qwen-system-one.sock}
TASK_KNOWN_HOSTS=${QWEN_SSH_KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}
TASK_FORWARD=${QWEN_SSH_FORWARD:-127.0.0.1:18791:127.0.0.1:18790}
case "$QWEN_SSH_HOST" in -*) echo 'SSH destination cannot start with a dash' >&2; exit 2;; esac
test -r "$QWEN_SSH_IDENTITY_FILE" && test -r "$TASK_KNOWN_HOSTS"
if ssh -S "$TASK_SOCKET" -O check "$QWEN_SSH_HOST" 2>/dev/null; then exit 0; fi
TASK_KEY_DIR=$(mktemp -d /tmp/interview-qwen-system-one.XXXXXXXX)
trap 'test ! -f "$TASK_KEY_DIR/key" || unlink "$TASK_KEY_DIR/key"; rmdir "$TASK_KEY_DIR"' EXIT
install -m 600 "$QWEN_SSH_IDENTITY_FILE" "$TASK_KEY_DIR/key"
ssh -M -S "$TASK_SOCKET" -f -N -i "$TASK_KEY_DIR/key" \
  -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$TASK_KNOWN_HOSTS" \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L "$TASK_FORWARD" "$QWEN_SSH_HOST"
