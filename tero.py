"""Brings up all of Tero: daemon + soul-connector. Ctrl+C stops both.

It checks the external dependencies before starting (Ollama, the model, the
Spotify token, system binaries) instead of letting each one fail later and
separately, inside the daemon, where the error shows up as a traceback in
the middle of a conversation.

The single-instance check is not decorative: two daemons at once load
Whisper large-v3 twice on the GPU and the second one dies with "CUDA failed
with error out of memory" (actually happened).

This used to be a bash script. It was rewritten in Python for the macOS
port: `flock`, `pgrep -f`, `readlink -f` and `wmctrl` are either missing or
different there, and keeping two launchers in sync is worse than one that
asks `sys.platform` in the four places where the OS matters. The `tero`
next to this file is now a one-line wrapper.
"""

import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import httpx
import psutil

from health import HEALTH_EXIT_CODE

ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"
LOG = LOGS_DIR / "tero.log"
LOG_SOUL_CONNECTOR = LOGS_DIR / "soul_connector.log"
LOCK = LOGS_DIR / ".lock"
CONFIG_PATH = ROOT / "config.toml"

DAEMON_WAIT_S = 180  # the first time, Whisper is downloaded from HuggingFace
SOUL_CONNECTOR_WAIT_S = 30

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
USER_CONFIG_DIR = Path.home() / ".config" / "tero"
SPOTIFY_TOKEN = USER_CONFIG_DIR / "spotify_token.json"
GROQ_KEY = USER_CONFIG_DIR / "groq_key"

# What each OS needs installed. Nothing here is fatal: a missing binary
# takes down one tool, not the daemon.
SYSTEM_BINARIES = {
    "linux": ("playerctl", "wpctl", "wmctrl", "notify-send"),
    "darwin": ("osascript",),
}

GNOME_EXTENSION = "soul-connector@tero.local"
SOUL_CONNECTOR_TITLE = "tero-soul-connector"

_IS_LINUX = sys.platform.startswith("linux")

if sys.stdout.isatty():
    C_OK, C_BAD, C_WARN = "\033[32m", "\033[31m", "\033[33m"
    C_DIM, C_END = "\033[2m", "\033[0m"
else:
    C_OK = C_BAD = C_WARN = C_DIM = C_END = ""


# --- output ---------------------------------------------------------------


def _to_log(text: str) -> None:
    with open(LOG, "a") as log:
        log.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")


def step(text: str) -> None:
    print(f"  {C_DIM}…{C_END} {text}")
    _to_log(f"-> {text}")


def ok(text: str) -> None:
    print(f"  {C_OK}✓{C_END} {text}")
    _to_log(f"ok: {text}")


def warn(text: str) -> None:
    print(f"  {C_WARN}!{C_END} {text}")
    _to_log(f"aviso: {text}")


def error(text: str) -> None:
    print(f"  {C_BAD}✗{C_END} {text}")
    _to_log(f"ERROR: {text}")


def _hint(command: str) -> None:
    print(f"      {C_DIM}{command}{C_END}")


def _print_tail(lines: int = 15) -> None:
    for line in LOG.read_text(errors="replace").splitlines()[-lines:]:
        print(f"      {line}")


# --- shutdown -------------------------------------------------------------

_daemon: subprocess.Popen | None = None
_soul_connector: subprocess.Popen | None = None
_already_killed = False
# The lock file stays open on purpose: the flock lives exactly as long as
# this process does.
_lock_file = None


def _terminate_tree(process: subprocess.Popen) -> None:
    # The real child (python) as well as the parent: `uv run` is a wrapper
    # and killing only it can leave the python orphaned and alive.
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    process.terminate()


def kill_all() -> None:
    # Called both from the Ctrl+C path and from the `finally` of main(), so
    # without this guard it would run twice and say goodbye twice.
    global _already_killed
    if _already_killed:
        return
    _already_killed = True
    print()
    for process in (_soul_connector, _daemon):
        if process is not None and process.poll() is None:
            _terminate_tree(process)
    for process in (_soul_connector, _daemon):
        if process is None:
            continue
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    _to_log("Tero cortado.")
    print(f"{C_DIM}Chau.{C_END}")


# --- single instance ------------------------------------------------------


