# Tero

Asistente de voz de escritorio, activado por tecla. Corre como daemon en
segundo plano; el micrófono se abre solo mientras se mantiene apretada una
tecla dedicada.

Desarrollo en **Linux** (GNOME/Wayland) — el proyecto arrancó directo acá,
no hubo migración desde Windows pese a que secciones viejas de este
documento lo daban por planificado (ver `os_platform/linux.py`, ya escrito
y en uso; no existe `os_platform/windows.py`).

Idioma del asistente: **español** (rioplatense). Idioma del código:
**inglés** — identificadores, comentarios, docstrings y nombres de
archivo. Siguen en castellano, a propósito, los textos que Tero dice en
voz alta (los `return` de las herramientas), los mensajes de log y de
error, y el prompt del sistema (`brain/prompt.py`): son lo que define cómo
habla.

---

## Qué hace

1. Se aprieta una tecla → beep → graba mientras está apretada.
2. Se suelta → beep → transcribe (local).
3. Un modelo local chico elige una herramienta y la ejecuta.
4. Responde por voz (TTS local).
5. Una ventana overlay muestra el "soul-connector", una onda de audio, mientras habla.

Ejemplos de uso previstos:

- "Poné el disco negro de Metallica"
- "¿Cómo está afuera?" → "Hace frío, a las 3 parece que va a llover"
- "Buscame zapatillas adidas talle 44 en MercadoLibre"
- "Mirá este error de la consola, ¿qué puede ser? Arreglalo"

---

## Decisiones ya tomadas (no revisar sin motivo)

### Activación: push-to-talk, sin wake word

Se descartó la escucha continua a propósito. Sin wake word no hay falsos
disparos, no hay VAD, no hay detección de fin de turno, no hay sesión de
audio abierta consumiendo cuota. La tecla marca inicio y fin.

- Pulsación sostenida = push-to-talk (grabar mientras está apretada).
- Pulsación corta = toggle (para dictados largos).
- Tecla sugerida: una que no se use nunca (`Pause`, `Menu`, `ScrollLock`)
  o `Super+Espacio`.
- **Barge-in**: si se aprieta la tecla mientras Tero está hablando, corta
  el audio al toque y arranca a grabar de una, sin esperar a que termine
  la frase (`main.py`, `on_down`/`voice/tts.py`). Apretarla mientras todavía
  está transcribiendo/pensando (nada sonando todavía) se ignora a
  propósito, para no tener dos turnos procesándose en paralelo.

### Cerebro: modelo local + Codex como herramienta

**No hay un clasificador que decida entre local y nube.** El modelo local
ve el catálogo de herramientas, y una de ellas es `delegate_to_codex`. El
ruteo sale gratis del tool calling.

Regla dura: **Codex nunca contesta preguntas, solo hace trabajo sobre
archivos.** El prompt del sistema debe decirlo explícitamente. Mandarle
"¿cómo está el clima?" quema cuota y latencia.

### Restricción de presupuesto (importante)

El usuario paga ChatGPT Plus (20 USD/mes). **La suscripción no incluye
acceso a la API.** No hay presupuesto para API key de OpenAI.

La única vía oficial de usar la suscripción desde código es **Codex CLI
con login de ChatGPT** (`codex login`, después `codex exec` en modo no
interactivo). Está documentado por OpenAI.

Descartado explícitamente:

- Automatizar el navegador de ChatGPT o usar librerías no oficiales de la
  sesión web: viola los términos y arriesga la cuenta.
- Gemini: la suscripción tampoco da API, y Gemini CLI dejó de atender
  cuentas individuales el 18/06/2026 (reemplazado por Antigravity CLI).
  Fuera de alcance por decisión del usuario.

Por eso el **80% del uso diario debe correr local**: gratis, sin cuota,
sin depender de políticas de terceros.

### Contexto: bajo demanda, nunca continuo

Nada de capturar pantalla en bucle. En el instante exacto en que se aprieta
la tecla, el daemon captura:

- Ventana activa y su título (barato, siempre).
- Scrollback de terminal, si se habla de la consola.
- Captura de pantalla **solo** si se dice algo tipo "mirá esto".

