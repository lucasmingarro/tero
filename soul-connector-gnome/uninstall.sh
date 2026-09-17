#!/usr/bin/env bash
# Removes the soul-connector extension and leaves the system as it was.
#
# The only things this extension leaves outside the repo are a symlink and the
# uuid recorded in GNOME's extension list (dconf). This deletes both. There are
# no system packages to uninstall, and no daemon changes to revert: the
# pywebview soul-connector (soul_connector/) stays intact and working, and
# going back to the main branch requires nothing here.

set -euo pipefail

UUID="soul-connector@tero.local"
TARGET="$HOME/.local/share/gnome-shell/extensions/$UUID"

gnome-extensions disable "$UUID" 2>/dev/null && echo "✓ Deshabilitada" \
  || echo "  (no estaba habilitada)"

# `disable` takes it out of enabled-extensions but may leave it recorded in
# disabled-extensions; that is cleaned too, so no trace is left in dconf.
current=$(gsettings get org.gnome.shell disabled-extensions 2>/dev/null || echo "@as []")
if [[ "$current" == *"$UUID"* ]]; then
  updated=$(python3 - "$current" "$UUID" <<'PY'
import ast, sys
value = sys.argv[1].removeprefix("@as ").strip()
uuid = sys.argv[2]
remaining = [x for x in ast.literal_eval(value) if x != uuid]
print("[" + ", ".join("'" + x + "'" for x in remaining) + "]")
PY
)
  gsettings set org.gnome.shell disabled-extensions "$updated"
  echo "✓ Limpiada de dconf (disabled-extensions)"
fi

if [ -L "$TARGET" ]; then
  rm "$TARGET"
  echo "✓ Symlink borrado: $TARGET"
elif [ -e "$TARGET" ]; then
  echo "! $TARGET existe pero no es un symlink: no lo borro por las dudas."
else
  echo "  (no había symlink)"
fi

echo
echo "Listo. El código sigue en el repo; esto solo lo desconectó de GNOME."
echo "El soul-connector original (pywebview) no fue tocado: uv run python -m soul_connector.window"