def _take_lock() -> bool:
    global _lock_file
    _lock_file = open(LOCK, "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _daemon_outside_launcher() -> bool:
    """A daemon started by hand, outside this script: the lock does not see
    those (it is only held by this process) and two Whisper on the same GPU
    is exactly what has to be avoided.

    It looks for "a python running this repo's main.py", not for a
    particular interpreter path, because the path is not dependable: the
    bash version matched `$PWD/.venv/bin/python`, which on macOS never
    appears in the command line (the venv python shows up as the framework
    binary it re-executes, /opt/homebrew/.../Python.app/...), and did not
    match a relative `.venv/bin/python` either.
    """
    daemon_script = (ROOT / "main.py").resolve()
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.info["pid"] == os.getpid():
            continue
        cmdline = process.info["cmdline"] or []
        if len(cmdline) < 2 or "python" not in Path(cmdline[0]).name.lower():
            continue
        for argument in cmdline[1:]:
            if argument != "main.py" and not argument.endswith("/main.py"):
                continue
            script = Path(argument)
            if not script.is_absolute():
                try:
                    script = Path(process.cwd()) / script
                except psutil.Error:
                    continue
            if script.resolve() == daemon_script:
                return True
    return False


# --- dependency checks ----------------------------------------------------


def _ollama_tags() -> str | None:
    """Body of Ollama's /api/tags, or None if it does not answer."""
    try:
        response = httpx.get(OLLAMA_TAGS_URL, timeout=5.0)
    except Exception:
        return None
    return response.text if response.status_code == 200 else None


def _brain_model() -> str | None:
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)["brain"]["model"]
    except Exception:
        return None


def _check_ollama() -> bool:
    tags = _ollama_tags()
    if tags is None:
        error("Ollama no responde en localhost:11434.")
        if _IS_LINUX:
            _hint("Arrancalo con: systemctl start ollama")
        else:
            _hint("Arrancalo con: ollama serve   (o brew services start ollama)")
        return False
    model = _brain_model()
    if model and f'"{model}"' not in tags:
        error(f"Ollama anda pero no tiene el modelo '{model}'.")
        _hint(f"Bajalo con: ollama pull {model}")
        return False
    ok(f"Ollama activo (modelo {model})" if model else "Ollama activo")
    return True


def _check_optional_credentials() -> bool:
    """Warns about what is missing without stopping anything, and answers
    whether Groq is configured (which changes what the daemon loads at
    startup, and so what this script waits for)."""
    if SPOTIFY_TOKEN.exists():
        ok("Spotify logueado")
    else:
        warn("Spotify sin loguear: la música no va a andar (uv run python -m tools._spotify_auth)")

    groq = GROQ_KEY.is_file() and GROQ_KEY.stat().st_size > 0
    if groq:
        ok("Transcripción: Groq online (Whisper local de respaldo si falla)")
    else:
        warn(
            f"Sin key de Groq ({GROQ_KEY}): transcripción 100% local, "
            "se carga Whisper al arrancar"
        )
    return groq


def _check_system_binaries() -> None:
    expected = SYSTEM_BINARIES.get("linux" if _IS_LINUX else sys.platform, ())
    missing = [binary for binary in expected if shutil.which(binary) is None]
    if missing:
        warn(f"Faltan binarios de sistema: {' '.join(missing)} (ver INSTALACIONES.md)")
    else:
        ok("Binarios de sistema presentes")


# --- daemon ---------------------------------------------------------------


def _child_environment() -> dict[str, str]:
    """Environment for the daemon and the soul-connector.

    PYTHONUNBUFFERED because their stdout is a file, not a terminal: with
    block buffering the daemon's progress lines do not reach logs/tero.log
    until the buffer fills, and this script -- which waits for exactly those
    lines -- gives up after DAEMON_WAIT_S while the daemon is in fact fine
    (reproduced with a stub: four printed lines, none in the log). main.py
    only sets this on the CUDA re-exec path, which is a no-op outside Linux
    and does not run on Linux either when LD_LIBRARY_PATH is already set.
    """
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _start_daemon(groq: bool) -> subprocess.Popen:
    if groq:
        step("Arrancando el daemon (carga la voz; Whisper local solo si Groq falla)")
    else:
        step("Arrancando el daemon (carga Whisper y la voz, tarda unos segundos)")
    with open(LOG, "a") as log:
        return subprocess.Popen(
            ["uv", "run", "python", "main.py"],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=_child_environment(),
            cwd=ROOT,
        )