Motivo de seguridad: el asistente ve terminal y pantalla, o sea contraseñas,
tokens de Vercel, conexiones a Neon, correos. No mandar eso a la nube sin
necesidad.

---

## Stack

| Pieza | Elección | Windows (nunca implementado) | Linux (real) |
|---|---|---|---|
| Audio in/out | `sounddevice` | igual | igual |
| STT | Groq (Whisper `large-v3` online) con `faster-whisper` local de respaldo, ver más abajo | igual | igual |
| Modelo local | Qwen3 4B Instruct vía Ollama | igual | igual |
| TTS | Piper (ONNX, voz es) | igual | igual |
| Tecla global | — | `pynput` | `evdev` |
| Ventana activa | — | `pygetwindow` / Win32 | `wmctrl` / D-Bus |
| Media | — | teclas multimedia | MPRIS (`playerctl`) + Web API de Spotify |
| Overlay | pywebview (Qt) + WebSocket | — | `QT_QPA_PLATFORM=xcb` (sin `gtk4-layer-shell`, no instalado) |

### STT: Groq online, Whisper local de respaldo

Decisión tomada el 2026-09-13, tras medir en vivo que un modelo chico mal
transcripto ("Poné música" → "Buena música.") le saca al cerebro toda
chance de acertar, sin importar cuán bien elija herramientas.

Con una API key de Groq en `~/.config/tero/groq_key` (ver
INSTALACIONES.md), cada pedido se transcribe primero con
`whisper-large-v3` en **Groq** (gratis, plan free, sin tarjeta): mismo
modelo y precisión que el local, sin ocupar los ~3,7 GB de VRAM que
Whisper se lleva de forma permanente. El plan gratis (2000 pedidos/día)
queda muy por encima de lo que genera un push-to-talk personal.

Whisper local (`faster-whisper`) queda como **respaldo**, cargado recién
la primera vez que Groq falla (sin key, sin red, límite alcanzado, error
del servidor) -- nunca al arrancar. Al fallar, Tero avisa por voz
("Pasando a modo offline, esperá que cargo Whisper") y sigue con el mismo
audio, sin pedir que se repita el pedido. Cuando Groq vuelve a responder,
suelta la referencia al modelo local (libera la VRAM) y avisa "Volví a
modo online". Implementado en `voice/stt.py` (`HybridSTT`). Sin key de
Groq, el comportamiento es el de siempre: 100% local, cargado al
arrancar.

**Contrapartida de privacidad, explícita:** mientras Groq esté disponible,
la voz de cada pedido sale de la máquina hacia sus servidores (no la
pantalla ni la terminal, ver más abajo). Se activó Zero Data Retention en
la cuenta para que no la retengan. Decisión del usuario, sabiendo esto —
lo que le importa proteger es la comprensión de frases libres, no el
conocimiento general del modelo (ver charla del 2026-09-13).
| Servicio | — | Task Scheduler | systemd user ✅ (`systemd/tero.service`, ver más abajo) |

`modelo small` se probó primero pero alucinaba nombres propios (Trelew,
artistas); se subió a `large-v3` a pedido explícito del usuario
("prefiero un modelo un poco más lento pero que funcione").

La fila de Windows es la tabla de diseño original — nunca se llegó a
escribir `os_platform/windows.py`, el desarrollo fue siempre en Linux.

---

## Estructura

```
tero/
  main.py            bucle principal
  health.py          vigilancia de RAM/VRAM/temperatura
  os_platform/       base.py, linux.py (no hay windows.py, ver más arriba)
  voice/             stt.py, tts.py
  brain/             router.py, prompt.py
  tools/             music.py, weather.py, web.py, maps.py, phone.py,
                     terminal.py, clock.py, volume.py, youtube.py,
                     move_window.py, _spotify_auth.py, _telegram.py,
                     _ducking.py, _youtube_screen.py,
                     _youtube_favorites.py, _read_terminal_atspi.py,
                     codex.py (fase 4)
  soul_connector/    server.py, window.py, system_audio.py, index.html,
                     siriwave.umd.js (vendorizada)
  soul-connector-gnome/  extension.js, wave.js, bar.js, move.js, link.js,
                     particles.js, panel.js
                     (misma onda, como extensión de GNOME Shell)
  config.toml
```

