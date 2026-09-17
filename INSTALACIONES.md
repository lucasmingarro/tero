# Registro de instalaciones en el sistema

Todo lo que este proyecto agregó **fuera** del repo: paquetes de sistema,
servicios, cambios de permisos. El objetivo es poder revertir todo si el
proyecto se descarta, sin tener que recordar qué se tocó.

Lo que vive **adentro** del repo no está acá: dependencias Python van en
`pyproject.toml`/`uv.lock` y desaparecen solas al borrar la carpeta
(`uv` las instala en `.venv/`, nunca en el sistema).

Criterio a partir de ahora: antes de instalar algo, preferir lo que ya
viene en el sistema (D-Bus, portapapeles, etc.) o resolverlo en Python
puro. Instalar un paquete de sistema solo cuando de verdad no hay otra.

---

## Instalado

| Qué | Comando | Para qué | Cómo revertir |
|---|---|---|---|
| `libportaudio2` | `sudo apt install libportaudio2` | Lib nativa que necesita `sounddevice` (no viene en el wheel de PyPI para Linux) | `sudo apt remove libportaudio2` |
| Usuario en grupo `input` | `sudo usermod -aG input $USER` | Para que `evdev` lea `/dev/input/event*` sin ser root (tecla global) | `sudo gpasswd -d $USER input` (+ relogin) |
| Ollama | script oficial de instalación (`curl -fsSL https://ollama.com/install.sh \| sh`) | Servidor del modelo local (Qwen3), corre como servicio systemd (`ollama.service`, usuario/grupo propios `ollama`) | `sudo systemctl disable --now ollama`, borrar `/usr/local/bin/ollama`, `/usr/share/ollama`, `sudo userdel ollama` |
| Modelos `qwen3:4b-instruct` y `qwen3:4b` | `ollama pull qwen3:4b-instruct` | Cerebro del asistente (tool calling) | `ollama rm qwen3:4b-instruct qwen3:4b` (~5 GB en `~/.ollama/models`) |
| `playerctl` | `sudo apt install playerctl` | `control_playback`: play/pausa/siguiente vía MPRIS (D-Bus) | `sudo apt remove playerctl` |
| `wmctrl` | `sudo apt install wmctrl` | El soul-connector **clásico** (pywebview): pedirle a Mutter "siempre encima" (`-b add,above`) para la ventana, de forma más persistente que el flag `on_top` de Qt. El soul-connector como extensión de GNOME (`soul-connector-gnome/`) no lo necesita — vive en la capa de *chrome* del shell, siempre encima sin pedirlo | `sudo apt remove wmctrl` |
| `libxcb-cursor0`, `libxcb-icccm4`, `libxcb-keysyms1` | `sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1` | El soul-connector clásico: dependencias del plugin `xcb` de Qt, para correr vía `QT_QPA_PLATFORM=xcb` (XWayland) — la sesión es Wayland nativo, donde `wmctrl` no ve ninguna ventana | `sudo apt remove libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1` |
| Servicio systemd de usuario `tero.service` | `ln -s ~/proyectos/tero/systemd/tero.service ~/.config/systemd/user/tero.service && systemctl --user daemon-reload` | Arrancar/parar/reiniciar Tero por `systemctl --user` en vez de `pkill`/`nohup` a mano — es lo que usa el toggle "Tero" del menú rápido de GNOME (`soul-connector-gnome/panel.js`, 2026-09-14). No autoarranca en el login salvo que se corra además `systemctl --user enable tero` | `systemctl --user disable --now tero; rm ~/.config/systemd/user/tero.service` |

## Cuentas / apps externas registradas

No son paquetes, pero son rastro fuera del repo igual — si se descarta el
proyecto, esto queda huérfano si no se borra a mano:

| Qué | Dónde | Para qué | Cómo revertir |
|---|---|---|---|
| App "Tero" en Spotify | https://developer.spotify.com/dashboard (cuenta del usuario) | Client ID para la Web API (búsqueda + reproducción real, ver `tools/_spotify_auth.py`). Client ID guardado en `config.toml` (no es secreto, PKCE no lo necesita) | Borrar la app desde el dashboard |
| Token OAuth de Spotify | `~/.config/tero/spotify_token.json` (permisos 600) | Refresh token de la sesión logueada (scopes: `user-modify-playback-state`, `user-read-playback-state`, `user-library-read` -- este último para `play_random_music`, que elige de "Tus me gusta") | `rm ~/.config/tero/spotify_token.json` (+ opcionalmente revocar el acceso de la app desde la cuenta de Spotify) |
| Bot de Telegram "tero_asistente_bot" | Creado con @BotFather en la cuenta de Telegram del usuario | `send_to_phone`: mandar texto/links al celular (Samsung SM-A556E) del usuario, gratis, sin límites de uso personal | Borrar el bot hablándole a @BotFather (`/deletebot`), y `rm ~/.config/tero/telegram.json` |
| Cuenta en GroqCloud (console.groq.com) + API key | Cuenta del usuario, sin tarjeta. **Zero Data Retention (Global ZDR)** activado en Settings → Data Controls | STT online (`voice/stt.py`, `GroqSTT`): mismo `whisper-large-v3` que el respaldo local, gratis, plan free con 2000 pedidos/día -- muy por encima del uso real de un push-to-talk personal. Key guardada en `~/.config/tero/groq_key` (permisos 600), igual que el token de Spotify. Ojo: la voz sale de la máquina en cada pedido mientras Groq esté disponible (ver CLAUDE.md, sección Seguridad) | `rm ~/.config/tero/groq_key` (Tero cae solo a Whisper local, ver abajo) + revocar la key y borrar la cuenta desde la consola de Groq |

## Evaluado y descartado (para no repetir la discusión)

- **Controlador MPRIS propio en vez de `playerctl`**: técnicamente
  posible con `dbus-next`/`jeepney` (D-Bus puro, sin paquete de sistema),
  pero no elimina la dependencia real — el reproductor (Spotify, VLC, el
  navegador) tiene que hablar D-Bus/MPRIS igual, eso no se puede
  reemplazar. Se optó por `playerctl`: paquete estándar de Linux de
  escritorio (freedesktop.org), gratis, sin red, sin cuota — no es el
  tipo de dependencia externa que preocupa en este proyecto (esa
  preocupación es sobre servicios de terceros tipo OpenAI/Gemini, no
  sobre utilidades de sistema). Reabrir esta decisión si algún día
  `playerctl` da problemas reales.

## Ya presente en el sistema, no instalado por el proyecto

Por las dudas, para no confundir con lo de arriba: `wl-paste`, `xclip` y
`notify-send` (usados por `read_terminal` y `Platform.notify`) ya
venían con el escritorio GNOME/Wayland de esta máquina — no se instalaron
para Tero.

Lo mismo con `wpctl` (WirePlumber): viene con el stack de audio del
sistema. Lo usan `set_volume` y el ducking de música
(`tools/_ducking.py`), los dos sobre el sink por defecto
(`@DEFAULT_AUDIO_SINK@`). El ducking baja el maestro **solo mientras la
tecla está apretada** (la ventana en que el micrófono está abierto) y lo
devuelve al soltarla: nunca se solapa con la voz de Tero, que sale por
ese mismo sink. El diseño anterior bajaba el stream de Spotify por
separado y necesitaba además `pw-dump` para encontrarlo; se descartó el
2026-09-14 (el motivo está al principio de `tools/_ducking.py`) y
`pw-dump` ya no se usa.