def _wait_for_daemon(process: subprocess.Popen) -> bool:
    """Follows the startup stages the daemon prints, so the wait shows what
    it is waiting for instead of a single silent pause."""
    stt_seen = tts_seen = llm_seen = False
    stt_local = False  # True if this run loaded local Whisper (not Groq)
    for _ in range(DAEMON_WAIT_S * 2):
        if process.poll() is not None:
            error("El daemon se murió al arrancar. Últimas líneas:")
            _print_tail()
            return False
        log = LOG.read_text(errors="replace")
        if not stt_seen and "STT: Groq online" in log:
            stt_seen = True
        if not stt_seen and "Cargando modelo de transcripción" in log:
            stt_seen = True
            stt_local = True
            step("Cargando el modelo de transcripción")
        if not tts_seen and "Cargando voz" in log:
            tts_seen = True
            if stt_local:
                ok("Modelo de transcripción cargado")
            step("Cargando la voz")
        if not llm_seen and "Cargando modelo de lenguaje" in log:
            llm_seen = True
            ok("Voz cargada")
            step("Cargando el modelo de lenguaje (recién reiniciada la máquina tarda)")
        if "Tero escuchando" in log:
            return True
        time.sleep(0.5)
    error(f"El daemon no terminó de arrancar en {DAEMON_WAIT_S}s. Últimas líneas:")
    _print_tail()
    return False


def _key_from_log() -> str | None:
    for line in LOG.read_text(errors="replace").splitlines():
        if "Tero escuchando" in line:
            match = re.search(r"Mantené (.*) para hablar", line)
            return match.group(1) if match else None
    return None


def _health_reason() -> str | None:
    reasons = [
        line.split("equipo: ", 1)[1]
        for line in LOG.read_text(errors="replace").splitlines()
        if "Cerrando Tero para cuidar el equipo: " in line
    ]
    return reasons[-1] if reasons else None


# --- soul-connector -------------------------------------------------------
# An optional client: if it does not come up, the daemon keeps working
# anyway (that is a project rule, not a coincidence). Which is why this
# section never exits with an error, it only warns.


