"""Phase 1 (skeleton): key -> recording -> transcription -> repeat out loud.

Useful to measure the real latency of the local path before adding the brain
(Ollama) and the tools.
"""

import glob
import os
import sys
import sysconfig


def _ensure_cuda_libs() -> None:
    """The pip packages nvidia-cublas-cu12/nvidia-cudnn-cu12 ship the .so
    files but ctranslate2 looks for them through LD_LIBRARY_PATH, which is
    only read when the process starts. If it is not set, the process
    re-executes itself once with the corrected path (which avoids having to
    export it by hand before every daemon start)."""
    if sys.platform != "linux":
        # CUDA (and LD_LIBRARY_PATH itself) is Linux-only here: the nvidia-*
        # packages are not even installed on other platforms (see the
        # markers in pyproject.toml), and on macOS CTranslate2 runs on CPU.
        return
    if os.environ.get("_TERO_CUDA_LIBS_OK"):
        return
    libs = sorted(glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "nvidia", "*", "lib")))
    if not libs or all(lib in os.environ.get("LD_LIBRARY_PATH", "") for lib in libs):
        os.environ["_TERO_CUDA_LIBS_OK"] = "1"
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(libs) + (":" + current if current else "")
    os.environ["_TERO_CUDA_LIBS_OK"] = "1"
    # execv does not inherit the -u flag: without this, stdout is block
    # buffered (it is not a tty) and the daemon's prints are not visible
    # live.
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


_ensure_cuda_libs()

import threading
import time
import tomllib
from pathlib import Path

import numpy as np
import sounddevice as sd

import health
from brain.router import Brain
from os_platform import create_platform
from soul_connector.server import SoulConnectorServer
from soul_connector.system_audio import SystemAudioMonitor
from tools import music, youtube
from tools._ducking import Ducker
from voice.stt import HybridSTT, LocalSTT, groq_configured
from voice.tts import TTS

CONFIG_PATH = Path(__file__).parent / "config.toml"


def _beep(frequency_hz: float, duration_s: float = 0.08) -> None:
    t = np.linspace(0, duration_s, int(44100 * duration_s), endpoint=False)
    tone = 0.2 * np.sin(2 * np.pi * frequency_hz * t).astype(np.float32)
    sd.play(tone, samplerate=44100)
    sd.wait()


class Recorder:
    """Accumulates audio from an InputStream while it is active."""

    def __init__(self, sample_rate_hz: int, channels: int, on_level=None):
        self._sample_rate_hz = sample_rate_hz
        self._channels = channels
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        # Optional: live mic level while recording, so the soul-connector
        # moves with the user's real voice instead of a fixed value.
        self._on_level = on_level
        # Auto-gain instead of a fixed multiplier: a hand-calibrated number
        # (e.g. rms*2.5) looks right with one mic and wrong with another --
        # the real rms of a mic lives on a much lower and less predictable
        # scale than TTS audio (which comes out normalized). The recent
        # volume peak is tracked and everything is normalized against it, so
        # speaking at a normal volume always opens the wave almost fully,
        # whatever the mic. The peak decays slowly (not all at once during a
        # short silence between words) but rises instantly if you speak
        # louder.
        self._peak_rms = 0.02

    def _callback(self, indata, frames, time_info, status):
        self._chunks.append(indata.copy())
        if self._on_level is not None:
            rms = float(np.sqrt(np.mean(np.square(indata))))
            self._peak_rms = max(rms, self._peak_rms * 0.999)
            # Factor 3.0 at the end (at the user's request, the wave barely
            # moved): it amplifies the level already normalized against the
            # peak, it does not change the normalization itself. Cap 2.2,
            # not 1.0 -- same reason as voice/tts.py: 1.0 saturated right
            # away and left no room for peaks to look bigger than the rest.
            level = (min(1.0, rms / self._peak_rms) ** 0.5) * 3.0
            self._on_level(min(2.2, level))

    def start(self) -> None:
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self._sample_rate_hz,
            channels=self._channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks, axis=0).reshape(-1)


