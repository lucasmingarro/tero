# Soul Connector de Tero como extensión de GNOME Shell

La misma onda que `soul_connector/` (pywebview), pero dibujada adentro de
gnome-shell en vez de adentro de un Chromium propio.

**Por qué:** el soul-connector de pywebview se lleva ~1,3 GB de RAM
(medido) porque levanta QtWebEngine — un navegador entero — para dibujar
una onda de 260x74. Acá el dibujo corre en el proceso de gnome-shell, que
ya está en memoria.

**Medición del reemplazo**, comparando el PSS de gnome-shell con la
extensión habilitada y deshabilitada:

| | RAM |
|---|---|
| Soul-connector pywebview (QtWebEngine + Python) | ~1300 MB |
| Soul-connector extensión | **por debajo del ruido de medición** (±1,4 MB) |

El proceso de gnome-shell fluctúa más de lo que cuesta la extensión.

De yapa resuelve un problema viejo: el "siempre encima" nunca funcionó
bien bajo Mutter, y el soul-connector de pywebview lo peleaba llamando a
`wmctrl` en un bucle mientras Tero hablaba. Siendo parte del shell no hay
nada que pelear.

## Instalar

```bash
./install.sh
```

Hace un symlink desde `~/.local/share/gnome-shell/extensions/` a esta
carpeta del repo y habilita la extensión.

**GNOME Shell no relee las extensiones nuevas hasta reiniciarse, y en
Wayland eso significa cerrar sesión y volver a entrar.** Después de
reloguear ya queda andando sola.

## Sacarla

```bash
./uninstall.sh
```

Y se fue. Esto está pensado así desde el diseño:

- **No toca el daemon.** La extensión es otro cliente más del WebSocket de
  niveles (`ws://127.0.0.1:8765`), el mismo que ya consume
  `soul_connector/`. No hay una sola línea distinta en `main.py`,
  `soul_connector/server.py` ni en las herramientas. Volver a la rama
  `main` no requiere deshacer nada acá.
- **No instala nada a nivel sistema.** Ni paquetes, ni servicios, ni sudo.
  Lo único que deja fuera del repo es un symlink en el home y el uuid
  anotado en dconf; `uninstall.sh` borra las dos cosas.
- **El soul-connector de pywebview sigue intacto.** `soul_connector/` no
  se tocó: si sacás la extensión, `./tero` vuelve a levantarlo solo
  (detecta si la extensión está habilitada y en ese caso no lo levanta,
  para no tener dos ondas superpuestas).
- **Si la extensión falla, falla sola.** GNOME la deshabilita y sigue; no
  se lleva puesta la sesión.

## Archivos

| | |
|---|---|
| `extension.js` | Integración con el shell: widget flotante, estados, reproductor |
| `wave.js` | Port a Cairo del estilo `ios9` de SiriWave (la matemática de la onda) |
| `link.js` | Cliente WebSocket del stream de niveles del daemon |
| `stylesheet.css` | Tipografías y colores del nombre de canción y la barra |

## Moverlo

`Ctrl+Alt` + arrastrar con el mouse. La posición se guarda en
`~/.config/tero/soul_connector_position.json` y se respeta en el próximo
arranque, recortada al work area por si cambió la pantalla.

En GNOME 50 quién recibe un clic lo decide el *pick* de Clutter en ese
momento: actor reactivo bajo el puntero → el clic va al shell; si no,
pasa a la ventana de abajo. Por eso el soul-connector es reactivo **solo
mientras Ctrl+Alt está apretado con el puntero encima** (se consulta con
`global.get_pointer()` cada 30 ms). El resto del tiempo es atravesable.

Dos caminos que no funcionan, para no repetirlos:

- `captured-event` en `global.stage` con el actor no reactivo: el clic
  nunca llega, en Wayland va directo a la ventana.
- Leer el puntero dejando pasar el clic: la ventana de abajo recibe el
  Ctrl+Alt+arrastre, y tiling-assistant usa Ctrl y Alt durante un
  arrastre de ventana — la ventana se mueve en cuadrícula.

Ctrl+Alt y no Super porque Mutter ya usa Super+arrastrar para mover
ventanas.

## Desarrollo

**Nunca probar en la sesión real.** GNOME cachea el código de la
extensión y en Wayland recargarlo cuesta un logout. Se prueba en un shell
anidado — una ventana con un GNOME entero adentro, que arranca de cero:

```bash
./test.sh
```

Editar, correrlo, probar a mano en esa ventana (arrastre incluido),
repetir. Vuelve a abrir uno limpio cada vez. Solo al final se reloguea,
una vez, para pasarlo a la sesión de verdad.

Para cambios de dibujo ni siquiera hace falta eso: `./preview.js`
renderiza la onda y la barra a un PNG.

Ojo: en GNOME 50 la opción `--nested` ya no existe (era la de siempre en
las guías viejas); el modo anidado ahora es `--devkit`, y correr
`--wayland` sin `--display-server` intenta tomar el hardware y falla con
`Failed to take control of the session`.

Para ver si cargó bien, contra el bus de esa sesión anidada:

```bash
gdbus call --session --dest org.gnome.Shell.Extensions \
  --object-path /org/gnome/Shell/Extensions \
  --method org.gnome.Shell.Extensions.GetExtensionInfo "soul-connector@tero.local"
```

`'state': <1.0>` es habilitada y andando; `'error'` trae el mensaje si
algo se rompió.

### `disable` + `enable` NO recarga el código

La trampa más cara de todas, porque falla en silencio y con toda la pinta
de haber funcionado. GNOME cachea los módulos ES de la extensión: al
re-habilitarla vuelve a correr **el que quedó en memoria**, no el del
disco. No hay error, no hay warning, y el daemon hasta registra la
reconexión del WebSocket — pero la hace el código viejo.

O sea que esto **no sirve** para probar un cambio:

```bash
gnome-extensions disable soul-connector@tero.local && gnome-extensions enable soul-connector@tero.local
```

En Wayland la única forma de cargar código nuevo es cerrar sesión y
volver a entrar (o el shell anidado de más arriba, que sí arranca de
cero). Antes de gastar un logout conviene revisar el archivo entero: no
hay segunda oportunidad sin gastar otro.

Para confirmar qué versión quedó cargada, un `log()` al principio de
`enable()` y después:

```bash
journalctl --user --since "1 minute ago" -o cat | grep soul-connector
```

Ojo con `journalctl -f` redirigido a un archivo: se bufferea y puede
devolver cero líneas aunque el mensaje esté. Conviene consultar el
journal después del hecho, con `--since`, en vez de seguirlo en vivo.

## Estado

Verificado en GNOME Shell 50.1:

- Carga y se habilita sin errores ni warnings de JS.
- Sobrevive un ciclo completo de disable/enable (o sea `disable()` limpia
  bien: no deja timers ni actores colgados).
- Se conecta de verdad al daemon: con Tero corriendo, el `gnome-shell`
  anidado aparece con una conexión establecida al puerto 8765 y el daemon
  loguea el cliente nuevo.
- El render de la onda se validó aparte, dibujando a PNG con Cairo: sale
  el multicolor azul/rojo/verde del original.

Lo único que **no** está verificado es cómo se ve ubicado en pantalla
dentro de la sesión real — GNOME bloquea la API de screenshot para
llamadores no autorizados, así que eso hay que mirarlo a ojo después de
reloguear.
