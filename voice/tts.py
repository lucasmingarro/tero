"""Local speech synthesis with Piper."""

import threading
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from piper import PiperVoice

MODELS_DIR = Path(__file__).parent / "models"

_LEVEL_WINDOW_S = 0.05  # how often an audio level is reported (soul-connector)


class TTS:
    def __init__(self, voice: str = "es_AR-daniela-high"):
        model = MODELS_DIR / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(
                f"Falta el modelo de voz {model}. Descargalo con:\n"
                f"  uv run python -m piper.download_voices {voice} --download-dir {MODELS_DIR}"
            )
        self._voice = PiperVoice.load(model)

    def speak(
        self,
        text: str,
        on_level: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """on_level: optional callback, RMS (roughly 0-1) every
        _LEVEL_WINDOW_S seconds during playback -- the soul-connector uses it
        to animate itself.
        cancel: if passed and set while speaking (e.g. the user pressed the
        key to interrupt), it cuts the audio right away and does not
        synthesize the remaining chunks of text -- see main.py, on_down."""
        for chunk in self._voice.synthesize(text):
            if cancel is not None and cancel.is_set():
                return
            audio = chunk.audio_float_array
            if on_level is None:
                sd.play(audio, samplerate=chunk.sample_rate)
                sd.wait()
            else:
                self._play_with_level(audio, chunk.sample_rate, on_level, cancel)

    def _play_with_level(
        self,
        audio: np.ndarray,
        samplerate: int,
        on_level: Callable[[float], None],
        cancel: threading.Event | None = None,
    ) -> None:
        """Plays through an OutputStream + callback (continuous, without the
        micro gaps of chopping it up with repeated sd.play()/wait()) and
        reports the RMS of every block that actually plays."""
        step = max(1, int(samplerate * _LEVEL_WINDOW_S))
        position = 0
        finished = False

        def callback(outdata, frames, time_info, status):
            nonlocal position, finished
            if cancel is not None and cancel.is_set():
                # CallbackAbort cuts immediately, without waiting for what
                # is already in the output buffer to finish playing (unlike
                # CallbackStop) -- that is what makes it "shut up" at once.
                raise sd.CallbackAbort
            block = audio[position : position + frames]
            outdata[: len(block), 0] = block
            if len(block) < frames:
                outdata[len(block) :, 0] = 0.0
                finished = True
            rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
            # sqrt(rms) instead of rms*k: linear flattened the quiet parts
            # of the voice (low rms) close to zero, they looked almost
            # motionless. The square root lifts those relatively more,
            # without losing the peaks landing close to 1. Factor 5.0
            # (raised from 1.7, at the user's request): with 1.7 the wave
            # barely moved during normal speech. Cap 2.2 instead of 1.0 (the
            # "amplitude" wave.js receives does not have to stop at 1.0, it
            # is just one more multiplier): with a cap of 1.0, normal speech
            # saturated 82% of the time and left no room for peaks to look
            # bigger than the rest.
            on_level(min(2.2, (rms**0.5) * 5.0))
            position += frames
            if finished:
                raise sd.CallbackStop

        with sd.OutputStream(
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            blocksize=step,
            callback=callback,
        ) as stream:
            while stream.active:
                sd.sleep(30)
