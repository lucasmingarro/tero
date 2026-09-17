"""A dedicated Chrome window to show YouTube on a fixed monitor (the
user's desktop "YouTube monitor").

Why a separate Chrome profile forced onto XWayland: the Chrome the user
runs day to day is a native Wayland client, and a native Wayland client
does not let any external program tell it which monitor to open on --
tested live, `--window-position` had no effect at all and the window
always ended up on the active monitor, whatever value was asked for.
Forcing `--ozone-platform=x11` (with its own `--user-data-dir`, so it
really starts a new process instead of asking the already running Chrome
for a window, which would inherit its Wayland backend) makes the window a
real X11/XWayland window, which mutter does allow moving -- the same
pattern `soul_connector/window.py` already used (`QT_QPA_PLATFORM=xcb`)
for the same reason.

`--window-position` at launch is not enough either: tested live, the
window still shows up on the default monitor, ignoring the flag. What does
work, confirmed live: let it open wherever it wants and then move it with
`wmctrl -ir <window> -e ...`, locating the window by the PID of the
just-launched process (not by title -- the title changes with every
URL/channel, the PID does not).

Switching channels navigates the SAME tab over CDP (`--remote-debugging-
port`, localhost only) instead of killing the window and opening a new one
-- an earlier version of this file did the latter, and the user asked why
the tab was not reused. He was right, and it was not just about tidiness:
killing and reopening changed the identity of the audio stream in PipeWire
on every channel switch, which genuinely broke ducking
(`tools/_ducking.py`) -- the volume stayed stuck at the ducked 10% after
switching channels mid-conversation, because the Ducker was still tracking
the old, already dead node. Reusing the tab removes the cause instead of
patching the symptom: the audio stream never changes identity between
channels, because the process is never closed. If CDP fails for whatever
reason (the window is not open, the user closed it by hand), it falls back
to the old path of opening a new window.
"""

import asyncio
import json as _json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

# Chrome on XWayland, moved with wmctrl, identified through /proc: Linux
# desktop, end to end. tools/youtube.py takes its own SUPPORTED_PLATFORMS
# from here, and tools/music.py asks `available()` before using the
# "the user named a known channel" shortcut.
SUPPORTED_PLATFORMS = {"linux"}


def available() -> bool:
    return sys.platform in SUPPORTED_PLATFORMS


PROFILE = Path.home() / ".config" / "tero" / "chrome_youtube"

# Localhost only (Chrome's default when --remote-debugging-address is not
# passed), so nothing is exposed outside this machine.
_CDP_PORT = 9333

# Chrome appends this to the title of every window; what matters for the
# soul-connector is what comes before it.
_TITLE_SUFFIXES = (" - YouTube - Google Chrome", " - Google Chrome")

# Fixed geometry of the monitor where "YouTube" lives on this desktop (the
# Samsung C24FG70, top left -- confirmed live with the user trying
# different positions). If the monitor layout ever changes, `xrandr` shows
# the real geometry of each one.
MONITOR = {"x": 0, "y": 0, "width": 1920, "height": 1080}


def show(url: str) -> None:
    """Shows `url` on the dedicated window, on the YouTube monitor.

    If the window is already open, it navigates in place (CDP) -- same
    process, same audio stream, no flicker. If there is no live window
    (first time, or CDP failed for whatever reason), it opens one from
    scratch."""
    if _navigate_via_cdp(url):
        return
    _close()
    PROFILE.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            "google-chrome",
            "--ozone-platform=x11",
            "--new-window",
            f"--user-data-dir={PROFILE}",
            f"--window-position={MONITOR['x']},{MONITOR['y']}",
            f"--window-size={MONITOR['width']},{MONITOR['height']}",
            # Without this, Chrome blocks autoplay with sound on a profile
            # with no prior "engagement" -- and this profile, being
            # dedicated, never has any: every video sat paused waiting for
            # a click. The user asked for it to start on its own.
            "--autoplay-policy=no-user-gesture-required",
            f"--remote-debugging-port={_CDP_PORT}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _reposition(process.pid)


