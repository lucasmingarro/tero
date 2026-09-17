"""Moving an app's window to another monitor on the desktop.

Why not wmctrl: wmctrl can only touch X11/XWayland windows from outside the
compositor. Under Wayland (which is how this desktop runs, see CLAUDE.md)
most native apps simply ignore it -- the same problem tools/
_youtube_screen.py already had, dodged there by forcing
--ozone-platform=x11 when launching Chrome. This generalizes: calling
Meta.Window.move_to_monitor() from INSIDE the compositor -- a D-Bus method
exposed by soul-connector-gnome/extension.js, which already runs as part of
the shell -- does not care whether the window is X11 or native Wayland.

Validated live before writing this: an empty terminal was opened inside a
nested shell (gnome-shell --devkit) and moved across monitors through this
very method, confirmed with the monitor before and after. See the project
memory, entry "mover ventana a monitor".

Requires soul-connector-gnome to be loaded with this method (after a logout
if extension.js was edited after the last session start -- GNOME caches the
code, see that folder's README).
"""

import json
import re
import subprocess

from tools import tool

_BUS = "org.gnome.Shell"
_PATH = "/org/gnome/Shell/Extensions/Tero"
_IFACE = "org.gnome.Shell.Extensions.Tero"


def _call(method: str, *args: str) -> dict:
    process = subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", _BUS,
            "--object-path", _PATH,
            "--method", f"{_IFACE}.{method}",
            *args,
        ],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if process.returncode != 0:
        error = process.stderr.strip()
        if "UnknownMethod" in error or "UnknownObject" in error:
            return {
                "ok": False,
                "error": "la extensión de Tero en GNOME todavía no tiene esta "
                "función cargada (hace falta cerrar sesión y volver a entrar)",
            }
        return {"ok": False, "error": error}
    match = re.search(r"^\(\s*'(.*)'\s*,?\)\s*$", process.stdout.strip(), re.DOTALL)
    if not match:
        return {"ok": False, "error": f"respuesta inesperada de D-Bus: {process.stdout!r}"}
    return json.loads(match.group(1))


@tool
def move_window_to_monitor(app: str, monitor: int) -> str:
    """Moves an app's window to another monitor on the desktop.

    app: the app name or part of its window title (e.g. "chrome",
    "terminal", "spotify", "code").
    monitor: monitor number, starting at 1.
    """
    result = _call("MoveWindowToMonitor", app, str(monitor - 1))
    if not result.get("ok"):
        # The error strings are the wire protocol with extension.js: they
        # are matched here, so both sides have to be changed together.
        error = result.get("error", "error desconocido")
        if "window not found" in error:
            return f"No encontré ninguna ventana de {app!r} abierta."
        if error.startswith("no monitor"):
            return f"Este escritorio no tiene el monitor {monitor}."
        return f"No pude mover la ventana: {error}"
    return f"Movida la ventana de {result['title']!r} al monitor {monitor}."
