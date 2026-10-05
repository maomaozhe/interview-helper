#!/bin/sh
# Adopt only the previously deployed process in this exact managed directory.
set -eu
SERVICE_DIR=/home/dylan/services/interview-jev
test "$(id -un)" = dylan
test "$(cd "$SERVICE_DIR" && pwd -P)" = "$SERVICE_DIR"
test -f "$SERVICE_DIR/service.env"
mkdir -p /home/dylan/.config/systemd/user
TARGET=/home/dylan/.config/systemd/user/interview-system-one.service
if test -f "$TARGET"; then
  grep -Fq 'ExecStart=/home/dylan/h3/.venv/bin/python -u /home/dylan/services/interview-jev/laya_server.py' "$TARGET"
fi
install -m 600 "$SERVICE_DIR/interview-system-one.service" "$TARGET"
systemctl --user daemon-reload
if ! systemctl --user is-active --quiet interview-system-one.service; then
  if test -f "$SERVICE_DIR/service.pid"; then
    previous_pid=$(cat "$SERVICE_DIR/service.pid")
    case "$previous_pid" in *[!0-9]*|'') exit 1;; esac
    if test -r "/proc/$previous_pid/cmdline"; then
      previous_command=$(tr '\000' ' ' < "/proc/$previous_pid/cmdline")
      case "$previous_command" in *"$SERVICE_DIR/laya_server.py"*) kill "$previous_pid";; *) echo 'PID belongs to another process; refusing adoption'; exit 1;; esac
      for wait_round in 1 2 3 4 5; do
        test -r "/proc/$previous_pid/cmdline" || break
        sleep 1
      done
    fi
  fi
fi
systemctl --user enable --now interview-system-one.service
systemctl --user is-enabled interview-system-one.service
systemctl --user show interview-system-one.service -p ActiveState -p SubState -p MainPID