### Capa de plataforma (clave para la migración)

Una sola interfaz de cinco funciones. El resto del programa nunca sabe en
qué sistema corre. Migrar a Linux = escribir un archivo de ~150 líneas.

```python
# os_platform/base.py
class Platform:
    def listen_key(self, on_down, on_up): ...
    def active_window(self) -> dict:      ...
    def capture_screen(self) -> bytes:    ...
    def media(self, action: str):         ...
    def notify(self, text: str):          ...
```

El audio **no** entra en esta abstracción: `sounddevice` ya es
multiplataforma.

### Herramientas

Cada herramienta es una función con docstring y tipos. Un decorador genera
el esquema JSON que consume Ollama. Agregar una capacidad = un archivo de
~20 líneas, sin tocar el núcleo.

```python
tools = [
  play_music, play_random_music, control_playback,
  get_weather, open_url, search_site, set_volume,
  read_terminal, get_time, get_trip, send_to_phone,
  play_youtube_channel, open_youtube, suggest_youtube_channels,
  move_window_to_monitor,
  capture_screen, delegate_to_codex          # salida de escape
]
```

Catálogo completo ✅ salvo `capture_screen` (fase 3, atado a
`Platform.capture_screen`) y `delegate_to_codex` (fase 4).
`get_time` no estaba en el plan original: se agregó porque el modelo
local no tiene noción de reloj y "qué hora es"/"qué día es hoy" lo
necesitan. `get_trip` y `send_to_phone` tampoco estaban en el
plan original, surgieron de pedidos concretos del usuario (distancia a un
lugar + mandarle la dirección al celular).

Notas por herramienta:

- **Clima**: Open-Meteo. Sin clave, sin registro. Leer forecast horario y
  dejar que el modelo lo resuma en lenguaje natural.
- **Música**: reproducción real vía la Web API de Spotify (OAuth PKCE, ver
  `tools/_spotify_auth.py`), no solo abrir una búsqueda — necesario
  para que "poné X" realmente empiece a sonar X, y para que "siguiente"
  tenga una cola de verdad detrás. `play_music` busca y encola el
  resultado más varios favoritos al azar detrás; `play_random_music`
  es para pedidos genéricos ("poné música") y elige de "Tus me
  gusta" (scope `user-library-read`) en vez de repetir siempre lo mismo.
  `control_playback` usa `playerctl` (MPRIS) para play/pausa/siguiente/
  anterior sobre lo que ya esté sonando (Spotify, navegador, etc.) —
  requiere tenerlo instalado, no viene por defecto. Requiere Spotify
  Premium (la Web API no deja reproducir en cuentas free).
  **Música y YouTube se pausan mutuamente**: arrancar algo en
  `play_music`/`play_random_music` pausa la ventana de
  YouTube (Chrome expone cada ventana con media como reproductor MPRIS
  aparte, `chromium.instance<PID>` — ver `_youtube_screen.pause()`),
  y `play_youtube_channel` pausa Spotify puntualmente
  (`music.pause_spotify()`, apuntado a `-p spotify` a propósito, para
  no confundirse con el reproductor de la propia ventana de YouTube).
  **Ducking** (`tools/_ducking.py`, no es una herramienta del
  modelo): mientras Tero escucha/piensa/habla, **todo lo que esté
  sonando en el sistema** baja al 10% — progresivo, no de golpe, regla
  global desde el 2026-09-14 (no una lista de apps conocidas: empezó
  siendo solo Spotify, después se sumó a mano la ventana de YouTube, y
  terminó siendo "cualquier audio" a pedido explícito del usuario) — y
  vuelve solo al volumen real al terminar (no entre "pensando" y
  "hablando": si hay que hablar, se queda abajo hasta el final para no
  pegar un salto para arriba y otro para abajo antes de contestar). Se
  identifica cada stream activo (`state=="running"`) vía `pw-dump`,
  salvo el del propio proceso de Tero (TTS/beeps, por PID) para no
  duckearse a sí mismo. `Ducker` guarda a qué **PIDs** corresponde su
  estimación de volumen (no a qué ids de nodo de PipeWire, que pueden
  cambiar aunque sea la misma ventana — confirmado en vivo navegando por
  CDP) y fuerza releerlo si los PIDs activos cambiaron desde la última
  vez — sin esto, arrastraba el volumen duckeado viejo sobre un stream
  nuevo que en realidad arrancaba en su volumen real, dejándolo pegado
  bajo (bug real, visto dos veces con cambios de canal de YouTube a
  mitad de conversación). Dos vías descartadas en el camino para mover el
  volumen: `playerctl volume` no sirve porque el cliente de Spotify para
  Linux no implementa `SetVolume` vía MPRIS (éxito reportado, cero
  efecto real); la Web API de Spotify (`/me/player/volume`) sí cambia el
  volumen pero `/me/player/devices` tarda 1-3s en reflejarlo (eventual
  consistency), demasiado lento para una rampa. Lo que funciona: el
  propio volumen de cada stream de salida en PipeWire (`wpctl status` →
  "Streams", node id propio, no el sink del sistema) — instantáneo y no
  toca el sink que usa el TTS para salir.
