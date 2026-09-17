# Tero

Asistente de voz de escritorio, activado por tecla. Corre como daemon en
segundo plano; el micrófono se abre solo mientras se mantiene apretada una
tecla dedicada (por defecto, **Control derecho**).

- Push-to-talk, sin wake word.
- La mayor parte del procesamiento corre local (Ollama, Piper, Whisper de
  respaldo) — sin cuota, sin depender de internet salvo para
  clima/música/mapas/Telegram y, opcionalmente, la transcripción (Groq).
- Español rioplatense de punta a punta.

Ver [`CLAUDE.md`](CLAUDE.md) para las decisiones de diseño y el estado de
cada fase, [`BITACORA.html`](BITACORA.html) para el historial de qué se
fue haciendo, e [`INSTALACIONES.md`](INSTALACIONES.md) para el detalle de
todo lo que se instaló a nivel sistema (con cómo revertirlo).

Probado en Ubuntu con GNOME/Wayland. Debería andar en cualquier distro con
PipeWire, pero los pasos de instalación de paquetes son para `apt`.

## Instalación

Hay un instalador que hace todos los pasos de abajo, explicando qué es
cada dependencia y para qué antes de instalarla:

```bash
./install.sh
```

Los pasos de Spotify y Telegram necesitan crear una cuenta/app a mano
(no se pueden scriptear), así que el instalador se detiene ahí y te
muestra exactamente qué hacer. El resto de esta sección es la misma
información para quien prefiera ir paso a paso.

### 1. Python (vía `uv`)

El proyecto fija Python 3.12 (`faster-whisper`/`evdev` no siempre tienen
wheels para versiones de Python muy nuevas). No hace falta tener 3.12
instalado a mano, `uv` lo resuelve solo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # si no tenés uv
uv sync
```

En macOS, `uv` sale por Homebrew (el script de arriba también anda):

```bash
brew install uv
uv sync
```

### 2. Paquetes de sistema

```bash
sudo apt install -y libportaudio2 playerctl wmctrl \
    libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1
```

| Paquete | Para qué |
|---|---|
| `libportaudio2` | Lib nativa de `sounddevice`, no viene en el wheel de PyPI |
| `playerctl` | Control de reproducción (play/pausa/siguiente) vía MPRIS |
| `wmctrl`, `libxcb-cursor0`, `libxcb-icccm4`, `libxcb-keysyms1` | Solo para el soul-connector **clásico** (overlay pywebview) — ver "El soul-connector" más abajo. El soul-connector como extensión de GNOME no los necesita |

### 3. Permisos de teclado

`evdev` necesita leer `/dev/input/event*` sin ser root:

```bash
sudo usermod -aG input $USER
```

**Hace falta un logout/login completo del escritorio** (no alcanza con
abrir una terminal nueva) para que el grupo nuevo tome efecto. Si después
de reloguear `id` no muestra `input`, probá un reboot completo.

### 4. Ollama + el modelo

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b-instruct
```

Se usa específicamente `qwen3:4b-instruct` y no `qwen3:4b` a secas: la
variante base agrega un bloque `<think>` a cada respuesta (15-25s de
latencia extra), la instruct no.

### 5. Voz (Piper)

```bash
uv run python -m piper.download_voices es_AR-daniela-high \
    --download-dir voice/models
```

### 6. Whisper (STT)

No necesita instalación aparte: `faster-whisper` descarga el modelo
(`large-v3` por defecto, configurable en `config.toml`) la primera vez que
se usa, desde HuggingFace. Con GPU tarda ~15s la descarga inicial y queda
cacheado.

### 6b. Groq (opcional, transcripción online)

Sin esto, Whisper local hace toda la transcripción y se carga al arrancar
(paso 6, arriba). Con una API key de Groq, la transcripción va primero por
**Groq** (mismo `whisper-large-v3`, gratis, sin tarjeta) y Whisper local
queda de respaldo, cargado recién si Groq falla — ver la sección "STT:
Groq online, Whisper local de respaldo" en `CLAUDE.md` para el detalle y
la contrapartida de privacidad (la voz sale de la máquina en cada pedido
mientras Groq esté disponible).

