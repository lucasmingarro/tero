"""Soul-connector WebSocket server: broadcasts state
(idle/listening/thinking/speaking) and audio level (RMS) to whoever is
connected.

The daemon has to work without the soul-connector (see the phase README):
if no client is connected, or if the server could not even start (port
taken, etc.), emitting a state/level does nothing -- it never raises, it
never blocks the rest of Tero.

The message keys and the state names are the wire protocol with
soul_connector/index.html and soul-connector-gnome/extension.js: changing
one side means changing the others.
"""

import asyncio
import json
import threading

import websockets

PORT = 8765


class SoulConnectorServer:
    def __init__(self, port: int = PORT):
        self._port = port
        self._clients: set = set()
        # The last loading notice, to send it to whoever connects halfway
        # through: the GNOME soul-connector starts with the session and
        # connects on its own at any point during the daemon startup, and
        # without this it had no way of knowing Tero was still loading.
        self._current_loading: dict | None = None
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._ready.set()
        self._loop.run_forever()

    async def _serve(self) -> None:
        async def handle(ws) -> None:
            self._clients.add(ws)
            print("(soul-connector: cliente conectado)")
            if self._current_loading is not None:
                try:
                    await ws.send(json.dumps({"loading": self._current_loading}))
                except Exception:
                    pass
            try:
                await ws.wait_closed()
            finally:
                self._clients.discard(ws)
                print("(soul-connector: cliente desconectado)")

        try:
            await websockets.serve(handle, "127.0.0.1", self._port)
        except OSError:
            # Port taken (e.g. another instance of the daemon running): the
            # soul-connector simply has nobody to talk to, not fatal.
            pass

    def _broadcast(self, message: dict) -> None:
        if not self._clients:
            return
        data = json.dumps(message)

        async def _send_to_all() -> None:
            dead = set()
            for client in list(self._clients):
                try:
                    await client.send(data)
                except Exception:
                    dead.add(client)
            self._clients.difference_update(dead)

        asyncio.run_coroutine_threadsafe(_send_to_all(), self._loop)

    def state(self, name: str) -> None:
        """name: 'idle' | 'listening' | 'thinking' | 'speaking' | 'music'."""
        self._broadcast({"state": name})

    def level(self, value: float) -> None:
        """value: RMS of the audio being played, roughly 0.0-1.0."""
        self._broadcast({"level": value})

    def song(self, info: dict | None) -> None:
        """info: {"text", "progress_ms", "duration_ms", "playing"} or None if
        nothing is playing. The soul-connector interpolates the progress
        between updates."""
        self._broadcast({"song": info})

    def loading(self, text: str | None, progress: float = 0.0) -> None:
        """Startup notice ("Cargando voz", 0.33), or text=None once loading
        is done. The progress is by stage, not a measurement: neither
        faster-whisper nor Piper nor Ollama report progress while loading."""
        self._current_loading = None if text is None else {"text": text, "progress": progress}
        self._broadcast({"loading": self._current_loading})