- **Mapas**: `get_trip` geocodifica con Open-Meteo (misma API que el
  clima, sin clave) y calcula distancia/tiempo real con el servidor demo
  de OSRM (gratis, sin clave), devolviendo también la URL real de Google
  Maps para la ruta.
- **Celular**: `send_to_phone` manda texto/links al celular del
  usuario vía un bot de Telegram personal (`tools/_telegram.py`) —
  se eligió sobre GSConnect/Google Chat por simplicidad de setup.
- **YouTube**: `play_youtube_channel` abre en vivo el canal que el
  usuario nombre — **texto libre, no una lista fija** (`tools/
  youtube.py`). Empezó como un `Literal[...]` de siete canales
  hardcodeados y el usuario lo marcó como un antipatrón con razón: una
  lista cerrada no generaliza, ni el modelo puede llamar la herramienta
  con algo fuera del enum. Ahora `tools/_youtube_favorites.py`
  resuelve por aprendizaje: primero busca por parecido fonético
  (`difflib`) entre los canales ya conocidos (arranca con siete
  sembrados a mano, crece con el uso) — sin red, así "Bortegui" sigue
  resolviendo a "Vorterix" aunque la transcripción salga mal, cada vez
  mejor cuantas más veces se pida; si no hay nada parecido, busca en
  vivo en YouTube (scraping de resultados filtrados a canales, sin API
  key) y lo aprende para la próxima. Persistido en
  `~/.config/tero/youtube_channels.json`. Arranca solo con sonido gracias a
  `--autoplay-policy=no-user-gesture-required` (sin esto, Chrome bloquea
  el autoplay con sonido en un perfil sin historial de interacción, que
  es siempre el caso de este perfil dedicado). Sin canal nombrado, `open_youtube` abre la home y
  pregunta específico vs. novedades (única excepción a "nunca preguntar",
  ver `brain/prompt.py`); `suggest_youtube_channels` responde esa
  pregunta chequeando en vivo (`/live` de cada canal) y, de respaldo, el
  feed RSS por si subieron algo sin estar en vivo. Se abre siempre en una
  ventana de Chrome dedicada, fija en un monitor del escritorio del
  usuario (`tools/_youtube_screen.py`) — necesita forzar
  `--ozone-platform=x11` porque el Chrome nativo de Wayland no deja
  posicionar la ventana por código (ver detalle en `BITACORA.html`,
  2026-09-14). Un cambio de canal **navega la misma pestaña por CDP**
  (`--remote-debugging-port`, solo localhost) en vez de matar la ventana
  y abrir una nueva — así el stream de audio nunca cambia de identidad
  en PipeWire, que es justo lo que rompía el ducking al cambiar de canal
  a mitad de una conversación (ver más abajo). Si CDP falla, cae a abrir
  una ventana nueva.
- **Web / MercadoLibre**: **no** hacer un agente con navegador. El modelo
  arma la URL y se abre. Es instantáneo y no se rompe:
  `listado.mercadolibre.com.ar/zapatillas-adidas-talle-44`
  El agente con Playwright se reserva solo para lo que no se puede
  parametrizar por URL. `search_site` generaliza esto a mercadolibre/
  google/youtube/amazon/maps.
