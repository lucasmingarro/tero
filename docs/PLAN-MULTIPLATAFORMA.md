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
  Ajustes → Privacidad). `notify` con `osascript`. `get/set_master_volume`:
  medido (ver "Medido en macOS"), `osascript` cuesta ~450 ms por llamada y
  no sirve para la rampa → quedó CoreAudio por `ctypes`, sin pyobjc, con
  `Platform.volume_step_s = 0.05`. `available_ram_mb` con `psutil`.
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
- **2.8 [X] Que no se pierda el arranque de la frase.** Medido en la fase
  1: en macOS pasan 410-460 ms entre `on_down` y la primera muestra
  grabada, y con frases cortas Whisper transcribe un fragmento o inventa
  (ver "Defecto encontrado" en "Medido en macOS"). Opciones: beep sin
  `wait()`, abrir el `InputStream` antes del beep, o mantener el
  dispositivo de entrada abierto. Camino tecla → voz: medir en Linux y en
  Mac antes y después, como pide AGENTS.md.

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

## Medido en macOS

MacBook de Lucas, 2026-09-17, durante la fase 1. Está acá para no volver a
discutir en tres meses por qué el volumen no usa AppleScript.

### Volumen: por qué CoreAudio y no `osascript`

| Camino | Leer | Escribir |
|---|---|---|
| `osascript` (`get volume settings` / `set volume output volume N`) | 400 ms (mediana 326) | 450-500 ms (mediana 493) |
| CoreAudio por `ctypes` (`volm` scalar) | **0,25 ms** | mediana **3,9 ms**, con picos de 130-450 ms |

Promedios de 20 llamadas, restaurando el volumen original. Desglose de los
~450 ms de `osascript`: 2,5 ms son el `fork`+`exec`, 43 ms arrancar el
intérprete de AppleScript, y los ~400 ms restantes la operación de volumen
en sí (`get volume settings` consulta salida, entrada y alerta juntas).

Por eso **no** alcanzaba con subir `volume_step_s` y seguir con
`osascript`, como decía el plan original: con la rampa del ducking
(`_FADE_DOWN_FACTOR = 0.22`, ~13 pasos hasta 0,10) el fade de bajada
tardaría ~6 s, y la tecla se mantiene apretada 1-2 s. El ducking nunca
llegaría a hacer nada.

CoreAudio no agrega dependencia: `ctypes` es stdlib y
`CoreAudio.framework` es del sistema — no hace falta pyobjc, que era el
plan B anotado. Los picos de 130-450 ms (el sistema persistiendo el
cambio) se aceptan: la rampa corre en su propio hilo y trabaja sobre su
propia estimación, así que una escritura lenta alarga esa iteración y nada
más — no encola escrituras ni acumula pasos atrasados.

Medido con `volume_step_s = 0.05` (contra 0,02 en Linux): 10 pasos de
rampa tardan 1,11 s, o sea ~111 ms por paso. La bajada completa queda en
~1,4 s y la subida, que es deliberadamente lenta
(`_FADE_UP_FACTOR = 0.05`), en ~6 s. Si molesta, el ajuste es
`_FADE_UP_FACTOR`, no el mecanismo.

Salidas sin control de volumen (HDMI/DisplayPort, algunos AirPlay):
`master_volume()` devuelve `None`, el `Ducker` sale solo en el bootstrap y
se loguea una vez, no en cada tecla.

### AppleScript: lo que sí paga la pena

| Operación | Promedio de 10 |
|---|---|
| `display notification` (`Platform.notify`) | 128 ms (mediana 123) |
| `tell application "Spotify" to playpause` (`media`) | 193 ms (mediana 192) |

Son una llamada por turno, no por paso de rampa: entran cómodas en el
presupuesto y AppleScript es la forma más corta de decirlo.

### El loop completo, por etapa (verificación 1.8)

Turnos reales por voz, con Groq configurado, `qwen3:4b-instruct` local y
Piper `es_AR-daniela-high`:

| Etapa | Medido |
|---|---|
| Transcripción (Groq online) | **0,39-0,55 s** |
| Cerebro, respuesta directa sin tool | 0,55-0,77 s |
| Cerebro, con una tool | ~1,0 s |
| Cerebro, con dos tools en el mismo turno | 6,4 s (incluye ejecutar las dos) |
| TTS: síntesis | **0,32 s** (27 chars) a **1,04 s** (72 chars) |
| TTS: total que loguea el daemon | 3,0-5,6 s |
| Total por turno | 4,0-5,9 s |

El `tts (3.x s)` del log **no es latencia**: `speak()` sintetiza y
reproduce, así que incluye la duración del audio hablado (1,4 s para 27
chars, 4,4 s para 72). Lo que se paga como espera es la síntesis, 0,3-1,0
s. Mismo código que en Linux, no hay nada específico de Mac acá. Cargar el
modelo de voz: 0,75 s.

