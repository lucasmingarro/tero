#!/usr/bin/env bash
# Development mode: opens a nested GNOME Shell (a window with a whole desktop
# inside) that loads the extension from scratch.
#
# It exists because in the real session GNOME caches the extension code:
# `disable`/`enable` does not reload it, and on Wayland the only alternative is
# logging out. Here every run starts clean.
#
#   ./test.sh           closes the previous nested shell and opens a new one
#   ./test.sh --close   closes it and that is it
#
# For the soul-connector to move, Tero has to be running (./tero).
#
# It is launched with `setsid` so the nested shell and everything it drags along
# (calendar-server, notifications, nautilus, tracker...) end up in their own
# process group, and closing = killing that whole group. Killing only
# gnome-shell left those processes orphaned, piling up on every run.

set -u
DIR="${XDG_RUNTIME_DIR:-/tmp}"
LOG="$DIR/soul-connector-nested.log"
PGID_FILE="$DIR/soul-connector-nested.pgid"

close_nested() {
  pgid=$(cat "$PGID_FILE" 2>/dev/null)
  if [ -n "$pgid" ] && kill -0 -- "-$pgid" 2>/dev/null; then
    kill -TERM -- "-$pgid" 2>/dev/null
    for _ in 1 2 3 4 5 6; do
      kill -0 -- "-$pgid" 2>/dev/null || break
      sleep 0.5
    done
    kill -KILL -- "-$pgid" 2>/dev/null
  fi
  rm -f "$PGID_FILE"
  sweep_by_bus
}

# Killing the group is not enough: services like gvfsd are started by `systemd
# --user` through D-Bus activation, so their parent is systemd and they fall
# outside the group. What they do inherit is the nested shell's bus, which is
# always a socket in /tmp/dbus-*. The real session uses /run/user/<uid>/bus and
# never matches this filter.
sweep_by_bus() {
  for pid in $(pgrep -u "$(id -u)"); do
    bus=$(tr '\0' '\n' 2>/dev/null <"/proc/$pid/environ" | grep '^DBUS_SESSION_BUS_ADDRESS=')
    case "$bus" in
      DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/dbus-*) kill -TERM "$pid" 2>/dev/null ;;
    esac
  done
}

close_nested
if [ "${1:-}" = "--close" ]; then
  echo "Shell anidado cerrado."
  exit 0
fi

setsid env WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
  dbus-run-session -- gnome-shell --devkit >"$LOG" 2>&1 &
echo $! >"$PGID_FILE"  # with setsid, the child's pid is the group's pgid

echo "Shell anidado abierto (log: $LOG)."
echo "El soul-connector aparece abajo a la derecha de esa ventana."

sleep 8
if grep -q "JS ERROR" "$LOG"; then
  echo "Ojo, errores de JS:"
  grep "JS ERROR" "$LOG" | head -5
fi
