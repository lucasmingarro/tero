"""System health watchdog: warns and shuts Tero down if the machine gets
into a state that puts the stability of the session at risk.

First things first, because it is the question that started this file:
**Tero cannot burn the graphics card.** GPU thermal control lives in the
firmware, below the operating system: the card throttles itself (slowdown)
when it reaches its limit and shuts itself off if it goes past it, and no
process can disable that. On this machine (RTX 5060 Laptop) the factory
limit is 87 C, with hardware slowdown 2 C above that and shutdown 5 C
above. A Python process has no way around it.

The real risks are different, and they are about stability, not hardware:

1. **Running out of RAM.** The only one that can hang the whole system.
   Whisper large-v3 and Ollama are not light, and this machine has 14 GB.
   When `MemAvailable` gets close to zero, Linux starts swapping nonstop
   and the desktop is unusable for several minutes before the OOM killer
   decides who to kill (and it may not pick Tero).
2. **Running out of VRAM.** Already happened: starting a second daemon
   killed the second one with `CUDA out of memory`. It breaks nothing, but
   since this GPU also drives the desktop, VRAM pressure can make desktop
   applications slow or crash.

Hence this module's criterion: **RAM shuts down, thermals only warn.**
Shutting down on temperature would be redundant with what the card already
does on its own, so it warns (at the user's explicit request, he wants to
know) but only shuts down if the hardware slowdown holds for a long while,
which means the card has been throttling and the machine probably has a
ventilation problem.

None of this is spoken: if the problem is precisely a lack of memory,
spinning up the TTS to announce it makes things worse. It goes out through
a desktop notification and the log.
"""

import threading
import time
from typing import Callable

from os_platform import get_platform

HEALTH_EXIT_CODE = 3  # recognized by the ./tero launcher

_INTERVAL_S = 5.0

# What the platform reports as available without the machine starting to
# swap (MemAvailable on Linux, see Platform.available_ram_mb), not "free":
# that is the number that matters here.
_CRITICAL_RAM_MB = 400
_WARNING_RAM_MB = 1200

_WARNING_VRAM_MB = 200

# How many consecutive samples are needed to act. A one-off spike (Chrome
# opening something heavy) must not cut a conversation in half; a real
# problem holds.
_RAM_SHUTDOWN_SAMPLES = 3  # ~15s
_THERMAL_SHUTDOWN_SAMPLES = 12  # ~60s of sustained hardware slowdown
_WARNING_SAMPLES = 2

# So the same notification is not repeated every 5 seconds.
_WARNING_QUIET_PERIOD_S = 300.0


class HealthMonitor:
    """Samples the machine state in the background and warns or shuts down.

    `on_critical(reason)` is called exactly once, and it is what decides how
    to close -- this module never calls sys.exit on its own, so the daemon
    is not left half closed from some arbitrary thread.
    """

    def __init__(
        self,
        on_critical: Callable[[str], None],
        on_warning: Callable[[str], None],
        interval_s: float = _INTERVAL_S,
    ):
        self._on_critical = on_critical
        self._on_warning = on_warning
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._already_shutdown = False
        self._streaks: dict[str, int] = {}
        self._last_warning: dict[str, float] = {}

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _count(self, key: str, active: bool) -> int:
        self._streaks[key] = self._streaks.get(key, 0) + 1 if active else 0
        return self._streaks[key]

    def _warn(self, key: str, text: str) -> None:
        now = time.monotonic()
        if now - self._last_warning.get(key, -_WARNING_QUIET_PERIOD_S) < _WARNING_QUIET_PERIOD_S:
            return
        self._last_warning[key] = now
        self._on_warning(text)

    def _shutdown(self, reason: str) -> None:
        if self._already_shutdown:
            return
        self._already_shutdown = True
        self._on_critical(reason)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self._check()
            except Exception as error:
                # The watchdog can never be the one that breaks the daemon.
                print(f"(monitor de salud falló, se sigue sin él: {error})")
                return

    def _check(self) -> None:
        platform = get_platform()
        ram_mb = platform.available_ram_mb()
        if ram_mb is not None:
            if self._count("critical_ram", ram_mb < _CRITICAL_RAM_MB) >= _RAM_SHUTDOWN_SAMPLES:
                self._shutdown(
                    f"queda muy poca memoria libre ({ram_mb:.0f} MB). "
                    "Si sigo, el equipo entero se puede colgar swapeando."
                )
                return
            if self._count("low_ram", ram_mb < _WARNING_RAM_MB) >= _WARNING_SAMPLES:
                self._warn("low_ram", f"Queda poca memoria libre ({ram_mb:.0f} MB).")

        gpu = platform.gpu_status()
        if gpu is None:
            return

        if self._count("thermal", gpu["thermal_slowdown"]) >= _THERMAL_SHUTDOWN_SAMPLES:
            self._shutdown(
                f"la placa de video viene frenándose sola por temperatura "
                f"({gpu['temperature_c']:.0f} C) hace un rato largo. No hay riesgo de "
                "rotura (eso lo maneja el firmware), pero conviene dejarla enfriar."
            )
            return
        if self._streaks.get("thermal", 0) >= _WARNING_SAMPLES:
            self._warn(
                "thermal",
                f"La placa se está frenando sola por temperatura ({gpu['temperature_c']:.0f} C).",
            )

        if self._count("vram", gpu["free_vram_mb"] < _WARNING_VRAM_MB) >= _WARNING_SAMPLES:
            self._warn(
                "vram",
                f"Queda poca memoria de video ({gpu['free_vram_mb']:.0f} MB); "
                "el escritorio puede ponerse lento.",
            )
