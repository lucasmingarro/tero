"""Music: real playback through the Spotify Web API (see
tools/_spotify_auth.py for the OAuth login). Opening a search URL is not
enough -- that only shows results, it does not pick or start a song -- so
the search is resolved against /v1/search to get the exact track and sent
to play with /v1/me/player/play.

Requires Spotify Premium: the Web API playback endpoint returns 403 on free
accounts.
"""

import random
import time
from typing import Literal

import httpx

from os_platform import get_platform
from tools import _spotify_auth, _youtube_favorites, _youtube_screen, tool

_API = "https://api.spotify.com/v1"


def _headers() -> dict:
    return {"Authorization": f"Bearer {_spotify_auth.get_token()}"}


def _search_track(query: str) -> dict | None:
    response = httpx.get(
        f"{_API}/search",
        params={"q": query, "type": "track", "limit": 1},
        headers=_headers(),
        timeout=10.0,
    )
    response.raise_for_status()
    items = response.json()["tracks"]["items"]
    return items[0] if items else None


def _active_device() -> str | None:
    response = httpx.get(f"{_API}/me/player/devices", headers=_headers(), timeout=10.0)
    response.raise_for_status()
    devices = response.json()["devices"]
    if not devices:
        return None
    active = next((d for d in devices if d["is_active"]), devices[0])
    return active["id"]


def playback_state() -> dict | None:
    """What Spotify is playing right now (text, progress and duration in
    ms), or None if nothing is loaded. If it is paused it still returns the
    dict (with playing=False) instead of None -- the soul-connector shows
    that differently (in red, progress not advancing) instead of hiding
    everything as if there were nothing. Not a model tool -- the
    soul-connector uses it to show what is playing under the wave."""
    try:
        response = httpx.get(f"{_API}/me/player/currently-playing", headers=_headers(), timeout=5.0)
        if response.status_code != 200 or not response.content:
            return None
        data = response.json()
        item = data.get("item")
        if not item:
            return None
        artist = item["artists"][0]["name"] if item.get("artists") else "?"
        return {
            "text": f"{item['name']} · {artist}",
            "progress_ms": data.get("progress_ms") or 0,
            "duration_ms": item.get("duration_ms") or 0,
            "playing": bool(data.get("is_playing")),
        }
    except Exception:
        return None


# How long to wait for Spotify to show up as a device after opening it.
# Measured with the snap: ~6s warm. Right after login, with a cold disk
# cache, it takes considerably longer -- with the old 10s wait it did fail
# with "ni abriendo la app". It is polled every second, so the normal case
# does not wait any longer than needed.
_OPEN_WAIT_S = 25


def _open_spotify() -> None:
    get_platform().open_uri("spotify:")


def _ensure_device() -> str | None:
    """Active Spotify Connect device, opening the app if none is visible
    yet."""
    device_id = _active_device()
    if device_id is None:
        _open_spotify()
        for _ in range(_OPEN_WAIT_S):
            time.sleep(1)
            device_id = _active_device()
            if device_id is not None:
                break
    return device_id


def _player_state() -> dict | None:
    """Playback state, or None if nothing is loaded (204). A freshly opened
    app also gives 204: that is how "I just opened it" is told apart from
    "it was open with something paused"."""
    response = httpx.get(f"{_API}/me/player", headers=_headers(), timeout=10.0)
    if response.status_code == 204 or not response.content:
        return None
    response.raise_for_status()
    return response.json()


def _resume(device_id: str) -> None:
    # /me/player/play with no body picks up whatever was loaded, instead of
    # replacing the queue.
    response = httpx.put(
        f"{_API}/me/player/play", params={"device_id": device_id}, headers=_headers(), timeout=10.0
    )
    response.raise_for_status()


def _play_uris(uris: list[str], device_id: str) -> None:
    # A list, not a single uri: with one song and no queue behind it,
    # "siguiente" has nowhere to go (tested live: control_playback with
    # action "next" did nothing because there was no next track).
    response = httpx.put(
        f"{_API}/me/player/play",
        params={"device_id": device_id},
        headers=_headers(),
        json={"uris": uris},
        timeout=10.0,
    )
    response.raise_for_status()


def _random_favorites(count: int) -> list[dict]:
    """Up to `count` tracks from "Tus me gusta", from a random window,
    already shuffled. Empty list if there are no favorites or the read
    fails."""
    try:
        response = httpx.get(
            f"{_API}/me/tracks", params={"limit": 1}, headers=_headers(), timeout=10.0
        )
        response.raise_for_status()
        total = response.json().get("total", 0)
        if total == 0:
            return []
        size = min(count, total)
        offset = random.randint(0, max(0, total - size))
        response = httpx.get(
            f"{_API}/me/tracks",
            params={"limit": size, "offset": offset},
            headers=_headers(),
            timeout=10.0,
        )
        response.raise_for_status()
        tracks = [item["track"] for item in response.json()["items"]]
        random.shuffle(tracks)
        return tracks
    except Exception:
        return []


