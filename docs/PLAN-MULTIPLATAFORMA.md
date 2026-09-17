# Tero — Plan multiplataforma (Linux + macOS)

Estado del repo al 2026-09-17: rename a inglés staged pero sin commitear
(`cerebro/` → `brain/`, `health.py` nuevo, docs tocadas). Código Python
~3.4k líneas. Sólo existe `os_platform/linux.py`; `create_platform()` tira
`NotImplementedError` en cualquier otro OS.

Objetivo mínimo: que `uv sync` + `tero` arranquen en la Mac de Lucas y
funcione el loop completo (tecla → grabar → STT → cerebro → TTS) con las
herramientas que no dependen del escritorio Linux. Lo GNOME-específico queda
como "sólo Linux", no se porta.

---

## Diagnóstico: qué está atado a Linux hoy

La abstracción `Platform` (5 métodos) existe pero está casi vacía: de los 5
sólo `listen_key` y `notify` están implementados, y la mayor parte del
acoplamiento a Linux vive **fuera** de `os_platform/`, en `tools/`,
`soul_connector/`, `health.py`, `main.py` y el launcher bash.

Crítico (bloquea arrancar en Mac):

| Pieza | Dependencia Linux | Reemplazo en macOS |
|---|---|---|
| `pyproject.toml` | `evdev`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` sin markers → `uv sync` falla en Mac | environment markers (`sys_platform == 'linux'`) |
| `main.py::_ensure_cuda_libs` | busca libs nvidia y hace `os.execv` | no-op fuera de Linux |
| `os_platform/linux.py` | `evdev` + `/dev/input` | `os_platform/macos.py` con `pynput` (requiere permiso Input Monitoring / Accesibilidad) |
| `tero` (launcher bash) | `flock` (no existe en macOS), `pgrep -f`, `gnome-extensions`, `wmctrl`, `systemctl` | launcher en Python (`tero.py`/`python -m tero`), lock por archivo con `fcntl`/portalocker |
| `tools/_ducking.py` | `wpctl` sobre `@DEFAULT_AUDIO_SINK@` | `osascript set volume output volume N` (o CoreAudio vía pyobjc si la rampa queda lenta) |
| `tools/volume.py` | `wpctl` | ídem |
| `health.py` | `/proc/meminfo`, `nvidia-smi` | `psutil.virtual_memory()`; GPU: no-op (ya devuelve `None` si falla) |
| `Platform.notify` | `notify-send` | `osascript display notification` |

Medio (funciona degradado o se apaga):

| Pieza | Dependencia | Plan |
|---|---|---|
| `tools/music.py::control_playback`, `pause_spotify` | `playerctl` (MPRIS) | Spotify Web API (`/me/player/pause|next|previous`, ya hay token) como camino común; AppleScript `tell application "Spotify"` como alternativa local |
| `tools/music.py::_open_spotify` | `xdg-open spotify:` | `open -a Spotify` / `webbrowser.open("spotify:")` |
| `tools/_youtube_screen.py` | `google-chrome --ozone-platform=x11`, `wmctrl`, `/proc/<pid>/cmdline`, `playerctl chromium.instance` | ver sección "YouTube en background" |
| `soul_connector/system_audio.py` | monitor de sink PipeWire | macOS no tiene loopback nativo; apagar (estado "music" sin onda) o BlackHole opcional |
| `soul_connector/window.py` | `pywebview[qt]`, `QT_QPA_PLATFORM=xcb`, `wmctrl` | `pywebview` backend Cocoa (frameless + on_top funcionan nativo, sin Qt ni wmctrl) |
| `tools/terminal.py` | AT-SPI + `wl-paste`/`xclip` primary | macOS Accessibility (`AXUIElement` vía pyobjc) para Terminal.app/iTerm2, o AppleScript `contents of selected tab`; no hay primary selection → sin fallback |
| `tools/move_window.py` | D-Bus a la extensión GNOME | AppleScript/AX `set position of window`; baja prioridad |
| `systemd/tero.service` | systemd user | `launchd` plist (`~/Library/LaunchAgents/local.tero.plist`) |
| `tools/_read_terminal_atspi.py` | `/usr/bin/python3` + PyGObject | sólo Linux |
| `soul-connector-gnome/` | GNOME Shell | sólo Linux, no se porta |

Cross-platform ya (no tocar): `sounddevice`, `faster-whisper`, Groq STT,
Piper TTS, Ollama, `httpx`, Spotify Web API, Telegram, Open-Meteo/OSRM,
`webbrowser`, `tools/__init__.py` (registry), `brain/`.

Rutas: todo usa `~/.config/tero/` hardcodeado (7 lugares). En Mac funciona,
pero lo correcto es `platformdirs` con compatibilidad hacia atrás.

Whisper local en Apple Silicon: `faster-whisper` (CTranslate2) corre sólo
CPU en Mac. Con Groq como primario no importa; para el fallback offline
bajar el default a `medium`/`small` en Mac o evaluar `mlx-whisper` más
adelante.

---

## Decisiones de diseño a fijar antes de codear

1. **Mover el acoplamiento a la capa de plataforma.** `Platform` crece de 5 a
   ~10 métodos: `listen_key`, `notify`, `get_master_volume/set_master_volume`
   (ducking + volume), `media(action)`, `open_app(uri)`, `read_terminal()`,
   `available_ram_mb()`, `gpu_status()`, `browser_launch(url, profile)`.
   Las tools dejan de llamar `subprocess` directo; llaman `platform.x()`.
   Lo que no tenga implementación en un OS devuelve `None`/`NotSupported` y
   la tool contesta por voz "eso no anda en esta máquina".
2. **Catálogo de tools por plataforma.** `brain/router.py` importa las tools
   incondicionalmente; pasar a registrar sólo las que la plataforma soporta
   (`move_window_to_monitor` y `read_terminal` no aparecen en el catálogo
   del modelo en Mac hasta que existan). Evita que el modelo llame algo que
   va a fallar.
3. **Launcher en Python, no bash.** `tero` (bash) se reescribe como
   `tero.py` / entry point `tero` en `pyproject`: single-instance, checks de
   deps, arranque del daemon, log rotativo, arranque opcional del
   soul-connector. Un solo código para los dos OS.
4. **Config con overrides por OS.** `config.toml` gana secciones
   `[linux]`/`[macos]` (tecla por defecto: `KEY_RIGHTCTRL` vs `ctrl_r`,
   modelo Whisper fallback, browser binario).
5. **Soul-connector:** en Mac sólo la variante pywebview (Cocoa). La
   extensión GNOME queda como está. `system_audio` se desactiva en Mac
   (opcional BlackHole en fase 4).

### YouTube en background (pedido de Lucas)

Hoy: ventana dedicada de Chrome en un monitor fijo, movida con `wmctrl`,
navegada por CDP. El hermano quiere historial; Lucas quiere que corra en
background con Chromium.

Propuesta: hacer el "reproductor de YouTube" un backend configurable:

- `browser` (actual, portado): binario configurable (`google-chrome` /
  `chromium` / `Chromium.app`), perfil propio, CDP para navegar. En Mac
  `--window-position` funciona sin `wmctrl`. "Background" = ventana
  minimizada o en otro Space; Chromium **headless no reproduce audio**, así
  que no es opción.
- `mpv` (nuevo, opcional): `yt-dlp` resuelve el stream del canal en vivo y
  `mpv --no-video` lo reproduce. Sin ventana, cross-platform, menos RAM,
  controlable por IPC (`--input-ipc-server`) para pause/volume. Sin
  historial de Chrome (a Lucas no le importa). Es lo que mejor encaja con
  "en background".

Config: `[youtube] backend = "browser" | "mpv"`, `browser_binary = ...`.
El hermano deja `browser` + chrome; Lucas usa `mpv` o `browser` + chromium.

---

## Tareas para kiro-cli, por fase y criticidad

Cada ítem es una tarea autocontenida (un prompt a kiro-cli). Orden = orden
de ejecución. Marcar `[L]` = sólo toca Linux, `[M]` = sólo macOS, `[X]` =
común.

### Fase 0 — Cerrar lo pendiente (antes de tocar nada)

- **0.1 [X] Commitear el rename a inglés.** Revisar el diff staged, buscar
  restos de nombres viejos (`cerebro`, `boca`, `leer_terminal`, etc.) en
  código, docs, `systemd/tero.service`, `soul-connector-gnome/*.js`,
  `BITACORA.html`. Commit.
- **0.2 [X] Corregir CLAUDE.md.** Sección Ducking describe el diseño viejo
  (por app, pw-dump/PIDs); actualizar a "sink maestro sólo mientras la
  tecla está apretada" según `tools/_ducking.py`. Sacar la tabla
  "Windows (nunca implementado)" y reemplazarla por la tabla Linux/macOS de
  este plan. Marcar `os_platform/` como "en expansión" y no como "migrar =
  150 líneas".
- **0.3 [X] AGENTS.md.** Respuesta a las preguntas de kiro:
  `output_dir` = `docs/`; consolidate = sólo AGENTS.md; idioma = AGENTS.md
  en inglés, resto en castellano; CLAUDE.md intacto y referenciado desde
  AGENTS.md; checks de consistencia y completitud activados (el warning de
  Ducking se resuelve en 0.2 primero). AGENTS.md debe documentar
  explícitamente la convención: código/identificadores/comentarios en
  inglés; strings hablados, logs, errores y `brain/prompt.py` en
  castellano; docs de usuario en castellano.

### Fase 1 — Que `uv sync` y el daemon arranquen en Mac (crítico)

- **1.1 [X] `pyproject.toml` con markers.** `evdev`, `nvidia-cublas-cu12`,
  `nvidia-cudnn-cu12`, `pywebview[qt]` → `sys_platform == 'linux'`.
  `pynput`, `pywebview` (sin extra), `pyobjc-framework-Cocoa` (si hace
  falta) → `sys_platform == 'darwin'`. Agregar `psutil`, `platformdirs`
  como comunes. Regenerar `uv.lock` con `uv lock` (resuelve para ambos).
- **1.2 [X] `main.py::_ensure_cuda_libs`** no-op si `sys.platform !=
  'linux'`.
- **1.3 [X] Ampliar `Platform` (base.py)** con los métodos de la decisión 1.
  Implementarlos en `linux.py` moviendo el código que hoy vive en
  `_ducking.py`, `volume.py`, `music.py` (`_open_spotify`, `pause_spotify`,
  `_playerctl`), `health.py` (`_available_ram_mb`, `_gpu`). Las tools pasan
  a usar `create_platform()` (singleton). Sin cambio de comportamiento en
  Linux: correr una sesión real después.
- **1.4 [M] `os_platform/macos.py`.** `listen_key` con `pynput`
  (`Key.ctrl_r` por defecto; documentar el permiso de Input Monitoring en
  Ajustes → Privacidad). `notify` con `osascript`. `get/set_master_volume`
  con `osascript -e 'output volume of (get volume settings)'` / `set
  volume output volume N` (medir latencia de la rampa; si >30 ms por paso,
  pasar a CoreAudio con pyobjc). `available_ram_mb` con `psutil`.
  `gpu_status` → `None`. `open_app("spotify:")` con `open`. `media()` vía
  Spotify Web API (común) con fallback AppleScript.
- **1.5 [X] `create_platform()`** devuelve `MacOSPlatform` en `darwin`.
  Config: tecla por sección `[macos]`.
- **1.6 [X] Launcher `tero.py`** que reemplaza al bash: lock, checks
  (Ollama+modelo, Groq key, Spotify token, binarios por OS), arranque del
  daemon como subprocess, espera por líneas de log, log rotativo,
  soul-connector opcional (pywebview en Mac; en Linux respetar la detección
  de la extensión GNOME). El `tero` bash queda como wrapper de una línea o
  se borra. Exit code 3 de health se conserva.
- **1.7 [X] Catálogo de tools condicional** (decisión 2): cada módulo de
  tool declara `SUPPORTED = {"linux", "darwin"}`; `router.py` registra
  sólo las soportadas. En Mac quedan afuera de entrada `read_terminal` y
  `move_window_to_monitor`.
- **1.8 [M] Verificación manual en la Mac:** `uv sync`, `ollama pull`,
  Piper voice, `tero`, un turno "qué hora es", uno "cómo está el clima",
  uno "poné X" en Spotify. Registrar tiempos por etapa (objetivo CLAUDE.md:
  <1.5 s).

### Fase 2 — Paridad de herramientas en Mac

- **2.1 [X] `control_playback` vía Spotify Web API** como camino principal
  en ambos OS (ya hay token y scope `user-modify-playback-state`);
  `playerctl` queda como fallback Linux para reproductores no-Spotify.
- **2.2 [X] YouTube: backend configurable** (`browser`/`mpv`) según la
  sección de arriba. `_youtube_screen.py` se parte en `_youtube_browser.py`
  (portado: binario configurable, sin `wmctrl`, sin `/proc`, detección de
  proceso vía `psutil`) y `_youtube_mpv.py` (yt-dlp + mpv IPC).
  `youtube.current_state()` y `pause()` pasan por el backend.
- **2.3 [M] `read_terminal` en Mac** vía Accessibility (AXUIElement con
  pyobjc) para Terminal.app/iTerm2/Ghostty; si no expone texto, AppleScript
  para Terminal.app/iTerm2. Sin fallback de selección (no existe primary en
  macOS).
- **2.4 [X] `platformdirs`** para `~/.config/tero` → `user_config_dir
  ("tero")`, leyendo la ruta vieja si existe (no romper la instalación del
  hermano).
- **2.5 [M] Soul-connector pywebview en Cocoa:** sacar `QT_QPA_PLATFORM` y
  `wmctrl` del camino Mac; `on_top=True` nativo. `system_audio` deshabilitado
  en Mac con log claro.
- **2.6 [M] `launchd` plist** equivalente a `systemd/tero.service`
  (`KeepAlive=false`, `RunAtLoad` opcional).
- **2.7 [M] `install.sh` para Mac** (o `install.py` común): `brew install
  portaudio mpv yt-dlp` (mpv/yt-dlp sólo con backend mpv), `ollama`, Piper,
  permisos de Input Monitoring/Micrófono explicados.

### Fase 3 — Producto (después de que ande)

- **3.1 [X] Docs:** README con sección por OS, INSTALACIONES.md con tabla
  macOS (brew), CLAUDE.md/AGENTS.md actualizados con la capa de
  plataforma real.
- **3.2 [X] Tests mínimos sin hardware:** registry de tools, catálogo por
  plataforma, `Ducker` con un `Platform` fake, parsing de `config.toml`.
- **3.3 [X] Empaquetado:** `uv tool install`/`pipx` con entry point `tero`;
  evaluar `pyinstaller` más adelante.
- **3.4 [X] Fase 3 original (capture_screen)** con `Platform.capture_screen`
  (`grim`/`gnome-screenshot` en Linux, `screencapture` en Mac).
- **3.5 [M] Whisper fallback en Apple Silicon:** medir `faster-whisper`
  CPU vs `mlx-whisper`; elegir default por OS en config.

### Fuera de alcance

Windows (queda la tabla de diseño, sin implementar), extensión GNOME en
Mac, `delegate_to_codex` (fase 4 original, independiente de esto).

---

## Riesgos

- `pynput` en macOS necesita permiso de Input Monitoring al proceso que
  corre Python (el binario del venv). Si el daemon corre desde launchd el
  permiso se pide al `python` del venv; documentarlo, es la primera
  pregunta de soporte que va a aparecer.
- Rampa de ducking con `osascript`: cada llamada son ~20-40 ms; con `_STEP_S
  = 0.02` puede quedar atrás. Medir en 1.4; plan B CoreAudio.
- Chromium con perfil propio y `--remote-debugging-port` en Mac funciona,
  pero "background" real sin ventana sólo lo da `mpv`.
- `uv lock` con markers resuelve ambos OS en un solo lock; verificar que
  `nvidia-*` no arrastre nada a la resolución de darwin.
