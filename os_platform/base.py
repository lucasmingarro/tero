"""Platform interface: everything that depends on the operating system goes
through here.

The rest of the program never imports anything from Windows/Linux directly,
it only instantiates the right implementation and uses this interface. In
particular, no tool runs an OS-specific binary itself: it asks the platform
for the capability (volume, media keys, notifications) and each
implementation decides how to get it.
"""

from abc import ABC, abstractmethod
from typing import Callable


class Platform(ABC):
    @abstractmethod
    def listen_key(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        """Starts a background listener for the activation key.

        Calls on_down when it is pressed and on_up when it is released.
        Does not block: it runs on its own thread.
        """

    @abstractmethod
    def active_window(self) -> dict:
        """Info about the focused window: {"title": str, "app": str}."""

    @abstractmethod
    def capture_screen(self) -> bytes:
        """Captures the current screen and returns PNG bytes."""

    @abstractmethod
    def media(self, action: str) -> None:
        """Sends a media control action: play, pause, next, previous.

        Not aimed at any particular player: whichever one is playing.
        Raises RuntimeError if no player took the action -- the message is
        user-facing (Spanish), the caller puts it in what Tero says.
        """

    @abstractmethod
    def pause_player(self, name: str) -> None:
        """Pauses one specific player by name (e.g. "spotify"), leaving any
        other one alone. Silent if that player is not running."""

    @abstractmethod
    def notify(self, text: str) -> None:
        """Shows a desktop notification."""

    @abstractmethod
    def master_volume(self) -> float | None:
        """Current volume of the default output, 0.0-1.0 (None if it cannot
        be read)."""

    @abstractmethod
    def set_master_volume(self, value: float) -> None:
        """Sets the volume of the default output, 0.0-1.0 (clamped)."""

    @abstractmethod
    def adjust_master_volume(self, delta_percent: int) -> None:
        """Moves the volume of the default output by a relative percentage:
        positive goes up, negative goes down."""

    @abstractmethod
    def set_mute(self, muted: bool) -> None:
        """Mutes or unmutes the default output."""

    @abstractmethod
    def open_uri(self, uri: str) -> None:
        """Hands a URI to the desktop so it opens whatever handles it (e.g.
        "spotify:"). Does not block and does not inherit this process'
        output.
        """

    @abstractmethod
    def available_ram_mb(self) -> float | None:
        """RAM that can be asked for without the machine starting to swap,
        in MB (None if it cannot be read)."""

    @abstractmethod
    def gpu_status(self) -> dict | None:
        """{"temperature_c": float, "free_vram_mb": float,
        "thermal_slowdown": bool}, or None if there is nothing to watch on
        this machine."""