Arranque completo (`./tero`) con Groq configurado: ~25 s hasta "Tero
escuchando", casi todo Ollama cargando el modelo de lenguaje.

### Defecto encontrado: se pierde el arranque de cada frase

Con frases cortas ("qué hora es") la transcripción llega cortada o
directamente inventada por Whisper a partir del fragmento ("¡Buenos
días!"). Medido: desde `on_down` hasta que el micrófono realmente graba
pasan **410-460 ms**, y son todos evitables:

| Paso de `main.py::on_down` | Costo en macOS |
|---|---|
| `_beep(880)` (`sd.play` + `sd.wait`) | ~300 ms (80 ms de tono + ~220 ms de abrir el dispositivo de salida) |
| `sd.InputStream(...)` | ~73 ms |
| `.start()` | ~50 ms |

En Linux esto no se notó nunca, probablemente porque PipeWire abre los
dispositivos mucho más rápido. Candidatos de arreglo (ninguno aplicado, ver
tarea 2.8): reproducir el beep sin `wait()`, abrir el `InputStream` antes
del beep, o dejar el dispositivo de entrada abierto desde el arranque. Toca
el camino tecla → voz, que es compartido con Linux, así que hay que medirlo
en las dos plataformas.

### Lo que falló, y por qué

- **`play_music`: 403 de Spotify**, `"The user is not registered for this
  application"`. No es del port: la app cuyo `client_id` está en
  `config.toml` está en modo desarrollo y la cuenta de Spotify de esta Mac
  no está en su lista de usuarios. Se arregla agregando la cuenta en el
  dashboard de la app, o creando una app propia y cambiando el `client_id`.
  `get_weather` (22,4°C en Buenos Aires) y `get_time` sí funcionan por voz.
- **`soul_connector/system_audio.py` tira `FileNotFoundError: 'wpctl'`** en
  un hilo, y el traceback ensucia `logs/tero.log`. No rompe nada (el
  daemon sigue), y es exactamente lo que la tarea 2.5 prevé desactivar en
  Mac.
- **El soul-connector levantó igual**, sin cambios: pywebview loguea "QT
  cannot be loaded" y cae solo al backend Cocoa (WKWebView). La tarea 2.5
  es más chica de lo previsto: sacar `gui="qt"` y el `wmctrl`.

### Micrófono y tecla en macOS, para el instalador

- El **dispositivo de entrada por defecto** puede ser uno virtual (acá
  había `Meet Recording Input`, de Google Meet, y también BlackHole, Teams
  y Zoom): Tero graba del default del sistema, así que hay que elegir el
  micrófono real en Ajustes → Sonido → Entrada. Con el virtual, todo turno
  termina en `(audio en silencio)`.
- Los **headsets con noise gate** (acá un PRO X Wireless) emiten silencio
  digital exacto (rms 0,00000) cuando nadie habla: no confundir con falta
  de permiso.
- El permiso de **Micrófono** se puede consultar sin adivinar:
  `AVCaptureDevice.authorizationStatusForMediaType_("soun")` (3 =
  concedido).
- **Los teclados internos de MacBook no tienen Control derecho**: el
  default `ctrl_r` solo sirve con teclado externo. En el interno las
  candidatas son `alt_r` (Option derecho) y `cmd_r`.

### Whisper local (el respaldo, cuando Groq falla)

`large-v3` con `faster-whisper`/CTranslate2 en esta Mac: la primera vez
baja 3 GB de HuggingFace en 72 s, y después **carga en 5,8 s** con el
modelo ya cacheado — eso es lo que se paga en cada arranque que necesite
el respaldo. CTranslate2 avisa que convierte los pesos de float16 a
float32 porque el backend no tiene float16 eficiente: corre en CPU, no hay
GPU para esto en Mac.

Cuánto tarda en **transcribir** no se midió acá a propósito: es la fase
3.5 (`faster-whisper` CPU vs `mlx-whisper`, y elegir el default por OS),
no la fase 1. Con Groq configurado, este camino es solo el respaldo.

### Permiso de Monitoreo de entrada

`CGPreflightListenEventAccess()` (CoreGraphics por ctypes) responde si el
permiso está dado, sin tener que adivinar por "no llegan eventos", y
`CGRequestListenEventAccess()` hace que macOS muestre el diálogo y liste
el programa. Ojo con a quién se le da el permiso: macOS lo atribuye al
proceso responsable, que arrancando con `./tero` es **la terminal**, no el
`python` del `.venv` (con launchd sí es el python). El mensaje de
`os_platform/macos.py` dice las dos cosas e imprime la ruta real.

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
