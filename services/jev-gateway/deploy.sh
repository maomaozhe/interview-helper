#!/bin/sh
set -eu
SERVICE_DIR=/home/dylan/services/interview-jev
cd "$SERVICE_DIR"
test "$(pwd -P)" = "$SERVICE_DIR"
test -f service.env
sed -i 's/\r$//' service.env
chmod 600 service.env
if test -f service.pid; then
  previous_pid=$(cat service.pid)
  case "$previous_pid" in *[!0-9]*|'') exit 1;; esac
  if test -r "/proc/$previous_pid/cmdline"; then
    previous_command=$(tr '\000' ' ' < "/proc/$previous_pid/cmdline")
    case "$previous_command" in *"$SERVICE_DIR/server.py"*|*"$SERVICE_DIR/laya_server.py"*) kill "$previous_pid";; *) echo 'PID belongs to another process; refusing restart'; exit 1;; esac
  fi
fi
set -a
. ./service.env
set +a
export PYTHONPATH="$SERVICE_DIR/laya-src"
nohup "${PYTHON_BIN:-python3}" -u "$SERVICE_DIR/${SERVER_SCRIPT:-server.py}" > "$SERVICE_DIR/service.log" 2>&1 < /dev/null &
echo $! > service.pid
