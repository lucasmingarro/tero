"""YouTube channels Tero learns by use, instead of a fixed list in code.

Why: the first version of this tool had a `Literal[...]` with seven fixed
channels -- the user flagged it as an antipattern, rightly so: asking for a
channel that is not on that list is not even a valid call for the model
(the enum rejects it), so it generalizes to nothing new. The fix is not
growing the list by hand every time -- it is having no fixed list.
`play_youtube_channel` (tools/youtube.py) takes free text; this memory is
what keeps "poné Olga" fast and accurate without a closed list: it is
looked up here first (no network, by phonetic similarity) and only goes out
to search YouTube live if nothing is close enough -- the same pattern
`play_music` already uses against Spotify (free search, not a catalog).

It learns on its own: every time `play_youtube_channel` resolves a channel
(whether it was already here or it found it by searching), it stores it or
adds one more hit -- so "Vorter" or a broken transcription like "Borteroc"
point at Vorterix next time, if it is already a known channel, instead of
being treated as a new channel every time. Stored in
`~/.config/tero/youtube_channels.json` (the same pattern as the
soul-connector position or the Spotify token).
"""

import difflib
import json
import re
import unicodedata
from pathlib import Path

import httpx

from tools import _youtube_screen

_PATH = Path.home() / ".config" / "tero" / "youtube_channels.json"

# A starting point, not a ceiling: the seven channels the user had already
# asked for repeatedly as of 2026-09-14 (resolved by hand through <link
# rel="canonical">, see BITACORA.html). The memory grows on its own from
# here.
_SEED = {
    "olga": {"handle": "olgaenvivo_", "id": "UC7mJ2EDXFomeDIRFu5FtEbA", "name": "Olga", "hits": 1},
    "mitre": {"handle": "Radiomitre", "id": "UCYvINPByAdCcpA0sWrF3I_w", "name": "Radio Mitre", "hits": 1},
    "urbana play": {"handle": "UrbanaPlayFM", "id": "UCC1kfsMJko54AqxtcFECt-A", "name": "Urbana Play", "hits": 1},
    "paren la mano": {"handle": "Parenlamano", "id": "UCulzKEqyE73gXCUqTimbP4A", "name": "Parén la Mano", "hits": 1},
    "aislados": {"handle": "AisladosElPodcast", "id": "UCXjNLHGKB83V7FxRvEeWJ5A", "name": "Aislados", "hits": 1},
    "vorterix": {"handle": "VorterixOficial", "id": "UCvCTWHCbBC0b9UIeLeNs8ug", "name": "Vorterix", "hits": 1},
    "midu": {"handle": "midudev", "id": "UC8LeXCWOalN8SxlrPcG-PaQ", "name": "Midu", "hits": 1},
}

# Measured live against the real transcriptions that motivated this
# ("Bortegui"/"Portelix"/"Bordelix" for "Vorterix" scored 0.62-0.75;
# unrelated words like "Olga" against "Vorterix" score ~0.15-0.17) --
# 0.55 cleanly separates a bad transcription from a genuinely different
# channel.
_SIMILARITY_THRESHOLD = 0.55


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", text).strip()


def _load() -> dict:
    if not _PATH.exists():
        return dict(_SEED)
    try:
        return json.loads(_PATH.read_text())
    except Exception:
        return dict(_SEED)


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def find_learned(text: str) -> dict | None:
    """The known channel closest to `text` (by name, not id), or None if
    none comes close enough -- that is when play_youtube_channel has to go
    search live.

    It deliberately compares the whole phrase, not word by word: splitting
    into words to tolerate filler ("Bueno, Bortelix" instead of just
    "Bortelix") was tried and dropped -- "hola" against "olga" scores 0.75
    (same four letters), identical to "bortelix" against "vorterix", so
    that path opened Olga's channel every time someone said "hola, ¿cómo
    estás?". With plain text thresholds, more permissive is not more
    general, it is more fragile."""
    goal = _normalize(text)
    best, best_ratio = None, 0.0
    for key, info in _load().items():
        ratio = difflib.SequenceMatcher(None, goal, key).ratio()
        if ratio > best_ratio:
            best, best_ratio = info, ratio
    return best if best_ratio >= _SIMILARITY_THRESHOLD else None


def open_if_known(text: str) -> str | None:
    """If `text` looks like an already learned channel, opens it and
    returns the confirmation phrase; None if nothing is close (doing
    nothing in that case). A single place for this logic -- used both by
    `play_music` (tools/music.py, "poné X" is ambiguous between song and
    channel) and by `brain/router.py` (a safety net for when the
    transcription came out so badly that not even the verb of the request
    survived -- see BITACORA.html, 2026-09-14: asking the model to
    reconstruct the verb through the prompt destabilized tool calling, so
    this is solved in code, not in the prompt)."""
    channel = find_learned(text)
    if channel is None:
        return None
    remember(channel["handle"], channel["id"], channel["name"])
    _youtube_screen.show(f"https://www.youtube.com/@{channel['handle']}/live")
    return f"Abrí {channel['name']} en vivo."


def remember(handle: str, channel_id: str, name: str) -> None:
    """Stores or reinforces a channel -- called every time
    play_youtube_channel opens something, both when it was already here
    (to keep the hit count) and when it was just resolved by searching.

    Keyed by `name` (the real channel name on YouTube), NEVER by what the
    user said this time -- if it were keyed by the transcribed text, every
    different bad transcription of "Vorterix" ("Bortegui", "Portelix",
    "Bordelix"...) would create a new entry instead of reinforcing the same
    one (a real bug, found while testing this very thing: four entries for
    a single channel, each with hits=1)."""
    data = _load()
    key = _normalize(name)
    previous_hits = data.get(key, {}).get("hits", 0)
    data[key] = {"handle": handle, "id": channel_id, "name": name, "hits": previous_hits + 1}
    _save(data)


def all_channels() -> list[dict]:
    """For suggest_youtube_channels: everything learned so far."""
    return list(_load().values())


def resolve_by_search(text: str) -> dict | None:
    """Searches `text` on YouTube and returns the first real channel it
    finds, or None if there is nothing -- with no API key: the results page
    filtered to channels is scraped (the `sp=EgIQAg==` parameter, the same
    one YouTube's real search applies for the "Channel" filter). This is
    only reached when find_learned() found nothing close, so it is
    acceptable for it to take a bit longer (one request)."""
    try:
        response = httpx.get(
            "https://www.youtube.com/results",
            params={"search_query": text, "sp": "EgIQAg=="},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        response.raise_for_status()
    except Exception:
        return None
    match = re.search(
        r'"channelRenderer":\{"channelId":"([^"]+)".*?'
        r'"title":\{"simpleText":"([^"]+)".*?'
        r'"canonicalBaseUrl":"/@([^"]+)"',
        response.text,
    )
    if not match:
        return None
    channel_id, name, handle = match.groups()
    return {"id": channel_id, "name": name, "handle": handle}
