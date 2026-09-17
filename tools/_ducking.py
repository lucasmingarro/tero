"""Ducking: turns the master output volume down while the microphone is
recording (push-to-talk held down), and brings it back when the key is
released.

Redesigned on 2026-09-14 after a full day of real bugs with the previous
design (ducking each application stream separately, one by one,
identifying them by PID in PipeWire): Spotify packaged as a Snap does not
carry its PID on the node that actually plays audio -- you have to find it
on a sibling "client" node through `client.id`, and without that Spotify
stopped being ducked at all. Chrome changes the id (and sometimes the
whole stream disappears from `pw-dump` for almost a second while the new
page loads) when navigating channels over CDP, and that left the volume
stuck at the ducked level for good more than once. Each fix covered one
concrete case without fixing the class of problem: figuring out "which
PipeWire stream is this application, right now" is structurally fragile,
it changes all the time for reasons that have nothing to do with Tero.

The way out: the user asked the right question -- why duck per
application if the master volume and Tero's voice never need to coexist?
The only real reason to duck is protecting the microphone recording while
it is open. Neither "thinking" (STT + brain, the mic is already closed)
nor "speaking" (TTS, Tero's own voice) needs anything ducked -- there is
no recording in progress to protect, and if the user wants to interrupt
Tero mid-sentence they press the key again (barge-in, see main.py) with
the volume having nothing to do with it. Narrowing ducking to exactly the
window where the key is held (`on_down` -> `on_up`, no longer tied to
listening/thinking/speaking/idle) makes the **master** volume
(`@DEFAULT_AUDIO_SINK@`) enough -- it always exists, it does not appear
and disappear halfway through a page load like a Chrome stream -- and all
the complexity of tracking individual applications goes away. The only
reason this module had never touched the master sink is that Tero's own
voice (TTS, the beeps) comes out of that same sink -- but since ducking
now lasts exactly as long as the recording, it never overlaps with
anything Tero needs heard: the user is still holding the key, Tero has
not said a word.

A single background thread interpolates the volume towards a target that
can change while it runs, without ever blocking the calling thread.
`wpctl get-volume` rounds to 2 decimals in its text output: re-reading it
on every ramp step to compute the next one (instead of keeping count
ourselves) stalls as soon as the difference drops below ~0.01 -- which is
why the real volume is read only once per full cycle (bootstrap) and from
there on the Ducker itself is the single source of truth for "where the
volume is now". Reading and writing the volume goes through the platform
layer (`Platform.master_volume`/`set_master_volume`).
"""

import threading
import time

from os_platform import get_platform

_DUCKED_VOLUME = 0.10
_STEP_S = 0.02
# Go down faster than up (cover whatever is playing before the mic
# finishes opening) and come back up slowly (so the return is not
# noticeable) -- the same asymmetric smoothing already used in the
# soul-connector for the RMS.
_FADE_DOWN_FACTOR = 0.22
_FADE_UP_FACTOR = 0.05
_DONE_THRESHOLD = 0.004


class Ducker:
    def __init__(self):
        self._lock = threading.Lock()
        # Captured once per cycle (see the module note) -- if the user
        # changes the volume by hand while Tero is in the middle of
        # something, the next cycle will overwrite it with this stale
        # value. Not worth solving: this is a personal assistant, and
        # releasing the key is enough for the next press to re-read the
        # actual current volume.
        self._real_volume: float | None = None  # where to return on key release
        self._current: float | None = None  # our own in-flight estimate
        self._target: float | None = None
        self._thread: threading.Thread | None = None

    def activate(self) -> None:
        """Call right when the microphone opens (on_down)."""
        with self._lock:
            self._target = _DUCKED_VOLUME
            self._ensure_thread()

    def deactivate(self) -> None:
        """Call right when the microphone closes (on_up)."""
        with self._lock:
            if self._real_volume is None:
                return  # never got activated
            self._target = self._real_volume
            self._ensure_thread()

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._ramp, daemon=True)
            self._thread.start()

    def _ramp(self) -> None:
        platform = get_platform()
        with self._lock:
            current = self._current
        if current is None:
            # Bootstrap: first duck of this cycle -- the only real read of
            # the whole cycle.
            real = platform.master_volume()
            if real is None:
                return
            current = real
            with self._lock:
                self._real_volume = real
                self._current = real

        while True:
            with self._lock:
                target = self._target
            if target is None:
                return
            if abs(target - current) < _DONE_THRESHOLD:
                current = target
                platform.set_master_volume(current)
                with self._lock:
                    self._current = current
                    if current == self._real_volume:
                        # A full round trip completed (went down and back
                        # up to the real value): forget the bootstrap so
                        # the next key press re-reads the real volume from
                        # scratch, in case the user changed it by hand in
                        # the meantime.
                        self._current = None
                        self._real_volume = None
                return
            factor = _FADE_DOWN_FACTOR if target < current else _FADE_UP_FACTOR
            current += (target - current) * factor
            platform.set_master_volume(current)
            with self._lock:
                self._current = current
            time.sleep(_STEP_S)
