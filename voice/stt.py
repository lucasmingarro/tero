"""Transcription: Groq online first, local Whisper as the fallback.

Why it is built this way: what matters most to the user is not how much
general knowledge the model has, it is that Tero understands free-form
sentences ("abrí Spotify y poné Fito", not fixed commands) -- and that
depends on hearing well, not on reasoning well. A badly transcribed
request gives the brain no chance at all (seen live: "Poné música" ->
"Buena música." made the whole turn fail).

Groq offers the same Whisper large-v3 for free, with no credit card: for
personal push-to-talk use, the real limit of the free plan (2000
requests/day) is nowhere close. That is why local Whisper is only the
fallback for when the Internet drops or Groq fails -- it is loaded on
demand, not at startup, so the ~3.7GB of VRAM it takes stay free for the
language model to fit entirely on the GPU (see brain/router.py).
"""

import io
import time
import wave
from pathlib import Path

import httpx
import numpy as np
from faster_whisper import WhisperModel

SAMPLE_RATE_HZ = 16000

_KEY_PATH = Path.home() / ".config" / "tero" / "groq_key"

# Biases transcription towards proper nouns Whisper keeps getting badly
# wrong (seen live: "Trelew" -> "entre el EU", "Toay" -> "todo ahí",
# "Jamiroquai" -> random variants every time). initial_prompt is not
# transcribed, it only conditions the decoder to recognize them if they
# sound similar. The user's cities (Trelew, where he is from; Toay, where he
# lives now) go first because they are the ones he will name the most.
# Extend here if more problematic names show up. Used both locally and with
# Groq.
_NAME_HINTS = (
    "Trelew, Toay, La Pampa, Rawson, Santa Rosa, Puerto Madryn, "
    "Comodoro Rivadavia, Charly García, Jamiroquai, Fito Páez, "
    "Soda Estéreo, Billie Eilish."
)


def groq_configured() -> bool:
    """True if there is a Groq API key stored (even if it later fails)."""
    return _read_key() is not None


def _read_key() -> str | None:
    try:
        key = _KEY_PATH.read_text().strip()
    except FileNotFoundError:
        return None
    return key or None


def _wav_bytes(audio: np.ndarray, sample_rate_hz: int) -> bytes:
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm16.tobytes())
    return buffer.getvalue()


class GroqError(Exception):
    """Groq did not transcribe: no key, no network, rate limited or a server
    error.

    retry_in_s: how long to wait before trying Groq again, if the server
    said so explicitly (the Retry-After header of a 429).
    """

    def __init__(self, reason: str, retry_in_s: float | None = None):
        super().__init__(reason)
        self.retry_in_s = retry_in_s


class LocalSTT:
    """Whisper running on this machine, through faster-whisper."""

    def __init__(self, model: str = "large-v3", device: str = "auto", compute_type: str = "default"):
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: mono float32 at 16kHz, in the [-1, 1] range."""
        segments, _ = self._model.transcribe(
            audio, language="es", initial_prompt=_NAME_HINTS
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class GroqSTT:
    """Whisper running on Groq's cloud (free plan, see the module docstring)."""

    _URL = "https://api.groq.com/openai/v1/audio/transcriptions"
    # Do not let a slow Groq delay much before falling back to the local
    # one: better a slightly eager fallback than a long hanging wait.
    _TIMEOUT_S = 8.0

    def __init__(self, model: str = "whisper-large-v3"):
        self._model = model

    def transcribe(self, audio: np.ndarray, sample_rate_hz: int) -> str:
        key = _read_key()
        if key is None:
            raise GroqError(f"sin API key ({_KEY_PATH})")

        try:
            response = httpx.post(
                self._URL,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", _wav_bytes(audio, sample_rate_hz), "audio/wav")},
                data={"model": self._model, "language": "es", "prompt": _NAME_HINTS},
                timeout=self._TIMEOUT_S,
            )
        except httpx.RequestError as error:
            raise GroqError(f"sin conexión: {error}") from error

        if response.status_code == 429:
            wait = response.headers.get("retry-after")
            raise GroqError(
                "límite del plan gratis alcanzado",
                retry_in_s=float(wait) if wait else None,
            )
        if response.status_code != 200:
            raise GroqError(f"Groq respondió {response.status_code}: {response.text[:200]}")

        return (response.json().get("text") or "").strip()


# How long to wait before retrying Groq after a failure with no explicit
# Retry-After (no network, server error). Not so short that it retries on
# every request while the Internet is down, not so long that it takes ages
# to notice it came back.
_FAILURE_COOLDOWN_S = 20.0
# A cap in case a 429 ever comes with an absurd Retry-After.
_MAX_COOLDOWN_S = 300.0


class HybridSTT:
    """Tries Groq on every request; if it fails, falls back to local Whisper
    (loaded only the first time it is needed) until Groq works again.

    on_notice(text): called so the caller says it out loud, when crossing
        from online to offline and back -- never on every request, only on
        the mode change.
    on_loading(text | None, progress): the same protocol main.py uses at
        startup, so the soul-connector shows "Cargando transcripción..."
        while the local model loads for the first time.
    """

    def __init__(self, local_config: dict, on_notice, on_loading, sample_rate_hz: int = SAMPLE_RATE_HZ):
        self._local_config = local_config
        self._on_notice = on_notice
        self._on_loading = on_loading
        self._sample_rate_hz = sample_rate_hz
        self._groq = GroqSTT()
        self._local: LocalSTT | None = None
        self._offline = False
        self._next_online_attempt = 0.0

    def transcribe(self, audio: np.ndarray) -> str:
        if time.monotonic() >= self._next_online_attempt:
            try:
                text = self._groq.transcribe(audio, self._sample_rate_hz)
            except GroqError as error:
                self._go_offline(error)
            else:
                self._go_online()
                return text
        return self._transcribe_local(audio)

    def _go_offline(self, error: GroqError) -> None:
        cooldown = min(error.retry_in_s or _FAILURE_COOLDOWN_S, _MAX_COOLDOWN_S)
        self._next_online_attempt = time.monotonic() + cooldown
        if not self._offline:
            self._offline = True
            print(f"(Groq no disponible: {error} -- paso a Whisper local)")
            self._on_notice("Pasando a modo offline, esperá que cargo Whisper.")

    def _go_online(self) -> None:
        if self._offline:
            self._offline = False
            # Without this the local model stays loaded in VRAM forever
            # after the first outage, even if it is never used again --
            # exactly what this design is trying to avoid.
            self._local = None
            print("(Groq disponible de nuevo -- descargo Whisper local)")
            self._on_notice("Volví a modo online.")

    def _transcribe_local(self, audio: np.ndarray) -> str:
        if self._local is None:
            self._on_loading("Cargando transcripción (modo offline)", 0.0)
            self._local = LocalSTT(**self._local_config)
            self._on_loading(None)
        return self._local.transcribe(audio)
