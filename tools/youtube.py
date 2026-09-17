"""YouTube channels: a voice shortcut to open them live (or to report what
is new) straight on the monitor dedicated to YouTube.

`play_youtube_channel` takes free text, not a closed list of channels --
see `tools/_youtube_favorites.py` for why (in short: a fixed
list/`Literal[...]` does not generalize, and the model cannot even call the
tool with something that is not in the enum) and how it learns new channels
by use.
"""

import re
from datetime import datetime, timedelta, timezone

import httpx

from tools import _youtube_favorites, _youtube_screen, tool
from tools.music import pause_spotify

# The whole module: the dedicated window is Chrome on XWayland, moved with
# wmctrl and inspected through /proc (tools/_youtube_screen.py). Phase 2.2
# of docs/PLAN-MULTIPLATAFORMA.md turns the player into a configurable
# backend, and that is what will make this work on macOS.
SUPPORTED_PLATFORMS = _youtube_screen.SUPPORTED_PLATFORMS


def current_state() -> dict | None:
    """For the soul-connector, same as it does with the Spotify song (see
    main.py, _update_soul_connector_song): {"text", "progress_ms",
    "duration_ms", "playing"} of whatever the YouTube window is showing
    right now, or None if it is not open. The real window title is read
    (_youtube_screen.current_title()), nothing is remembered about what the
    user asked for last time -- that way it also catches whatever the user
    opened by hand inside that same window, not just what Tero opened. No
    real duration (it is a live channel, not a song with a start and an
    end) -- the soul-connector knows to hide the progress bar when
    `duration_ms` is 0."""
    title = _youtube_screen.current_title()
    if not title:
        return None
    return {"text": title, "progress_ms": 0, "duration_ms": 0, "playing": True}


@tool
def play_youtube_channel(channel: str) -> str:
    """Opens the requested YouTube channel live, on the monitor dedicated
    to YouTube. Use it when the user names a specific channel ("poné
    Olga", "quiero ver Midu", "dale, Mitre", or any other channel they
    name, known or not) -- it opens straight away, nothing else needs to be
    asked. `channel`: the name as the user said it (or your best guess if
    the transcription came out broken, same criterion as with artist names
    before searching Spotify) -- it does not need to match anything
    exactly, the tool resolves it."""
    info = _youtube_favorites.find_learned(channel)
    if info is None:
        info = _youtube_favorites.resolve_by_search(channel)
        if info is None:
            return f"La herramienta 'play_youtube_channel' falló: no encontré ningún canal para {channel!r}."
    _youtube_favorites.remember(info["handle"], info["id"], info["name"])
    pause_spotify()  # so the two do not play at once
    _youtube_screen.show(f"https://www.youtube.com/@{info['handle']}/live")
    return f"Abrí {info['name']} en vivo."


@tool
def open_youtube() -> str:
    """Use it when the user asks for YouTube without saying which channel
    ("poné algo de youtube", "abrí youtube", "quiero ver algo"). It opens
    YouTube on the dedicated monitor, and your spoken answer has to ask
    whether they want something specific or whether you should tell them
    which channels have news right now -- this is the only tool for which
    asking and waiting for an answer is worth it (see the exception in the
    rules)."""
    _youtube_screen.show("https://www.youtube.com")
    return "Abrí YouTube."


def _is_live(handle: str) -> tuple[bool, str | None]:
    """(live right now, title) by following the channel's /live -- if there
    is no broadcast, YouTube redirects to the normal channel page and does
    not carry 'isLive':true."""
    try:
        response = httpx.get(
            f"https://www.youtube.com/@{handle}/live",
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=6.0,
        )
    except Exception:
        return False, None
    if '"isLive":true' not in response.text:
        return False, None
    match = re.search(r'"title":"([^"]+)"', response.text)
    return True, (match.group(1) if match else None)


def _uploaded_recently(channel_id: str, hours: int = 20) -> str | None:
    """Title of the latest video in the RSS feed if it was published less
    than `hours` ago, or None. A fallback for channels that do not stream
    live often (e.g. Midu, who uploads one-off videos instead of a daily
    show) -- for those, "is it live" almost never signals anything."""
    try:
        response = httpx.get(
            "https://www.youtube.com/feeds/videos.xml",
            params={"channel_id": channel_id},
            timeout=6.0,
        )
        response.raise_for_status()
    except Exception:
        return None
    entries = response.text.split("<entry>", 1)
    if len(entries) < 2:
        return None
    entry = entries[1]
    date_match = re.search(r"<published>([^<]+)</published>", entry)
    title_match = re.search(r"<title>([^<]+)</title>", entry)
    if not date_match or not title_match:
        return None
    published = datetime.fromisoformat(date_match.group(1))
    if datetime.now(timezone.utc) - published < timedelta(hours=hours):
        return title_match.group(1)
    return None


@tool
def suggest_youtube_channels() -> str:
    """Use it only when the user has answered that they want to see "lo
    nuevo" or "videos nuevos" (an answer to the question from
    open_youtube). It returns which channels are live or uploaded something
    recently (out of the ones Tero already knows by use), so you can read
    it out and ask which one they want -- it does not open anything yet,
    that is play_youtube_channel's job on the next turn."""
    news = []
    for data in _youtube_favorites.all_channels():
        live, title = _is_live(data["handle"])
        if live:
            news.append(f"{data['name']} está en vivo" + (f" ({title})" if title else ""))
            continue
        new_title = _uploaded_recently(data["id"])
        if new_title:
            news.append(f"{data['name']} subió: {new_title}")
    if not news:
        return "Ningún canal tiene novedades ahora."
    return "; ".join(news) + "."
