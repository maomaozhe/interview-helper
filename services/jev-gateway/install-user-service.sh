#!/bin/sh
# Adopt only the previously deployed process in this exact managed directory.
set -eu
SERVICE_DIR=${SERVICE_DIR:-"$HOME/services/interview-jev"}
PYTHON_BIN=${PYTHON_BIN:-"$SERVICE_DIR/.venv/bin/python"}
USER_UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
# Render only absolute, single-line paths safe for systemd's quoted fields.
case "$PYTHON_BIN" in /*) ;; *) echo 'PYTHON_BIN must be absolute'; exit 1;; esac
case "$SERVICE_DIR:$PYTHON_BIN" in
  *'"'*|*'\'*|*'%'*) echo 'Paths cannot contain quotes, backslashes, or systemd specifiers'; exit 1;;
esac
if printf '%s' "$SERVICE_DIR$PYTHON_BIN" | LC_ALL=C grep -q '[[:cntrl:]]'; then
  echo 'Paths cannot contain control characters'; exit 1
fi
test -x "$PYTHON_BIN"
test "$(cd "$SERVICE_DIR" && pwd -P)" = "$SERVICE_DIR"
test -f "$SERVICE_DIR/service.env"
mkdir -p "$USER_UNIT_DIR"
TARGET="$USER_UNIT_DIR/interview-system-one.service"
if test -f "$TARGET"; then
  grep -Fqx "ExecStart=\"$PYTHON_BIN\" -u \"$SERVICE_DIR/laya_server.py\"" "$TARGET" ||
    grep -Fqx "ExecStart=$PYTHON_BIN -u $SERVICE_DIR/laya_server.py" "$TARGET"
fi
TASK_UNIT=$(mktemp "$TARGET.tmp.XXXXXXXX")
trap 'test ! -f "$TASK_UNIT" || unlink "$TASK_UNIT"' EXIT
escape_sed() { printf '%s' "$1" | sed 's/[&|\\]/\\&/g'; }
sed -e "s|@SERVICE_DIR@|$(escape_sed "$SERVICE_DIR")|g" \
  -e "s|@PYTHON_BIN@|$(escape_sed "$PYTHON_BIN")|g" \
  "$SERVICE_DIR/interview-system-one.service" > "$TASK_UNIT"
install -m 600 "$TASK_UNIT" "$TARGET"
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