def _navigate_via_cdp(url: str) -> bool:
    """True if it managed to navigate an already open tab to `url` through
    Chrome's remote debugging protocol (CDP). False on any problem (window
    not open, port not answering, no tabs) -- in that case `show()` falls
    back to opening a new window."""
    try:
        tabs = httpx.get(f"http://127.0.0.1:{_CDP_PORT}/json", timeout=2.0).json()
    except Exception:
        return False
    tab = next((t for t in tabs if t.get("type") == "page"), None)
    debug_url = tab.get("webSocketDebuggerUrl") if tab else None
    if not debug_url:
        return False
    try:
        asyncio.run(_send_navigate(debug_url, url))
        return True
    except Exception:
        return False


async def _send_navigate(debug_url: str, url: str) -> None:
    async with websockets.connect(debug_url) as ws:
        await ws.send(_json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        await ws.recv()


def _close() -> None:
    subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], check=False)


def _is_our_process(pid: str) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
    except OSError:
        return False
    return f"user-data-dir={PROFILE}" in cmdline


def _clean_title(title: str) -> str:
    for suffix in _TITLE_SUFFIXES:
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            break
    # Chrome prefixes "(N) " when there are unread notifications/chat --
    # useless here.
    return re.sub(r"^\(\d+\)\s*", "", title).strip()


def _mpris_player() -> str | None:
    """Name of the MPRIS player (`playerctl -l`) for the dedicated window,
    if it is open and has active media. Chrome exposes every window with
    audio/video as its own MPRIS player (`chromium.instance<PID>`) -- and
    the PID in that name is exactly the one of the process launched by
    `show()`, so the same `--user-data-dir` filter used by
    `current_title()` is enough, with no need to remember the PID between
    calls."""
    try:
        output = subprocess.run(
            ["playerctl", "-l"], capture_output=True, text=True, timeout=2.0, check=False
        ).stdout
    except Exception:
        return None
    for name in output.splitlines():
        name = name.strip()
        match = re.match(r"^chromium\.instance(\d+)$", name)
        if match and _is_our_process(match.group(1)):
            return name
    return None


def pause() -> None:
    """Pauses whatever is playing in the YouTube window, if anything. Does
    nothing (silently) if the window is not open or has no active media --
    called from tools/music.py every time music starts, so the two do not
    play at once."""
    name = _mpris_player()
    if name:
        subprocess.run(["playerctl", "-p", name, "pause"], check=False)


def current_title() -> str | None:
    """Title of whatever the dedicated window is showing right now --
    whether Tero opened it (play_youtube_channel) or the user did by hand
    navigating inside that same window, it does not matter: the real title
    is read, nothing is remembered about what was asked for. None if the
    window is not open."""
    try:
        output = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, timeout=2.0, check=False
        ).stdout
    except Exception:
        return None
    for line in output.splitlines():
        # wmctrl -lp: window id, desktop, pid, hostname, title.
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        _window, _desktop, pid, _hostname, title = parts
        if _is_our_process(pid):
            return _clean_title(title)
    return None


def _reposition(pid: int, attempts: int = 20, wait_s: float = 0.2) -> None:
    """Waits for the window of `pid` to show up and places it on MONITOR.

    It retries because Chrome takes a while to map the window (more so the
    first time a new profile starts, which may also show a terms of service
    dialog before anything else -- that dialog is another window of the
    same process and has to be moved too, which is why it keeps
    repositioning even after having moved one already, instead of stopping
    at the first success)."""
    target = f"0,{MONITOR['x']},{MONITOR['y']},{MONITOR['width']},{MONITOR['height']}"
    for _ in range(attempts):
        time.sleep(wait_s)
        output = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, check=False
        ).stdout
        for line in output.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 3 and parts[2] == str(pid):
                subprocess.run(
                    ["wmctrl", "-ir", parts[0], "-e", target], check=False
                )
                # Really maximize (a window state mutter manages), not just
                # ask for the monitor size by hand -- that way it does not
                # depend on MONITOR having the exact resolution right (with
                # decorations, scaling, etc. the "eyeballed" size left a
                # visible margin around it, reported live by the user).
                subprocess.run(
                    ["wmctrl", "-ir", parts[0], "-b",
                     "add,maximized_vert,maximized_horz"],
                    check=False,
                )
                return