- **Terminal**: sin tmux a propósito (el usuario no quiere cambiar cómo
  labura por esto). Se lee por **AT-SPI** (accesibilidad de escritorio,
  `tools/_read_terminal_atspi.py`, corrido con el Python del
  sistema por subprocess porque PyGObject no está en el venv) si la
  ventana activa en ese instante expone un nodo de rol "terminal" — el
  caso de las terminales nativas de GTK/Qt (`ptyxis`, GNOME Terminal,
  Konsole). Verificado en vivo: funciona sin que el usuario copie nada.
  Las terminales de motor gráfico propio (Warp, Alacritty, Kitty) ni
  aparecen en el árbol de accesibilidad — probado con Warp, no aparece.
  Para esas, respaldo por **selección primaria** (`wl-paste --primary`/
  `xclip -selection primary`) — lo resaltado con el mouse, sin Ctrl+C. A
  propósito **no** se revisa el portapapeles de Ctrl+C: el usuario puede
  tener algo copiado ahí para otra cosa y no quiere que Tero se lo lleve
  puesto.

### `delegate_to_codex`

```python
def delegate_to_codex(task: str, directory: str) -> str:
    """Tareas sobre código o archivos del proyecto."""
```

Invoca `codex exec` en modo no interactivo, con `cwd` en el proyecto,
timeout generoso, salida capturada. Tres cuidados:

1. **Nunca destructivo sin confirmación.** Codex modifica archivos. Que
   corra sobre un repo con git limpio, o pedir confirmación hablada.
2. **Avisar que tarda.** El TTS dice "lo estoy viendo" al delegar, para que
   el silencio de 15 s no parezca que se colgó.
3. **Detrás de una interfaz.** Si OpenAI cambia algo, se reemplaza un solo
   archivo.

---

## El soul-connector (overlay) ✅

Es render, no IA. No hace falta sincronía labial ni fonemas. Implementado
con `pywebview` (backend Qt/QtWebEngine — no hay PyGObject en este
entorno, así que GTK no está disponible) renderizando
`soul_connector/index.html` (SiriWave vendorizada), y
`soul_connector/server.py` mandándole niveles/estado por WebSocket local
(`ws://127.0.0.1:8765`).

Hay una segunda implementación, `soul-connector-gnome/`: la misma onda
pero como extensión de GNOME Shell, corriendo adentro de `gnome-shell` en
vez de levantar un Chromium propio (~1,3 GB de RAM medidos vs. por debajo
del ruido de medición). `./tero` detecta sola cuál usar — ver
`soul-connector-gnome/README.md` para el detalle completo (geometría
medida, trampas de GNOME 50, arrastre con el mouse). Lo que sigue acá
describe la implementación original en pywebview.

- Señal: RMS real, no solo del TTS. Tres fuentes según el estado:
  el audio del TTS mientras habla, el **micrófono en vivo** mientras
  escucha (vía el callback de `sounddevice` en `Recorder`, con auto-gain
  contra el pico reciente de volumen — un multiplicador fijo no sirve
  porque el rms de un mic vive en una escala mucho más baja e
  impredecible que la del audio de TTS), y el audio de salida del sistema
  (PipeWire, `soul_connector/system_audio.py`) cuando no pasa nada más.
- **Suavizado asimétrico**: ataque rápido, decaimiento lento. Esto es lo
  que separa "se ve pro" de "se ve amateur". El RMS crudo tiembla.
- Ventana: sin bordes, sin foco. "Siempre encima" no es persistente bajo
  Mutter sin `gtk4-layer-shell` (no instalado): se fuerza
  `QT_QPA_PLATFORM=xcb` para que la ventana sea una ventana X11/XWayland
  real que `wmctrl` puede manipular, y se reintenta "traer al frente" en
  bucle mientras habla. Reposicionar por código (x/y de creación,
  `wmctrl -e`) no tiene ningún efecto en este entorno (confirmado); la
  única forma real de moverla es arrastrarla (`easy_drag=True`), y no hay
  forma de persistir esa posición entre reinicios del proceso.