1. Creá una cuenta en https://console.groq.com (no pide tarjeta).
2. En **Settings → Data Controls**, activá **Global ZDR** (Zero Data
   Retention), para que no retengan el audio.
3. Generá una key en **API Keys** y guardala:

   ```bash
   mkdir -p ~/.config/tero
   read -rsp "Key de Groq: " K && printf '%s' "$K" > ~/.config/tero/groq_key
   chmod 600 ~/.config/tero/groq_key
   unset K
   ```

### 7. Spotify (opcional, para música real)

Sin esto, los pedidos de música fallan. Con esto, `play_music`
busca la canción real y la reproduce (no solo abre una búsqueda), y
`play_random_music` elige algo nuevo de "Tus me gusta" para los
pedidos genéricos ("poné música") en vez de repetir siempre lo último.
Ambas encolan varios temas más detrás del primero, así que "siguiente"
tiene a dónde avanzar.

1. Andá a https://developer.spotify.com/dashboard, creá una app (Web API
   únicamente).
2. En **Redirect URIs**, agregá exactamente `http://127.0.0.1:8942/callback`.
3. Copiá el **Client ID** (no hace falta el secret, se usa OAuth con PKCE)
   y pegalo en `config.toml`, sección `[spotify]`.
4. Login único:

   ```bash
   uv run python -m tools._spotify_auth
   ```

   Se abre el navegador, autorizás, y el refresh token queda guardado en
   `~/.config/tero/spotify_token.json` (permisos 600, fuera del repo). Si
   ya habías logueado antes y el scope pedido cambió (quedó registrado en
   `INSTALACIONES.md`), borrá ese archivo y volvé a loguearte — Spotify no
   re-pregunta por permisos nuevos si la app ya estaba autorizada.

Requiere **Spotify Premium** — la Web API no deja reproducir en cuentas
free (sí deja buscar).

### 8. Telegram (opcional, para mandar cosas al celular)

1. En Telegram, hablale a **@BotFather**, mandale `/newbot` y seguí las
   instrucciones. Te da un **token**.
2. Buscá tu bot recién creado y mandale cualquier mensaje (para que
   aparezca en `getUpdates`).
3. Conseguí tu `chat_id`:

   ```bash
   curl -s "https://api.telegram.org/bot<TU_TOKEN>/getUpdates"
   ```

   Buscá `"chat":{"id": ...}` en la respuesta.
4. Guardá ambos datos en `~/.config/tero/telegram.json`:

   ```bash
   mkdir -p ~/.config/tero
   cat > ~/.config/tero/telegram.json << 'EOF'
   {
     "token": "TU_TOKEN",
     "chat_id": TU_CHAT_ID
   }
   EOF
   chmod 600 ~/.config/tero/telegram.json
   ```

### 9. Configuración (`config.toml`)

Ya viene con valores razonables. Lo más probable que quieras ajustar:

- `[key] name` — cuál tecla activa la escucha (nombre evdev, ej.
  `KEY_RIGHTCTRL`, `KEY_PAUSE`).
- `[stt] model` — `large-v3` (preciso, más lento) vs `medium`/`small`
  (más rápido, se equivoca más con nombres propios). Con Groq configurado
  (paso 6b), esto es solo el respaldo offline; sin Groq, es la
  transcripción de siempre.

## Arrancar

```bash
./tero
```

Levanta todo: verifica las dependencias (Ollama y el modelo, token de
Spotify, binarios de sistema), arranca el daemon, espera a que carguen
Whisper y la voz, abre el soul-connector, y después muestra el log en
vivo. `Ctrl+C` corta el daemon y el soul-connector juntos.