def _gnome_extension_enabled() -> bool:
    """If the soul-connector is installed as a GNOME extension
    (soul-connector-gnome/), there is already one drawing itself inside
    gnome-shell: starting the pywebview one as well would put two
    overlapping waves on screen, and the heavier of the two."""
    if not _IS_LINUX:
        return False
    try:
        result = subprocess.run(
            ["gnome-extensions", "list", "--enabled"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return GNOME_EXTENSION in result.stdout.split()


def _start_soul_connector() -> subprocess.Popen:
    step("Arrancando el soul-connector (overlay)")
    LOG_SOUL_CONNECTOR.write_text("")
    environment = _child_environment()
    if _IS_LINUX:
        # Necessary in native Wayland sessions: without it the window cannot
        # ask the window manager to stay on top while Tero speaks.
        environment["QT_QPA_PLATFORM"] = "xcb"
    with open(LOG_SOUL_CONNECTOR, "a") as log:
        return subprocess.Popen(
            ["uv", "run", "python", "-m", "soul_connector.window"],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            cwd=ROOT,
        )


def _soul_connector_on_screen(waited_s: float) -> bool:
    if _IS_LINUX:
        result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, check=False)
        return SOUL_CONNECTOR_TITLE in result.stdout
    # macOS: no wmctrl, and pywebview says nothing from outside, so "it did
    # not die in the first seconds" is all there is to check. Today it does
    # die: the window asks for the Qt backend, which is only installed on
    # Linux (phase 2.5 of docs/PLAN-MULTIPLATAFORMA.md moves it to Cocoa).
    return waited_s >= 2.0


def _bring_up_soul_connector() -> subprocess.Popen | None:
    if _gnome_extension_enabled():
        ok("Soul-connector: usando la extensión de GNOME (no hace falta la de pywebview)")
        return None
    process = _start_soul_connector()
    started = time.monotonic()
    while time.monotonic() - started < SOUL_CONNECTOR_WAIT_S:
        if process.poll() is not None:
            break
        if _soul_connector_on_screen(time.monotonic() - started):
            ok("Soul-connector en pantalla")
            return process
        time.sleep(0.5)
    warn(
        "El soul-connector no levantó (el daemon sigue andando igual). "
        f"Ver {LOG_SOUL_CONNECTOR}"
    )
    return process


# --- live log -------------------------------------------------------------


def _follow_log() -> None:
    """Prints the daemon's log as it grows, which is what is worth watching
    while using it (the same as `tail -n 0 -f`, minus the process)."""
    with open(LOG, errors="replace") as log:
        log.seek(0, os.SEEK_END)
        while True:
            line = log.readline()
            if not line:
                time.sleep(0.2)
                continue
            sys.stdout.write(line)
            sys.stdout.flush()


# --- main -----------------------------------------------------------------


def _run() -> int:
    global _daemon, _soul_connector

    print(f"\n{C_DIM}Tero{C_END}")

    # The single-instance check goes BEFORE touching the log: if a second
    # run rotated the log and then rejected itself, it would leave the good
    # run with a truncated log, and that run waits forever for a line that
    # is no longer there (actually happened while testing this).
    if not _take_lock():
        error("Ya hay un Tero corriendo (lanzado con este mismo script).")
        return 1
    if _daemon_outside_launcher():
        error("Ya hay un daemon de Tero corriendo, lanzado a mano y por fuera de este script.")
        _hint(f'Cortalo con: pkill -f "{ROOT}/.venv/bin/python.* main.py"')
        return 1

    # A fresh log per run, keeping the previous one: if something just
    # failed, that run's log is not lost on the retry.
    if LOG.exists():
        LOG.replace(LOG.with_name(LOG.name + ".1"))
    LOG.write_text("")
    ok("No hay otra instancia corriendo")

    if not _check_ollama():
        return 1
    groq = _check_optional_credentials()
    _check_system_binaries()

    _daemon = _start_daemon(groq)
    if not _wait_for_daemon(_daemon):
        return 1
    ok("Modelo de lenguaje cargado")
    key = _key_from_log()
    ok(f"Daemon escuchando (tecla: {key})" if key else "Daemon escuchando")

    _soul_connector = _bring_up_soul_connector()

    print(f"\n  {C_OK}Todo listo.{C_END} Ctrl+C para cortar todo.")
    print(f"  {C_DIM}Log: {LOG}  ·  soul-connector: {LOG_SOUL_CONNECTOR}{C_END}\n")
    _to_log(
        f"Todo listo (daemon {_daemon.pid}, "
        f"soul-connector {_soul_connector.pid if _soul_connector else '—'})."
    )

    threading.Thread(target=_follow_log, daemon=True).start()

    # If the daemon dies on its own, do not leave the script hanging on a
    # dead log.
    exit_code = _daemon.wait()
    if _already_killed:
        # We got here from a Ctrl+C: not a crash, it is what was asked for.
        return 0
    if exit_code == HEALTH_EXIT_CODE:
        # The health monitor in health.py decided to close. Not a crash: the
        # reason is already explained in the log.
        print()
        warn("Tero se cerró solo para cuidar el equipo:")
        reason = _health_reason()
        if reason:
            print(f"      {reason}")
        return 0
    error("El daemon se cayó solo. Últimas líneas:")
    _print_tail()
    return 1


def _on_signal(*_) -> None:
    """The equivalent of the bash `trap kill_all INT TERM`: it closes the
    children and, above all, sets the guard flag from inside the handler, so
    the "the daemon died on its own" check below can never mistake a
    requested shutdown for a crash.

    Registering SIGINT explicitly is not redundant with the default
    KeyboardInterrupt: started as a background job of a non-interactive
    shell, this process inherits SIGINT already ignored, and in that case
    Python does not install a handler of its own -- the Ctrl+C would simply
    do nothing (it did, while testing this).
    """
    kill_all()
    sys.exit(0)


def main() -> int:
    os.chdir(ROOT)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # Line buffering so the progress shows up live even when stdout is a
    # pipe (systemd, or `./tero | tee`).
    sys.stdout.reconfigure(line_buffering=True)
    # SIGTERM is how systemd stops the service; SIGINT is the Ctrl+C, which
    # the terminal also delivers to the daemon (same process group, as with
    # the bash version).
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    try:
        return _run()
    except KeyboardInterrupt:
        return 0
    finally:
        kill_all()


if __name__ == "__main__":
    sys.exit(main())
