"""System volume control, through the platform layer."""

from typing import Literal

from os_platform import get_platform
from tools import tool

SUPPORTED_PLATFORMS = {"linux", "darwin"}


@tool
def set_volume(action: Literal["up", "down", "mute", "unmute"], percent: int = 10) -> str:
    """Turns the system volume up or down, mutes it or unmutes it. This is
    only about the volume level -- to start or pause music use
    control_playback or play_music, not this.

    percent: how much to go up or down (only applies to up/down).
    """
    platform = get_platform()
    if action == "up":
        platform.adjust_master_volume(percent)
        return f"Subí el volumen {percent}%."
    if action == "down":
        platform.adjust_master_volume(-percent)
        return f"Bajé el volumen {percent}%."
    if action == "mute":
        platform.set_mute(True)
        return "Silencié el volumen."
    if action == "unmute":
        platform.set_mute(False)
        return "Reactivé el volumen."
    return f"No entendí la acción de volumen {action!r}."