```
Tero
  ✓ No hay otra instancia corriendo
  ✓ Ollama activo (modelo qwen3:4b-instruct)
  ✓ Spotify logueado
  ✓ Transcripción: Groq online (Whisper local de respaldo si falla)
  ✓ Binarios de sistema presentes
  … Arrancando el daemon (carga la voz; Whisper local solo si Groq falla)
  ✓ Voz cargada
  ✓ Daemon escuchando (tecla: KEY_RIGHTCTRL)
  ✓ Soul-connector: usando la extensión de GNOME (no hace falta la de pywebview)

  Todo listo. Ctrl+C para cortar todo.
```

(La última línea depende de cuál detecte: `Soul-connector en pantalla` si
usa el clásico, o un aviso si no levantó ninguno de los dos — nunca corta
el arranque, el soul-connector siempre es opcional.)

Se niega a arrancar si ya hay otro Tero corriendo: dos daemons cargan dos
veces Whisper `large-v3` en la GPU y el segundo muere con `CUDA failed
with error out of memory`.

Los logs quedan en `logs/` (ignorado por git): `tero.log` tiene el arranque
paso a paso más la salida del daemon (transcripción, qué herramienta se
llamó, tiempos de cada etapa), `soul_connector.log` el ruido de la
ventana. Cada corrida empieza un log nuevo y conserva el anterior como
`.1`.

Para tenerlo a mano desde cualquier lado:

```bash
ln -s "$PWD/tero" ~/.local/bin/tero   # opcional
```

### Arrancar cada pieza por separado

Para desarrollo, si querés correr solo una parte:

```bash
uv run python main.py                          # solo el daemon
QT_QPA_PLATFORM=xcb uv run python -m soul_connector.window   # solo el soul-connector clásico
```

(El soul-connector como extensión de GNOME no se lanza así: una vez
instalado, vive dentro de `gnome-shell` y anda solo.)

### El soul-connector (overlay opcional)

Onda animada que reacciona a la voz de Tero, al micrófono mientras
escucha, y a la música de fondo, más el nombre/progreso de lo que suena en
Spotify (se oculta sola si queda pausado 30s) y, mientras arranca, en qué
etapa de carga va. Es un cliente aparte, opcional — el daemon principal
funciona sin él, y `./tero` sigue adelante si no levanta.

Hay dos implementaciones, y `./tero` detecta sola cuál usar:

- **Extensión de GNOME** (`soul-connector-gnome/`, preferida si estás en
  GNOME): corre adentro de `gnome-shell`, que ya está en memoria, así que
  cuesta prácticamente nada (medido: por debajo del ruido de medición del
  propio `gnome-shell`) contra ~1,3 GB de RAM del soul-connector clásico.
  Se mueve con `Ctrl+Alt` + arrastrar. Instalación:

  ```bash
  cd soul-connector-gnome && ./install.sh
  ```

  Es un symlink a esta carpeta del repo + `gnome-extensions enable`. En
  Wayland, GNOME no relee extensiones nuevas hasta reiniciar la sesión
  (cerrar sesión y volver a entrar) — después de eso queda andando solo.
  `./uninstall.sh` lo saca. Detalle completo, incluidas las trampas de
  GNOME 50, en `soul-connector-gnome/README.md`.

- **Overlay clásico** (`soul_connector/`, pywebview + QtWebEngine):
  funciona en cualquier escritorio, no solo GNOME, a cambio de esos
  ~1,3 GB de RAM. Es el que usa `./tero` si no detecta la extensión de
  GNOME habilitada. El `QT_QPA_PLATFORM=xcb` (que `./tero` ya pone solo)
  es necesario en sesiones Wayland nativas: sin eso, la ventana no puede
  pedirle al gestor de ventanas que se quede "siempre encima" mientras
  habla.

## Herramientas disponibles

`get_weather`, `play_music`, `play_random_music`,
`control_playback`, `set_volume`,
`open_url`, `search_site` (mercadolibre/google/youtube/amazon/maps),
`get_trip` (distancia y ruta entre dos lugares), `read_terminal`,
`get_time`, `send_to_phone`, `play_youtube_channel`, `open_youtube`,
`suggest_youtube_channels`, `move_window_to_monitor`. Cada una es un
archivo de ~20-80 líneas en `tools/` — agregar una nueva no toca el
núcleo.
