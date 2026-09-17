"""Reads the contents of the active terminal through AT-SPI (desktop
accessibility), if the app that currently has focus exposes a node with
role "terminal" -- which is the case for native GTK/Qt terminals (ptyxis,
GNOME Terminal, Konsole) but not for the ones that draw their own UI with
their own graphics engine (Warp, Alacritty, Kitty): those never even show
up in the AT-SPI tree.

It runs under the **system** Python, not the project venv: PyGObject
(`gi`) is not in the venv (see CLAUDE.md) and adding it pulls in system
libs (girepository) that are already resolved for the system interpreter
-- simpler to invoke it through subprocess, the same pattern the project
already uses with playerctl/wmctrl/wl-paste instead of adding Python
dependencies. It is called with the absolute path to the interpreter, not
plain "python3": inside `uv run`, PATH may resolve to the venv one.

Output: the text on stdout and exit code 0 if it found an active terminal
with contents; exit code 1 with nothing on stdout if the active window is
not a terminal it recognizes (the caller falls back to another path, it is
not an error).
"""

import sys

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception:
    sys.exit(1)

_CHAR_LIMIT = 4000
_MAX_DEPTH = 40  # safety bound, no real tree is that deep


def _find_terminal_node(obj, depth=0):
    if depth > _MAX_DEPTH:
        return None
    try:
        if "terminal" in (obj.get_role_name() or "").lower():
            return obj
    except Exception:
        return None
    try:
        child_count = obj.get_child_count()
    except Exception:
        return None
    for i in range(child_count):
        try:
            child = obj.get_child_at_index(i)
        except Exception:
            continue
        if child is None:
            continue
        found = _find_terminal_node(child, depth + 1)
        if found is not None:
            return found
    return None


def _active_window():
    """The app+frame that currently has focus, or (None, None) if it could
    not be determined. It does not depend on wmctrl or the gnome-shell
    D-Bus API: AT-SPI's own ACTIVE state already knows."""
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        try:
            frame_count = app.get_child_count()
        except Exception:
            continue
        for j in range(frame_count):
            try:
                frame = app.get_child_at_index(j)
            except Exception:
                continue
            if frame is None:
                continue
            try:
                if frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                    return app, frame
            except Exception:
                continue
    return None, None


def main() -> int:
    _app, frame = _active_window()
    if frame is None:
        return 1
    node = _find_terminal_node(frame)
    if node is None:
        return 1
    try:
        text = Atspi.Text.get_text(node, 0, -1)
    except Exception:
        return 1
    text = (text or "").strip()
    if not text:
        return 1
    sys.stdout.write(text[-_CHAR_LIMIT:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