- Colores por estado: la onda usa el estilo `"ios9"` de SiriWave, que
  ignora el color del constructor y trae sus curvas hardcodeadas en
  azul/rojo/verde — hay que recolorear las curvas a mano en cada cambio
  de estado (ver `applyState()` en `index.html`) para que el color
  realmente cambie, no alcanza con el `drop-shadow` de afuera.
- Cuatro estados visuales: **escuchando** (blanco, reactivo al mic),
  **pensando** (violeta claro), **hablando** (multicolor original de la
  librería — a pedido explícito del usuario, es el único estado que no
  se fuerza a un color plano), **música** (turquesa, reactivo al audio
  del sistema). Más un idle que respira.
- Debajo de la onda, nombre de la canción + barra de progreso de lo que
  suena en Spotify (polling cada ~5s, interpolado en cada frame). Se
  oculta sola si queda pausada 30s seguidos.

**El daemon tiene que funcionar sin el soul-connector.** La ventana es un
cliente opcional del stream de niveles.

---

## Servicio systemd y menú de GNOME ✅

`systemd/tero.service` (unidad de usuario, `Restart=no` a propósito —
mismo criterio que `health.py`: avisar, no revivir solo si cortó por algo
real) envuelve `./tero` sin duplicar su lógica de arranque/logging. Se
instala con un symlink:

```
ln -s ~/proyectos/tero/systemd/tero.service ~/.config/systemd/user/tero.service
systemctl --user daemon-reload
systemctl --user start tero      # arrancar ahora
systemctl --user enable tero     # opcional: arrancar solo en cada login (no activado por defecto)
```

Si la extensión de GNOME del soul-connector está activa,
`soul-connector-gnome/panel.js` agrega un toggle "Tero" al menú rápido de
GNOME (el de WiFi/Bluetooth/etc., arriba a la derecha) con Iniciar/
Reiniciar/Cerrar/Ver log, controlando este mismo servicio por
`systemctl --user` — relee el estado real cada 4s en vez de confiar en
el último click, así que si `health.py` corta a Tero solo o alguien lo
para desde una terminal, el toggle lo nota igual. Es una pieza aparte de
`extension.js` (se instancia en `enable()`/`disable()` junto con la
onda), no depende de que el soul-connector esté dibujándose.

---

## Latencia objetivo

Del beep a la primera sílaba, camino local:

| Etapa | Tiempo |
|---|---|
| STT (Groq online, medido: ~0,6–0,7 s; con Whisper `small` local se subió a `large-v3` por precisión, ver sección STT) | 0,3–0,8 s |
| Modelo local (tool call) | 0,3–0,6 s |
| Ejecución de herramienta | ~0 |
| TTS primer sonido (streaming) | 0,2 s |
| **Total** | **~1,5 s** |

Con GPU baja de 1 s. Solo CPU sube a 2–3 s, todavía usable. Si pasa de 4 s
se deja de usar: **medir desde el día uno.**

Camino Codex: 10–30 s. Por eso hay que avisar por voz.

---

## Salud del equipo

**Tero no puede quemar la placa de video.** El control térmico vive en el
firmware de la GPU, por debajo del sistema operativo: se frena sola
(slowdown) al llegar a su límite y se apaga sola si lo pasa. En esta
máquina (RTX 5060 Laptop, 8 GB) el límite de fábrica es 87 C, slowdown por
hardware 2 C arriba, apagado 5 C arriba. Ningún proceso puede desactivar
eso. No hace falta vigilar temperatura para proteger el hardware.

Los riesgos reales son de estabilidad de la sesión, no de hardware, y son
dos:

- **Quedarse sin RAM** — el único que puede colgar el equipo entero. Son
  14 GB, y Whisper `large-v3` + Ollama no son livianos. Con `MemAvailable`
  cerca de cero, el escritorio queda inusable swapeando bastante antes de
  que el OOM killer elija a alguien (y puede no elegir a Tero).
- **Quedarse sin VRAM** — pasó de verdad al arrancar un segundo daemon sin
  querer: murió con `CUDA out of memory`. No rompe nada, pero esta GPU
  además maneja el escritorio, así que la presión de VRAM lo pone lento.

