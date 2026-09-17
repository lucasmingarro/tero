"""Platform implementation for macOS.

Two mechanisms on purpose, and the reason is measured, not stylistic (all
the numbers are from this machine on 2026-09-17, see the "Medido en macOS"
section of docs/PLAN-MULTIPLATAFORMA.md):

- **Volume: CoreAudio through ctypes.** `osascript` costs ~450 ms per
  volume call (43 ms of that is starting the AppleScript interpreter, the
  rest is the volume operation itself), and the ducking ramp in
  tools/_ducking.py writes the volume on every step -- at that price the
  fade would take seconds and the key is only held for one or two.
  CoreAudio reads in 0.25 ms and writes with a median of 4 ms. It adds no
  dependency: `ctypes` is stdlib and CoreAudio.framework is part of the
  system, no pyobjc involved.
- **Everything else: `osascript`.** Notifications ~128 ms, Spotify
  playpause ~193 ms. One call per turn, nowhere near the ramp, and
  AppleScript is by far the shortest way to say it.

The activation key comes from pynput, which needs the Input Monitoring
permission -- see `_explain_input_monitoring`, it is the first thing that
goes wrong on a fresh machine.
"""

import ctypes
import ctypes.util
import os
import subprocess
import sys
import threading
import time
from typing import Callable

import psutil
from pynput import keyboard

from os_platform.base import Platform

# --- CoreAudio (volume) ---------------------------------------------------

_coreaudio = ctypes.CDLL(
    ctypes.util.find_library("CoreAudio")
    or "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
)
# Explicit types: AudioObjectHasProperty returns a 1-byte Boolean and the
# other two an OSStatus (0 = ok). Without this, ctypes assumes int and can
# read whatever is left in the upper bytes of the register.
_coreaudio.AudioObjectHasProperty.restype = ctypes.c_ubyte
_coreaudio.AudioObjectGetPropertyData.restype = ctypes.c_int32
_coreaudio.AudioObjectSetPropertyData.restype = ctypes.c_int32


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("selector", ctypes.c_uint32),
        ("scope", ctypes.c_uint32),
        ("element", ctypes.c_uint32),
    ]


def _four_char_code(code: str) -> int:
    """CoreAudio selectors are four ASCII characters packed into a uint32."""
    return int.from_bytes(code.encode(), "big")


_SYSTEM_OBJECT = 1
_DEFAULT_OUTPUT_DEVICE = _four_char_code("dOut")
_SCOPE_GLOBAL = _four_char_code("glob")
_SCOPE_OUTPUT = _four_char_code("outp")
_VOLUME_SCALAR = _four_char_code("volm")
_MUTE = _four_char_code("mute")
# Element 0 is the device as a whole ("main"); 1 is the first channel, which
# is where devices that do not expose a main volume keep theirs.
_ELEMENTS = (0, 1)


def _default_output_device() -> int | None:
    device = ctypes.c_uint32(0)
    size = ctypes.c_uint32(4)
    address = _PropertyAddress(_DEFAULT_OUTPUT_DEVICE, _SCOPE_GLOBAL, 0)
    status = _coreaudio.AudioObjectGetPropertyData(
        ctypes.c_uint32(_SYSTEM_OBJECT),
        ctypes.byref(address),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(device),
    )
    return device.value if status == 0 else None


def _element_with(device: int, selector: int) -> int | None:
    """First element of `device` that exposes `selector`, or None if none
    does -- which is the normal case for volume over HDMI/DisplayPort and
    for some AirPlay outputs: the volume lives in the other device."""
    for element in _ELEMENTS:
        address = _PropertyAddress(selector, _SCOPE_OUTPUT, element)
        if _coreaudio.AudioObjectHasProperty(ctypes.c_uint32(device), ctypes.byref(address)):
            return element
    return None


# --- Input Monitoring permission ------------------------------------------

_coregraphics = ctypes.CDLL(
    ctypes.util.find_library("CoreGraphics")
    or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
)
_coregraphics.CGPreflightListenEventAccess.restype = ctypes.c_ubyte
_coregraphics.CGRequestListenEventAccess.restype = ctypes.c_ubyte


