#!/usr/bin/env bash
# Installs the soul-connector as a GNOME Shell extension.
#
# It copies nothing: it symlinks from ~/.local/share/gnome-shell/extensions to
# this folder in the repo. That way editing the code here is editing the
# installed extension, and uninstalling is deleting a symlink.
#
# Everything it touches lives in the user's home. It installs no system
# packages, does not touch the Tero daemon, needs no sudo. See uninstall.sh.

set -euo pipefail

UUID="soul-connector@tero.local"
SOURCE="$(dirname "$(readlink -f "$0")")"
TARGET="$HOME/.local/share/gnome-shell/extensions/$UUID"

if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
  echo "✗ Ya existe $TARGET y no es un symlink."
  echo "  Movelo o borralo a mano antes de seguir; no lo toco por las dudas."
  exit 1
fi

mkdir -p "$(dirname "$TARGET")"
ln -sfn "$SOURCE" "$TARGET"
echo "✓ Enlazada: $TARGET -> $SOURCE"

gnome-extensions enable "$UUID" 2>/dev/null && echo "✓ Habilitada" || {
  echo "! No se pudo habilitar todavía."
  echo "  GNOME Shell no relee las extensiones nuevas hasta reiniciarse, y en"
  echo "  Wayland eso significa cerrar sesión y volver a entrar."
  echo "  Después de reloguear: gnome-extensions enable $UUID"
  exit 0
}

echo
echo "Para sacarla: ./uninstall.sh"