@tool
def play_music(query: str) -> str:
    """Searches for a song, album or artist and plays it on Spotify.

    query: free text, e.g. "Metallica black album" or "Bad Bunny".
    """
    # "Poné X" is ambiguous between a song and a YouTube channel, and that
    # ambiguity cannot be resolved well in the prompt: the channels the
    # user watches are dynamic data (they grow with use, see
    # tools/_youtube_favorites.py), not something that can be listed in
    # fixed text. It is resolved here, with the real data: if X is already
    # a known channel, that wins over searching for it as a song -- it
    # avoids "Poné Vorterix" ending up playing whatever Spotify thinks
    # sounds similar instead of opening the actual channel (happened live).
    channel_result = _youtube_favorites.open_if_known(query)
    if channel_result is not None:
        return channel_result

    track = _search_track(query)
    if track is None:
        return f"La herramienta 'play_music' falló: no encontré ninguna canción para {query!r} en Spotify."

    device_id = _ensure_device()
    if device_id is None:
        return "La herramienta 'play_music' falló: no hay ningún dispositivo de Spotify activo, ni abriendo la app."

    # A few random favorites are queued after this song -- without that,
    # "siguiente" had nowhere to go (a single loose song is not a real
    # queue).
    queue = [track["uri"]] + [
        t["uri"] for t in _random_favorites(10) if t["uri"] != track["uri"]
    ]
    _youtube_screen.pause()  # so the two do not play at once
    _play_uris(queue, device_id)
    artist = track["artists"][0]["name"] if track["artists"] else "?"
    return f"Reproduciendo {track['name']!r} de {artist}."


@tool
def play_random_music() -> str:
    """Puts music on Spotify. Use for generic music requests that do NOT
    name an artist/song/album (e.g. "poné música", "poné algo", "poné
    alguna canción"). Opens Spotify if it is closed. If something was
    paused, it resumes that; if nothing was loaded, it plays random songs
    from the user's "Tus me gusta" (favorites)."""
    device_id = _ensure_device()
    if device_id is None:
        return "La herramienta 'play_random_music' falló: no hay ningún dispositivo de Spotify activo, ni abriendo la app."

    # "Poné música" with Spotify open and paused means "hit play", not
    # "swap what I was listening to for something else".
    player = _player_state()
    item = (player or {}).get("item")
    if player and player.get("is_playing"):
        return "Ya estaba sonando música, no cambié nada."
    if item:
        _youtube_screen.pause()
        _resume(device_id)
        artist = item["artists"][0]["name"] if item.get("artists") else "?"
        return f"Reanudando {item['name']!r} de {artist}."

    tracks = _random_favorites(15)
    if not tracks:
        return "La herramienta 'play_random_music' falló: no tenés canciones en 'Tus me gusta' en Spotify."

    _youtube_screen.pause()
    _play_uris([t["uri"] for t in tracks], device_id)
    first = tracks[0]
    artist = first["artists"][0]["name"] if first["artists"] else "?"
    return f"Reproduciendo {first['name']!r} de {artist} (de tus favoritos)."


# action -> (media action for the platform, what Tero says). The action
# names happen to match one to one; the dict stays because it also holds
# the Spanish confirmation and validates the action (an unknown one raises
# KeyError, which tools.execute turns into an error message).
_ACTIONS = {
    "play": ("play", "Listo, reproduciendo."),
    "pause": ("pause", "Listo, pausado."),
    "next": ("next", "Listo, siguiente."),
    "previous": ("previous", "Listo, anterior."),
}


def pause_spotify() -> None:
    """Pauses Spotify specifically (fixed MPRIS player name, "spotify") --
    for when a YouTube channel starts (see tools/youtube.py) and music must
    not play at the same time. Unlike control_playback, which deliberately
    does not target any particular player (it grabs "the first available
    one", whichever that is), here Spotify specifically is what matters --
    with the YouTube window also registered as an MPRIS player, leaving this
    untargeted could end up pausing YouTube itself instead of Spotify.
    Silent if Spotify is not running."""
    get_platform().pause_player("spotify")


@tool
def control_playback(action: Literal["play", "pause", "next", "previous"]) -> str:
    """Controls current media playback (play, pause, next, previous).

    Through MPRIS (playerctl): works with Spotify, browsers and most modern
    players on Linux, regardless of which one is playing. Requires
    `playerctl` installed.
    """
    command, done = _ACTIONS[action]
    platform = get_platform()
    try:
        platform.media(command)
        return done
    except RuntimeError as error:
        failure = error
    if action == "play":
        # No player took it, probably because Spotify is not even open. It
        # gets opened (same as in play_music) and retried.
        _open_spotify()
        for wait in (2, 2, 3, 3):
            time.sleep(wait)
            try:
                platform.media(command)
                return done
            except RuntimeError as error:
                failure = error
    return f"La herramienta 'control_playback' falló: {failure}."