def _applescript_string(text: str) -> str:
    """Escapes `text` so it can go inside a double quoted AppleScript
    literal (a stray quote in a transcription would otherwise break the
    script, or worse, change it)."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


# action (the same names control_playback uses) -> AppleScript verb.
# `play`/`pause` instead of `playpause`: the caller already decided which
# one it wants, and a toggle would do the opposite half the time.
_MEDIA_VERBS = {
    "play": "play",
    "pause": "pause",
    "next": "next track",
    "previous": "previous track",
}


class MacOSPlatform(Platform):
    # ~4 ms median per volume write, with spikes of up to 450 ms: the ramp
    # is stepped more slowly than on Linux (0.02) so a slow write does not
    # leave the fade permanently behind.
    volume_step_s = 0.05

    def __init__(self, key: str = "ctrl_r"):
        self._key = self._parse_key(key)
        self._key_name = key
        # macOS repeats key-press events while a key is held down; this is
        # what turns them into a single on_down, like the evdev value==1.
        self._held = False
        self._listener: keyboard.Listener | None = None
        # (device id, volume element) of the current ducking cycle. Resolved
        # on every master_volume() -- i.e. once per key press, when the
        # Ducker reads the real volume -- and not cached at startup, so
        # switching from speakers to headphones between two requests lands
        # on the new device by itself.
        self._output: tuple[int, int] | None = None
        self._warned: set[str] = set()

    @staticmethod
    def _parse_key(name: str) -> keyboard.Key | keyboard.KeyCode:
        key = getattr(keyboard.Key, name, None)
        if key is not None:
            return key
        if len(name) == 1:
            return keyboard.KeyCode.from_char(name)
        raise RuntimeError(
            f"No existe la tecla {name!r} en pynput. Para ver los nombres válidos: "
            'uv run python -c "from pynput.keyboard import Key; '
            'print(sorted(k.name for k in Key))"'
        )

    def _warn_once(self, text: str) -> None:
        """Logs `text` the first time only: these are conditions that repeat
        on every key press (an output with no volume control, for one) and
        the log would be nothing but this."""
        if text in self._warned:
            return
        self._warned.add(text)
        print(f"(aviso: {text})")

    # --- key --------------------------------------------------------------

    def listen_key(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        if not _coregraphics.CGPreflightListenEventAccess():
            self._explain_input_monitoring()
            # Asking is what makes macOS show the dialog and list this
            # program under Input Monitoring; without it the user has to
            # find and add the binary by hand.
            _coregraphics.CGRequestListenEventAccess()
        self._listener = keyboard.Listener(
            on_press=lambda key: self._on_press(key, on_down),
            on_release=lambda key: self._on_release(key, on_up),
        )
        # pynput's Listener is a daemon thread itself: start() returns right
        # away and the key loop lives in there, same as the evdev thread on
        # Linux.
        self._listener.start()
        threading.Thread(target=self._watch_listener, daemon=True).start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()

    def _on_press(self, key, on_down: Callable[[], None]) -> None:
        if key != self._key or self._held:
            return
        self._held = True
        on_down()

    def _on_release(self, key, on_up: Callable[[], None]) -> None:
        if key != self._key or not self._held:
            return
        self._held = False
        on_up()

    def _watch_listener(self) -> None:
        # Without the permission, the event tap cannot be created and
        # pynput's thread ends on its own instead of raising here. Nothing
        # else would notice: Tero would print "escuchando" and never react
        # to the key.
        time.sleep(2.0)
        if self._listener is not None and not self._listener.running:
            print(f"El teclado no se está escuchando: se cerró el listener de {self._key_name}.")
            self._explain_input_monitoring()

    def _explain_input_monitoring(self) -> None:
        real = os.path.realpath(sys.executable)
        print(
            "Falta el permiso de Monitoreo de entrada: sin eso la tecla de Tero no se "
            "escucha nunca.\n"
            "  Activalo en Ajustes del Sistema > Privacidad y seguridad > Monitoreo de "
            "entrada.\n"
            "  El permiso se le da al programa que lanza Tero: si arrancás con ./tero, "
            "es la terminal que estás usando; si lo lanza launchd, es este python:\n"
            f"    {sys.executable}\n"
            f"    (que es en realidad {real})\n"
            "  Después de darlo hay que volver a arrancar Tero."
        )

    # --- volume (CoreAudio) -----------------------------------------------

    def _resolve_output(self) -> tuple[int, int] | None:
        device = _default_output_device()
        if device is None:
            self._warn_once("CoreAudio no devolvió el dispositivo de salida por defecto")
            return None
        element = _element_with(device, _VOLUME_SCALAR)
        if element is None:
            self._warn_once(
                "la salida de audio actual no tiene control de volumen (pasa con "
                "HDMI/DisplayPort y algunos AirPlay): no puedo bajar la música mientras "
                "escucho, ni subir o bajar el volumen por voz"
            )
            return None
        return device, element

    def master_volume(self) -> float | None:
        """Volume of the default output, 0.0-1.0. 0.25 ms measured.

        Resolves the output device on every call, which is once per ducking
        cycle (the Ducker reads the real volume when the key goes down):
        that is what makes plugging headphones in between two requests take
        effect on the next one.
        """
        output = self._resolve_output()
        self._output = output
        if output is None:
            return None
        device, element = output
        value = ctypes.c_float(0.0)
        size = ctypes.c_uint32(4)
        address = _PropertyAddress(_VOLUME_SCALAR, _SCOPE_OUTPUT, element)
        status = _coreaudio.AudioObjectGetPropertyData(
            ctypes.c_uint32(device),
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(value),
        )
        if status != 0:
            self._warn_once(f"CoreAudio no pudo leer el volumen (status {status})")
            return None
        return value.value

    def set_master_volume(self, value: float) -> None:
        """Sets the volume of the default output, 0.0-1.0. Median 4 ms, with
        spikes of 130-450 ms when the system decides to persist the change
        -- which is why volume_step_s is 0.05 here (see the class attribute)
        and why the Ducker's ramp works off its own estimate instead of
        re-reading the volume on every step."""
        output = self._output or self._resolve_output()
        if output is None:
            return
        device, element = output
        volume = ctypes.c_float(max(0.0, min(1.0, value)))
        address = _PropertyAddress(_VOLUME_SCALAR, _SCOPE_OUTPUT, element)
        status = _coreaudio.AudioObjectSetPropertyData(
            ctypes.c_uint32(device), ctypes.byref(address), 0, None, ctypes.c_uint32(4),
            ctypes.byref(volume),
        )
        if status != 0:
            self._warn_once(f"CoreAudio no pudo cambiar el volumen (status {status})")

    def adjust_master_volume(self, delta_percent: int) -> None:
        current = self.master_volume()
        if current is None:
            return
        self.set_master_volume(current + delta_percent / 100)

    def set_mute(self, muted: bool) -> None:
        """Real mute (kAudioDevicePropertyMute) or nothing: taking the
        volume down to 0 would look the same on this turn and then lose the
        original volume for good on the next one."""
        device = _default_output_device()
        if device is None:
            self._warn_once("CoreAudio no devolvió el dispositivo de salida por defecto")
            return
        element = _element_with(device, _MUTE)
        if element is None:
            self._warn_once("la salida de audio actual no se puede silenciar (no expone mute)")
            return
        address = _PropertyAddress(_MUTE, _SCOPE_OUTPUT, element)
        value = ctypes.c_uint32(1 if muted else 0)
        status = _coreaudio.AudioObjectSetPropertyData(
            ctypes.c_uint32(device), ctypes.byref(address), 0, None, ctypes.c_uint32(4),
            ctypes.byref(value),
        )
        if status != 0:
            self._warn_once(f"CoreAudio no pudo cambiar el mute (status {status})")

    # --- desktop (osascript) ----------------------------------------------

    def notify(self, text: str) -> None:
        """Desktop notification. ~128 ms measured (average of 10)."""
        script = f'display notification "{_applescript_string(text)}" with title "Tero"'
        subprocess.run(["osascript", "-e", script], capture_output=True, check=False)

    def media(self, action: str) -> None:
        """Playback control, aimed at Spotify. ~193 ms measured (average of
        10 playpause).

        Not the same reach as the Linux one: MPRIS has no equivalent here,
        so this talks to Spotify specifically instead of "whichever player
        is playing". AppleScript opens Spotify if it is closed, so the
        caller's retry loop rarely comes into play.
        """
        verb = _MEDIA_VERBS.get(action)
        if verb is None:
            raise RuntimeError(f"no sé hacer {action!r} en la música")
        result = subprocess.run(
            ["osascript", "-e", f'tell application "Spotify" to {verb}'],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            # The osascript error is in English and full of AppleScript
            # noise: it goes to the log, and what Tero says stays Spanish.
            print(f"(osascript falló: {result.stderr.strip()})")
            raise RuntimeError("no pude controlar Spotify")

    def pause_player(self, name: str) -> None:
        if name != "spotify":
            self._warn_once(f"en macOS solo sé pausar Spotify, no {name!r}")
            return
        subprocess.run(
            ["osascript", "-e", 'tell application "Spotify" to pause'],
            capture_output=True,
            check=False,
        )

    def open_uri(self, uri: str) -> None:
        # Same care as on Linux: without DEVNULL whatever opens writes its
        # own noise into logs/tero.log, and with start_new_session it does
        # not hang off the daemon process.
        subprocess.Popen(
            ["open", uri],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # --- machine ----------------------------------------------------------

    def available_ram_mb(self) -> float | None:
        # psutil's "available" on macOS is what can be asked for without
        # pushing the machine into swap, the equivalent of MemAvailable on
        # Linux -- which is the number health.py wants.
        try:
            return psutil.virtual_memory().available / (1024 * 1024)
        except Exception:
            return None

    def gpu_status(self) -> dict | None:
        # There is nothing to watch here: no nvidia-smi, and on Apple
        # Silicon the GPU shares the machine's memory, so the RAM check in
        # health.py already covers the only pressure that matters. health.py
        # takes None as "no GPU to look after".
        return None

    def active_window(self) -> dict:
        # Phase 3, same as Linux. Here it would go through Accessibility
        # (AXUIElement), with the permission that implies.
        raise NotImplementedError("active_window llega en la fase 3 (Contexto)")

    def capture_screen(self) -> bytes:
        # Phase 3. `screencapture` is the way in.
        raise NotImplementedError("capture_screen llega en la fase 3 (Contexto)")
