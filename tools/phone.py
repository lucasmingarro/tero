"""Sending things to the phone through a private Telegram bot (see
tools/_telegram.py). Text and links only for now -- if photos or files are
ever needed, the Telegram API also has sendPhoto/sendDocument, and that
gets added as another tool when it is actually needed, not before.
"""

import httpx

from tools import _telegram, tool

_API = "https://api.telegram.org"


@tool
def send_to_phone(text: str) -> str:
    """Sends a text message (or a link) to the user's phone over Telegram.

    text: what to send, e.g. an address, a link, a note.
    """
    response = httpx.post(
        f"{_API}/bot{_telegram.token()}/sendMessage",
        json={"chat_id": _telegram.chat_id(), "text": text},
        timeout=10.0,
    )
    if response.status_code != 200:
        return f"La herramienta 'send_to_phone' falló: {response.text}"
    return "Listo, te lo mandé al celular."
