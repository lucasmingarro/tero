"""Platform implementation for Linux."""

import selectors
import subprocess
import threading
from typing import Callable

from evdev import InputDevice, ecodes, list_devices

from os_platform.base import Platform


_POINTER_AXES = {ecodes.REL_X, ecodes.REL_Y}


def _is_keyboard(device: InputDevice) -> bool:
    """Filters out mice and gamepads: a real keyboard has alphabetic keys and
    never reports pointer movement (REL_X/REL_Y). Excluding anything with
    EV_REL is not enough: several combo receivers (Logitech, Corsair) expose
    the scroll wheel (REL_HWHEEL) on the keyboard interface itself without
    being a mouse. A mouse with programmable buttons remapped to keys (e.g.
    the G903) still has REL_X/REL_Y because it is, in fact, a mouse."""
    capabilities = device.capabilities()
    keys = capabilities.get(ecodes.EV_KEY, [])
    has_alphabet = ecodes.KEY_A in keys and ecodes.KEY_Z in keys
    rel_axes = set(capabilities.get(ecodes.EV_REL, []))
    is_pointer = bool(rel_axes & _POINTER_AXES)
    return has_alphabet and not is_pointer


def _keyboards() -> list[InputDevice]:
    devices = []
    for path in list_devices():
        try:
            device = InputDevice(path)
        except OSError:
            continue
        if _is_keyboard(device):
            devices.append(device)
    return devices


class LinuxPlatform(Platform):
    def __init__(self, key: str = "KEY_PAUSE"):
        self._key_code = getattr(ecodes, key)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def listen_key(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._key_loop, args=(on_down, on_up), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _key_loop(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        devices = _keyboards()
        if not devices:
            raise RuntimeError(
                "No se encontró ningún teclado en /dev/input. "
                "¿El usuario está en el grupo 'input'? "
                "(sudo usermod -aG input $USER, después reloguear)"
            )
        selector = selectors.DefaultSelector()
        for device in devices:
            selector.register(device, selectors.EVENT_READ)
        try:
            while not self._stop.is_set():
                for key, _ in selector.select(timeout=0.2):
                    device = key.fileobj
                    for event in device.read():
                        if event.type != ecodes.EV_KEY or event.code != self._key_code:
                            continue
                        if event.value == 1:  # key pressed
                            on_down()
                        elif event.value == 0:  # key released
                            on_up()
        finally:
            for device in devices:
                selector.unregister(device)
                device.close()

    def active_window(self) -> dict:
        # Phase 3. On GNOME/Wayland there is no standard API without a
        # gnome-shell extension exposing this over D-Bus.
        raise NotImplementedError("active_window llega en la fase 3 (Contexto)")

    def capture_screen(self) -> bytes:
        # Phase 3.
        raise NotImplementedError("capture_screen llega en la fase 3 (Contexto)")

    def media(self, action: str) -> None:
        # Phase 2, the control_playback tool. Requires playerctl installed.
        raise NotImplementedError("media llega en la fase 2 (Cerebro)")

    def notify(self, text: str) -> None:
        subprocess.run(["notify-send", "Tero", text], check=False)
