"""Platform interface: everything that depends on the operating system goes
through here.

The rest of the program never imports anything from Windows/Linux directly,
it only instantiates the right implementation and uses this interface.
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
        """Sends a media control action: play_pause, next, previous."""

    @abstractmethod
    def notify(self, text: str) -> None:
        """Shows a desktop notification."""
