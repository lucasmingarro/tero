"""Platform implementation for Linux.

The binaries used here: `wpctl` (WirePlumber) for the volume, `playerctl`
(MPRIS) for playback, `xdg-open` to hand a URI to the desktop,
`notify-send` for notifications, `nvidia-smi` for the GPU. The key comes
from `/dev/input` through evdev, and the available RAM from
`/proc/meminfo`.
"""

import selectors
import subprocess
import threading
from typing import Callable

from evdev import InputDevice, ecodes, list_devices

from os_platform.base import Platform


_POINTER_AXES = {ecodes.REL_X, ecodes.REL_Y}

# The default output of the system. The one that always exists (unlike an
# individual application's stream, which appears and disappears) -- see the
# note at the top of tools/_ducking.py.
_SINK = "@DEFAULT_AUDIO_SINK@"


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
        result = subprocess.run(
            ["playerctl", action], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            # "No player could handle this command": there is no player with
            # an active MPRIS session, probably because Spotify is not even
            # open. What the caller does about it (open it and retry) is not
            # this layer's business.
            raise RuntimeError(result.stderr.strip() or "sin reproductor activo")

    def pause_player(self, name: str) -> None:
        # A fixed MPRIS player name, unlike media(): with the YouTube window
        # also registered as an MPRIS player, an untargeted pause could end
        # up pausing YouTube instead of the player the caller meant.
        subprocess.run(["playerctl", "-p", name, "pause"], capture_output=True, check=False)

    def notify(self, text: str) -> None:
        subprocess.run(["notify-send", "Tero", text], check=False)

    def master_volume(self) -> float | None:
        result = subprocess.run(
            ["wpctl", "get-volume", _SINK], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            return None
        for part in result.stdout.split():
            try:
                return float(part)
            except ValueError:
                continue
        return None

    def set_master_volume(self, value: float) -> None:
        subprocess.run(
            ["wpctl", "set-volume", _SINK, f"{max(0.0, min(1.0, value)):.3f}"],
            capture_output=True,
            check=False,
        )

    def adjust_master_volume(self, delta_percent: int) -> None:
        sign = "+" if delta_percent >= 0 else "-"
        subprocess.run(
            ["wpctl", "set-volume", _SINK, f"{abs(delta_percent)}%{sign}"], check=False
        )

    def set_mute(self, muted: bool) -> None:
        subprocess.run(["wpctl", "set-mute", _SINK, "1" if muted else "0"], check=False)

    def open_uri(self, uri: str) -> None:
        # Whatever gets opened inherits the file descriptors of whoever
        # opened it: without DEVNULL, Spotify writes its GTK warnings into
        # logs/tero.log, and with start_new_session it does not hang off the
        # daemon process.
        subprocess.Popen(
            ["xdg-open", uri],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def available_ram_mb(self) -> float | None:
        # MemAvailable (not "free"): it is the kernel's estimate of how much
        # can be requested without starting to swap, which is the number
        # that matters to health.py.
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / 1024
        except Exception:
            return None
        return None

    def gpu_status(self) -> dict | None:
        """Temperature, free VRAM and whether the card is throttling itself.

        `clocks_throttle_reasons.hw_thermal_slowdown` is a better signal than
        comparing the temperature against a fixed number: it is the card
        saying "I am at my limit", with this card's real limit, without
        having to guess a threshold per model.
        """
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=temperature.gpu,memory.total,memory.used,"
                    "clocks_throttle_reasons.hw_thermal_slowdown",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
            if result.returncode != 0:
                return None
            temp, total, used, slowdown = (p.strip() for p in result.stdout.split(","))
            return {
                "temperature_c": float(temp),
                "free_vram_mb": float(total) - float(used),
                "thermal_slowdown": slowdown.lower() == "active",
            }
        except Exception:
            # With no nvidia-smi (a machine without an NVIDIA GPU) there is
            # nothing to watch here; the RAM watch keeps working anyway.
            return None