class Tero:
    def __init__(self, config: dict):
        self._config = config
        self._platform = create_platform(key=config["key"]["name"])
        self._recorder = Recorder(
            config["audio"]["sample_rate_hz"], config["audio"]["channels"],
            on_level=self._soul_connector_level,
        )
        # The soul-connector first, before the models: that way it can show
        # which startup stage it is on, instead of sitting there drawing a
        # wave that looks ready and does not respond.
        self._soul_connector = self._create_soul_connector()
        # Before touching self._stt/_tts: _say() (the spoken notice for Groq
        # <-> local) goes through _set_soul_connector_state(), which needs
        # both.
        self._voice_state = "idle"
        self._ducker = Ducker()

        if groq_configured():
            # Whisper is not even loaded here: with Groq in the picture,
            # normal personal push-to-talk use does not come close to the
            # free plan limit (2000 requests/day), so local Whisper stays as
            # the fallback for when the Internet fails, loaded only the first
            # time it is needed -- see voice/stt.py.
            self._soul_connector_loading("Transcripción por Groq (online)", 0.0)
            self._stt = HybridSTT(
                config["stt"], on_notice=self._say, on_loading=self._soul_connector_loading,
                sample_rate_hz=config["audio"]["sample_rate_hz"],
            )
            print("STT: Groq online, Whisper local de respaldo si hace falta.")
        else:
            self._soul_connector_loading("Cargando transcripción", 0.0)
            print("Cargando modelo de transcripción...")
            self._stt = LocalSTT(**config["stt"])
        self._soul_connector_loading("Cargando voz", 1 / 3)
        print("Cargando voz...")
        self._tts = TTS(**config["tts"])
        self._brain = Brain(**config["brain"])
        # Before saying "listening": right after a machine restart, loading
        # the model takes longer than a query timeout (see brain/router.py),
        # and the first request failed with "se colgó el modelo local".
        self._soul_connector_loading("Cargando modelo de lenguaje", 2 / 3)
        print("Cargando modelo de lenguaje...")
        self._brain.preload()
        self._soul_connector_loading(None)
        self._audio_monitor = self._create_audio_monitor()
        self._shutdown_reason: str | None = None
        self._health_monitor = health.HealthMonitor(
            on_critical=self._health_critical, on_warning=self._health_warning
        )

        self._recording = False
        self._toggle_mode = False
        self._down_time = 0.0
        self._toggle_threshold_s = config["key"]["toggle_threshold_s"]
        # Barge-in: set in on_down if _voice_state is "speaking" at that
        # moment -- voice/tts.py checks it on every audio block and cuts
        # right away. It is cleared when each _process() starts, so every
        # turn begins with the flag down for its own TTS.
        self._cancel_tts = threading.Event()

        self._last_song_poll = 0.0
        # When the current pause started (None if not paused or nothing is
        # loaded). Used to hide the soul-connector player if it stays paused
        # for a long time -- showing "paused" forever after the user forgot
        # about the music is visual noise that adds nothing.
        self._paused_since: float | None = None

    def _create_soul_connector(self) -> SoulConnectorServer | None:
        # The soul-connector is an optional client: if this fails for
        # whatever reason, the daemon has to keep working, with no window.
        try:
            return SoulConnectorServer()
        except Exception as error:
            print(f"(soul-connector no disponible: {error})")
            return None

    def _soul_connector_loading(self, text: str | None, progress: float = 0.0) -> None:
        if self._soul_connector is not None:
            self._soul_connector.loading(text, progress)

    def _create_audio_monitor(self) -> SystemAudioMonitor | None:
        # Like the soul-connector: optional, the daemon has to run without
        # it.
        try:
            return SystemAudioMonitor(
                on_level=self._music_level, on_silence=self._music_silence
            )
        except Exception as error:
            print(f"(monitor de audio del sistema no disponible: {error})")
            return None

    def _set_soul_connector_state(self, name: str) -> None:
        self._voice_state = name
        if self._soul_connector is not None:
            self._soul_connector.state(name)

    def _say(self, text: str) -> None:
        """Speaks a notice outside the normal turn flow (e.g. Groq <-> local
        Whisper), without going through the brain or a user recording."""
        previous = self._voice_state
        self._set_soul_connector_state("speaking")
        self._tts.speak(text, on_level=self._soul_connector_level)
        self._set_soul_connector_state(previous)

    def _music_level(self, level: float) -> None:
        # Only if Tero is not in the middle of listening/thinking/speaking --
        # that always has visual priority over "there is music playing".
        if self._soul_connector is not None and self._voice_state == "idle":
            self._soul_connector.state("music")
            self._soul_connector.level(level)

    def _music_silence(self) -> None:
        if self._soul_connector is not None and self._voice_state == "idle":
            self._soul_connector.state("idle")

    def on_down(self) -> None:
        if self._recording:
            return
        if self._voice_state == "thinking":
            # Transcribing or waiting for the brain: there is nothing
            # playing yet to cut, and starting to record in parallel would
            # step on the turn in progress (two _process() at once, two TTS
            # competing). The press is ignored until it moves to "speaking"
            # or back to "idle" -- see barge-in below for the case that can
            # be interrupted.
            return
        if self._voice_state == "speaking":
            # Barge-in: do not wait for it to finish speaking. voice/tts.py
            # checks this flag on every audio block and cuts the sound right
            # away (see TTS.speak). The _process() of the interrupted turn
            # notices it was cancelled and does not step on the "listening"
            # state set two lines below.
            self._cancel_tts.set()
        self._down_time = time.monotonic()
        self._recording = True
        self._toggle_mode = False
        _beep(880)
        # Duck the master volume right here: it lasts exactly as long as the
        # recording (until on_up), not the whole turn -- "thinking" and
        # "speaking" have no open mic to protect, and this way it never
        # overlaps with Tero's own voice (see tools/_ducking.py).
        self._ducker.activate()
        self._set_soul_connector_state("listening")
        self._recorder.start()

    def on_up(self) -> None:
        if not self._recording:
            return
        if not self._toggle_mode:
            duration = time.monotonic() - self._down_time
            if duration < self._toggle_threshold_s:
                # short tap: switches to dictation mode, keeps recording
                self._toggle_mode = True
                return
        # push-to-talk released, or a second tap closing dictation mode
        self._recording = False
        self._toggle_mode = False
        _beep(440)
        self._ducker.deactivate()
        audio = self._recorder.stop()
        # On a separate thread: that leaves the thread reading the key
        # (os_platform/linux.py) free to notice a new press while
        # STT/brain/TTS run -- without this, the barge-in in on_down could
        # never fire because that same thread would be busy inside
        # _process().
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio: np.ndarray) -> None:
        if audio.size < self._config["audio"]["sample_rate_hz"] * 0.2:
            print("(audio demasiado corto, se ignora)")
            self._set_soul_connector_state("idle")
            return
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < 0.001:
            # Total silence (mic off/muted): not even worth sending to
            # Whisper -- with real silence it hallucinates things like
            # "gracias" instead of returning empty text. Stopping here
            # avoids that whole class of false positive.
            print(f"(audio en silencio, rms={rms:.5f} -- ¿mic apagado/mute? no se transcribe)")
            self._platform.notify("(silencio: ¿el micrófono está apagado?)")
            self._set_soul_connector_state("idle")
            return
        self._cancel_tts.clear()
        self._set_soul_connector_state("thinking")
        t0 = time.monotonic()
        text = self._stt.transcribe(audio)
        t1 = time.monotonic()
        print(f"transcripción ({t1 - t0:.2f}s): {text!r}")
        self._platform.notify(text or "(no se entendió nada)")
        if not text:
            self._set_soul_connector_state("idle")
            return
        response = self._brain.respond(text)
        t2 = time.monotonic()
        print(f"cerebro ({t2 - t1:.2f}s): {response!r}")
        if not response:
            print("(silencio intencional, no hay nada que decir)")
            self._set_soul_connector_state("idle")
            return
        self._set_soul_connector_state("speaking")
        self._tts.speak(response, on_level=self._soul_connector_level, cancel=self._cancel_tts)
        if not self._cancel_tts.is_set():
            self._set_soul_connector_state("idle")
        # If it was cancelled, on_down already set the state to "listening"
        # for the turn that interrupted this one -- do not step on it here.
        t3 = time.monotonic()
        print(f"tts ({t3 - t2:.2f}s), total ({t3 - t0:.2f}s)")

    def _soul_connector_level(self, level: float) -> None:
        if self._soul_connector is not None:
            self._soul_connector.level(level)

    def _health_critical(self, reason: str) -> None:
        # It only records the request: the actual shutdown is done by the
        # main loop, which owns the process. Closing from the monitor thread
        # would leave whatever is happening half done (an open recording,
        # the TTS speaking).
        print(f"\n*** Cerrando Tero para cuidar el equipo: {reason}")
        self._platform.notify(f"Cierro Tero: {reason}")
        self._shutdown_reason = reason

    def _health_warning(self, text: str) -> None:
        print(f"(salud: {text})")
        self._platform.notify(text)

    def run(self) -> None:
        self._platform.listen_key(self.on_down, self.on_up)
        self._health_monitor.start()
        print(f"Tero escuchando. Mantené {self._config['key']['name']} para hablar.")
        try:
            while self._shutdown_reason is None:
                time.sleep(0.5)
                self._update_soul_connector_song()
        except KeyboardInterrupt:
            print("\nChau.")
            return
        # Exiting through the health monitor: its own exit code so the
        # launcher tells it apart from a real crash.
        sys.exit(health.HEALTH_EXIT_CODE)

    def _update_soul_connector_song(self) -> None:
        # Every ~5s (not on every 0.5s tick) so the Spotify API is not hit
        # more than needed -- it is always sent (not only when the track
        # changes) because the progress advances on every poll and the
        # soul-connector uses it to resync the bar it interpolates between
        # updates.
        if self._soul_connector is None:
            return
        now = time.monotonic()
        if now - self._last_song_poll < 5.0:
            return
        self._last_song_poll = now
        info = music.playback_state()
        if info is None:
            # Nothing playing on Spotify: if a YouTube channel is open, show
            # that instead -- same slot in the interface, one thing at a time
            # (Spotify wins if both are active, a rare case).
            info = youtube.current_state()
        if info is None or info["playing"]:
            self._paused_since = None
        else:
            if self._paused_since is None:
                self._paused_since = now
            elif now - self._paused_since >= 30.0:
                # Paused a while ago: the whole player is hidden (as if
                # nothing were loaded), not just left frozen on pause
                # forever.
                info = None
        self._soul_connector.song(info)


def main() -> None:
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    Tero(config).run()


if __name__ == "__main__":
    main()
