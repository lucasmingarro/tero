# AGENTS.md

Working notes for AI agents on **Tero**, a push-to-talk desktop voice
assistant (Python 3.12, Linux-only today, macOS planned).

This file is *how to work in this repo*. `CLAUDE.md` is *why the project is
the way it is* — the design record, written in Spanish. Nothing here
repeats it:

| You need | Go to |
|---|---|
| Settled design decisions, tradeoffs | `CLAUDE.md` § Decisiones ya tomadas (no revisar sin motivo) |
| Per-tool notes (ducking, YouTube, Spotify, maps) | `CLAUDE.md` § Herramientas |
| Folder layout | `CLAUDE.md` § Estructura |
| What is still coupled to Linux | `CLAUDE.md` § Capa de plataforma (en expansión) |
| Latency budget per stage | `CLAUDE.md` § Latencia objetivo |
| Phase status | `CLAUDE.md` § Fases |
| Current roadmap (macOS port) | `docs/PLAN-MULTIPLATAFORMA.md` |
| History, session by session | `BITACORA.html` (chronological; old entries name files as they were called *then*, not as they are now) |
| System-level installs and how to undo them | `INSTALACIONES.md` |

## Layout, beyond `CLAUDE.md` § Estructura

That section covers the Python packages; the rest of the tree:

- `tero` — bash launcher: dependency checks, single instance (`flock`),
  daemon, log rotation, soul-connector detection. Gets rewritten in Python
  for the macOS port (plan § 1.6).
- `install.sh` — guided system install. `systemd/tero.service` — user unit.
- `docs/` — working plans. `logs/` and `voice/models/` are generated and
  gitignored.
- Inside `tools/`, a leading underscore means "not callable by the model"
  (`_ducking.py`, `_spotify_auth.py`, `_youtube_screen.py`): a helper. Only
  `@tool`-decorated functions reach the catalog the model sees.

## Language convention

**English:** identifiers, comments, docstrings, file and folder names,
`config.toml` keys.

**Rioplatense Spanish, on purpose — do not translate:**

- every string Tero says out loud, i.e. the `return` values of tools;
- log messages and error/exception messages;
- `brain/prompt.py` in full (the Spanish prompt *is* what makes Tero speak
  rioplatense; translating it would change the register of everything it
  says);
- user documentation: `README.md`, `INSTALACIONES.md`, `CLAUDE.md`,
  `BITACORA.html`;
- commit messages (match `git log`).

Three real examples:

```python
# tools/volume.py -- English name, signature and docstring; Spanish return
@tool
def set_volume(action: Literal["up", "down", "mute", "unmute"], percent: int = 10) -> str:
    """Turns the system volume up or down, mutes it or unmutes it. ..."""
    return f"Subí el volumen {percent}%."          # Tero says this out loud

# main.py -- log in Spanish
print(f"cerebro ({t2 - t1:.2f}s): {response!r}")

# os_platform/linux.py -- error message in Spanish, identifiers in English
raise RuntimeError("No se encontró ningún teclado en /dev/input. ...")
```

## Running it

Needs `uv` (Python 3.12 is pinned; `uv` resolves the interpreter itself).

```bash
uv sync
./tero          # everything: dep checks, daemon, soul-connector, live log. Ctrl+C stops all.
```

Each piece on its own, for development:

```bash
uv run python main.py                                        # daemon only
QT_QPA_PLATFORM=xcb uv run python -m soul_connector.window   # classic overlay only
uv run python -m tools._spotify_auth                         # one-time Spotify OAuth login
uv run python -m piper.download_voices es_AR-daniela-high --download-dir voice/models
```

The GNOME extension variant of the soul-connector is not launched like
this: `soul-connector-gnome/install.sh` symlinks it and it then lives
inside `gnome-shell`. Logs go to `logs/tero.log` (startup + daemon) and
`logs/soul_connector.log`, previous run kept as `.1`. `./tero` refuses to
start if another Tero is running: two daemons load Whisper `large-v3`
twice on the GPU and the second one dies with CUDA OOM.

## Verifying a change

There is no test suite yet (planned, `docs/PLAN-MULTIPLATAFORMA.md` § 3.2).
Until there is:

- `uv run python -c "import main"` must fail *only* on hardware/OS
  dependencies (`evdev`, CUDA), never on a broken import.
- Anything on the key → voice path must be timed in a real session: the
  daemon prints per-stage times (`transcripción`, `cerebro`) on every turn.
  Budget is ~1.5 s total — see `CLAUDE.md` § Latencia objetivo.
- Hardware paths (`evdev` key, PipeWire, CUDA, AT-SPI) cannot be exercised
  off Linux. Say what you could not verify instead of assuming it works.

## Adding a tool

1. New file `tools/<name>.py`, one function of ~20-80 lines: type hints,
   **English docstring** (the docstring *is* the description the model
   reads), **Spanish return string** (Tero says it out loud).
2. Decorate it with `@tool` from `tools/__init__.py`: the JSON schema is
   built from the signature, and `Literal[...]` becomes an enum.
3. Add `import tools.<name>  # noqa: F401` to `brain/router.py` —
   importing the module is what registers the tool.
4. Failures: return `"La herramienta 'x' falló: ..."` (or let the
   exception through: `execute()` wraps it in that same shape).
   `tools.is_error()` keys on that prefix, and the prompt tells the model
   never to invent a failure the tool did not report.

Adding a capability should not touch the core: no changes to `main.py`.

## Hard rules

- **No new system dependency without justifying it in
  `INSTALACIONES.md`** — what it is for, and how to undo it. Same bar for
  Python dependencies in `pyproject.toml`.
- **The daemon must work without the soul-connector.** `main.py` hosts the
  WebSocket server but every use of it is guarded (`_create_soul_connector`
  returns `None` on any failure and the turn goes on); the overlay itself
  is a separate optional process and `./tero` carries on if it does not
  come up. Never make a code path depend on it being there.
- **Never commit tokens or keys.** They live in `~/.config/tero/`, mode
  600, outside the repo: `groq_key`, `spotify_token.json`,
  `telegram.json`. The Spotify `client_id` in `config.toml` is public by
  design (PKCE, no secret).
- **Do not reintroduce string patches in the router** — detecting that the
  model "said" a tool name instead of calling it. Every one of them was
  deleted; the real cause was the history format (native tool-calling
  transcript: 12/12 vs 0/12 with a prose note). See `CLAUDE.md` § Fases,
  item 2.
- **Measure latency on every change to the key → voice path.** A change
  that improves nothing measurable is not worth the milliseconds it adds.
- Do not port GNOME-specific pieces to macOS. What is Linux-only stays
  Linux-only; see `docs/PLAN-MULTIPLATAFORMA.md` for what does get ported.
- The platform package is `os_platform/`, not `platform/`: that name would
  shadow the stdlib `platform` module. `create_platform()` raises
  `NotImplementedError` outside Linux, and new OS-specific behavior belongs
  in the platform layer, not in `tools/` — read `CLAUDE.md` § Capa de
  plataforma before adding a `subprocess` call to a tool.
