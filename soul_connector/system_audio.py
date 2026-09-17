"""Listens to the system audio (whatever comes out of the active sink,
through its PipeWire monitor) so the soul-connector reacts to what is
actually playing on the machine -- music from Spotify, a video, whatever --
not only when Tero speaks.

The default sink can change (the user switches from headphones to HDMI,
etc.), so it is re-detected every time the stream drops instead of assuming
a fixed one.
"""

import subprocess
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

_WINDOW_S = 0.05
_SILENCE_THRESHOLD = 0.003  # below this, nothing is considered to be playing


def _default_sink_name() -> str | None:
    result = subprocess.run(
        ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "node.name" in line:
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _monitor_index(sink_name: str) -> int | None:
    target = f"{sink_name}.monitor"
    for i, device in enumerate(sd.query_devices()):
        if device["name"] == target and device["max_input_channels"] > 0:
            return i
    return None


class SystemAudioMonitor:
    """Runs on its own thread. Calls on_level(rms) while there is signal, and
    on_silence() when nothing is playing (so the soul-connector can tell "no
    music" apart from "music playing through a quiet passage")."""

    def __init__(self, on_level: Callable[[float], None], on_silence: Callable[[], None]):
        self._on_level = on_level
        self._on_silence = on_silence
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            sink = _default_sink_name()
            index = _monitor_index(sink) if sink else None
            if index is None:
                self._on_silence()
                self._stop.wait(3)
                continue
            try:
                with sd.InputStream(
                    device=index,
                    channels=2,
                    samplerate=44100,
                    blocksize=int(44100 * _WINDOW_S),
                    dtype="float32",
                    callback=self._callback,
                ):
                    while not self._stop.is_set():
                        if _default_sink_name() != sink:
                            break  # the default sink changed, reopen with the new one
                        sd.sleep(500)
            except Exception:
                self._on_silence()
                self._stop.wait(3)

    def _callback(self, indata, frames, time_info, status) -> None:
        rms = float(np.sqrt(np.mean(np.square(indata))))
        if rms < _SILENCE_THRESHOLD:
            self._on_silence()
        else:
            self._on_level(min(1.0, (rms**0.5) * 2.6))