`health.py` vigila esto en segundo plano (cada 5 s) con un criterio simple:
**la RAM corta, lo térmico solo avisa** (cortar por temperatura sería
redundante con lo que la placa ya hace sola; solo corta si el slowdown por
hardware se sostiene ~1 min, que ya habla de un problema de ventilación).
Para lo térmico se lee `clocks_throttle_reasons.hw_thermal_slowdown` y no
un umbral en grados hardcodeado: es la placa diciendo "estoy en mi
límite", con el límite real de esa placa. Toda condición exige varias
muestras seguidas — un pico puntual no corta una conversación a la mitad.
Al cortar, avisa por `notify-send` y por el log (nunca por voz: si falta
memoria, levantar el TTS para anunciarlo la empeora) y sale con código 3,
que `./tero` distingue de una caída de verdad.

## Seguridad

- Un agente con acceso a shell ejecutando lo que *entendió* de la voz es
  riesgo real. Un `rm` mal transcrito arruina el día.
- Whitelist estricta de comandos.
- Confirmación explícita para cualquier acción destructiva.
- Notificación con la transcripción antes de ejecutar, para ver qué entendió.

---

## Fases

1. **Esqueleto** ✅ — tecla, grabación, Whisper, TTS. Commit `630b3a4`.
2. **Cerebro** ✅ — Ollama + Qwen3 (`qwen3:4b-instruct`) con tool calling
   de punta a punta, multi-ronda (hasta 4 llamadas por turno para pedidos
   compuestos), memoria corta (últimos 2 turnos). Catálogo completo (ver
   sección de Herramientas más arriba). Commits `5eef997` y `bad3ed1`.

   **El historial va en el formato nativo de tool calling** (un mensaje
   `assistant` con `tool_calls` y uno `tool` con el resultado), y esto no
   es un detalle: antes se fabricaba una nota en prosa ("en el turno
   anterior usaste la herramienta X y el resultado fue: Y") y eso le
   enseñaba al modelo que a ese pedido se contesta *escribiendo*. Medido
   repitiendo "siguiente canción": **0/12 tool calls con la nota en
   prosa, 12/12 con el transcript nativo** — y sacarle el texto del
   resultado a la nota seguía dando 0/12, o sea no dependía de la
   redacción. Ese único bug se había estado tapando con parches de string
   en el router (detectar que el modelo "dijo" el nombre de la acción en
   vez de invocarla) que se rompían con cada frase nueva; se borraron
   todos, y las frases que los motivaban ahora pasan 8/8 sin ellos.
   Lección para el futuro: si el modelo chico empieza a portarse mal,
   sospechar primero de lo que Tero le está metiendo en el contexto,
   antes de culpar al sampling o de agregar otra regla al prompt.
3. **Contexto** — captura bajo demanda. `read_terminal` migrado a AT-SPI
   ✅ (ver Herramientas), sin ventana activa expuesta como dato aparte —
   se usa internamente solo para saber qué está enfocado, no se muestra
   a ningún lado. Falta `capture_screen`.
4. **Codex** — la rama pesada. No arrancado.
5. **Soul-connector** ✅ — overlay con WebSocket, ver sección dedicada más arriba.
6. **Linux** ✅ — `os_platform/linux.py` ya existe y funciona (desarrollo
   pasó a Linux desde el arranque del proyecto; no hay
   `os_platform/windows.py`).

Estado actual: **fases 1, 2, 5 y 6 completas y commiteadas.** Pendiente
para retomar:
- Fase 3 (Contexto): `read_terminal` ya migrado a AT-SPI (2026-09-13).
  Falta `capture_screen` (depende de `Platform.capture_screen`).
- `delegate_to_codex` (fase 4) sigue sin arrancar.

---

## Contexto del desarrollador

- Stack habitual: Next.js 15, TypeScript, Tailwind, Neon (PostgreSQL),
  Vercel. Este proyecto es Python, o sea territorio menos familiar.
- Preferencia por explicaciones concisas y directas.
- El overlay en WebView se eligió justamente para poder diseñar el
  soul-connector con canvas/CSS en lugar de pelear con Cairo u OpenGL.
