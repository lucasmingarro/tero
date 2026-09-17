"""System volume control via wpctl (PipeWire)."""

import subprocess
from typing import Literal

from tools import tool

_SINK = "@DEFAULT_AUDIO_SINK@"


@tool
def set_volume(action: Literal["up", "down", "mute", "unmute"], percent: int = 10) -> str:
    """Turns the system volume up or down, mutes it or unmutes it. This is
    only about the volume level -- to start or pause music use
    control_playback or play_music, not this.

    percent: how much to go up or down (only applies to up/down).
    """
    if action == "up":
        subprocess.run(["wpctl", "set-volume", _SINK, f"{percent}%+"], check=False)
        return f"Subí el volumen {percent}%."
    if action == "down":
        subprocess.run(["wpctl", "set-volume", _SINK, f"{percent}%-"], check=False)
        return f"Bajé el volumen {percent}%."
    if action == "mute":
        subprocess.run(["wpctl", "set-mute", _SINK, "1"], check=False)
        return "Silencié el volumen."
    if action == "unmute":
        subprocess.run(["wpctl", "set-mute", _SINK, "0"], check=False)
        return "Reactivé el volumen."
    return f"No entendí la acción de volumen {action!r}."
