"""Reading the active terminal without making the user copy anything by
hand when it is a native terminal (GTK/Qt): it is read through
accessibility (AT-SPI, see `_read_terminal_atspi.py`), which exposes the
contents of native widgets as if for a screen reader. It does not work
with terminals that draw their own UI (Warp, Alacritty, Kitty): those
never show up in the accessibility tree.

For those, the fallback is the X/Wayland **primary selection** -- whatever
is highlighted with the mouse, without pressing Ctrl+C -- and nothing
else. The Ctrl+C clipboard is deliberately NOT checked: the user may have
something else copied there for a different purpose, and does not want
Tero to grab it.
"""

import subprocess
from pathlib import Path

from tools import tool

# AT-SPI and the X/Wayland primary selection: both Linux desktop. The macOS
# way in is Accessibility (AXUIElement) and there is no primary selection at
# all -- phase 2.3 of docs/PLAN-MULTIPLATAFORMA.md.
SUPPORTED_PLATFORMS = {"linux"}

_CHAR_LIMIT = 4000
_ATSPI_SCRIPT = Path(__file__).parent / "_read_terminal_atspi.py"
_SYSTEM_PYTHON = "/usr/bin/python3"


def _from_atspi() -> str | None:
    try:
        result = subprocess.run(
            [_SYSTEM_PYTHON, str(_ATSPI_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _from_primary_selection() -> str | None:
    for command in (["wl-paste", "--primary"], ["xclip", "-o", "-selection", "primary"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return None


@tool
def read_terminal() -> str:
    """Reads the contents of the active terminal: through accessibility if
    it is a native terminal (ptyxis, GNOME Terminal), or whatever was last
    highlighted with the mouse (primary selection) if not."""
    contents = _from_atspi() or _from_primary_selection()
    if not contents:
        return (
            "No pude leer nada: la ventana activa no es una terminal que pueda "
            "leer directamente, y no hay nada resaltado con el mouse."
        )
    return contents.strip()[-_CHAR_LIMIT:]
