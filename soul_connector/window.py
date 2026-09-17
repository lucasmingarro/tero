"""Soul-connector window: a separate process, an optional client of the
level stream. The daemon (main.py) has to work without this -- run it
yourself, separately, whenever you want to see it:

    uv run python -m soul_connector.window

There is no real "always on top" on Wayland/GNOME without gtk-layer-shell
(which needs system packages and there is no guarantee Mutter supports it
well for normal apps). Something simpler was chosen: the window never steals
focus (focus=False), and it brings itself to the front right when it starts
speaking (see bring_to_front, called from soul_connector/index.html over
WebSocket). That is enough: it only matters that it is visible while
speaking, not the rest of the time.
"""

import subprocess
from pathlib import Path

import webview

_WINDOW_TITLE = "tero-soul-connector"

_HTML_PATH = Path(__file__).parent / "index.html"
_SIRIWAVE_PATH = Path(__file__).parent / "siriwave.umd.js"
_WIDTH, _HEIGHT = 260, 74
_MARGIN = 20

def _html_with_inlined_js() -> str:
    # With url= (serving the file through pywebview's internal Bottle
    # server) the size/position options were not honored properly here --
    # with html= they are (the same path as the POC that worked).
    # siriwave.umd.js is inlined so it does not depend on the relative
    # <script src=...> resolving without a real base URL.
    html = _HTML_PATH.read_text()
    js = _SIRIWAVE_PATH.read_text()
    return html.replace(
        '<script src="siriwave.umd.js"></script>', f"<script>{js}</script>"
    )


class _API:
    def __init__(self, window: "webview.Window"):
        self._window = window
        self._window_id: str | None = None

    def bring_to_front(self) -> None:
        # Qt's on_top flag (see platforms/qt.py, set_on_top) does not hold:
        # Mutter stops honoring it as soon as the user focuses another
        # window. wmctrl asks the window manager for the same state
        # ("above") the user's "Always on top" menu item would set -- it
        # holds better because it goes through that other path. Both are
        # kept: wmctrl as the main attempt, the Qt flag as a fallback if
        # wmctrl is missing or fails.
        self._window.on_top = True
        self._window.show()
        if self._window_id is None:
            self._window_id = _find_window_id()
        if self._window_id is not None:
            subprocess.run(
                ["wmctrl", "-i", "-r", self._window_id, "-b", "add,above"],
                check=False,
            )


def _find_window_id() -> str | None:
    try:
        result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    for line in result.stdout.splitlines():
        if _WINDOW_TITLE in line:
            return line.split()[0]
    return None


def _bottom_right_position() -> tuple[int, int]:
    screens = webview.screens
    primary = screens[0] if screens else None
    if primary is None:
        return (100, 100)
    x = primary.x + primary.width - _WIDTH - _MARGIN
    y = primary.y + primary.height - _HEIGHT - _MARGIN
    return (x, y)


def main() -> None:
    x, y = _bottom_right_position()
    window = webview.create_window(
        _WINDOW_TITLE,
        html=_html_with_inlined_js(),
        width=_WIDTH,
        height=_HEIGHT,
        x=x,
        y=y,
        frameless=True,
        # Mutter does not let the app reposition itself (neither the x/y at
        # creation nor wmctrl -e work, tested with _MARGIN=400 with no
        # visual change at all). easy_drag=True is the only real way to move
        # it: click and drag anywhere on the window.
        easy_drag=True,
        focus=False,
        on_top=False,
        transparent=True,
        resizable=False,
        shadow=False,
    )
    window.expose(_API(window).bring_to_front)
    webview.start(gui="qt")


if __name__ == "__main__":
    main()
