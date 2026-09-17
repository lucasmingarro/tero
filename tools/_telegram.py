"""Telegram config for sending things to the phone: internal helper, not a
tool (it is not decorated with @tool).

The bot is created by hand once with @BotFather (free, no limits for
personal use). Token and chat_id live in ~/.config/tero/telegram.json
(mode 600, outside the repo, never in git) -- the token is sensitive,
anyone holding it can send messages as the bot.
"""

import json
from pathlib import Path

_CONFIG_PATH = Path.home() / ".config" / "tero" / "telegram.json"


def _config() -> dict:
    if not _CONFIG_PATH.exists():
        raise RuntimeError(
            f"Falta {_CONFIG_PATH}: no se configuró el bot de Telegram todavía."
        )
    return json.loads(_CONFIG_PATH.read_text())


def token() -> str:
    return _config()["token"]


def chat_id() -> int:
    return _config()["chat_id"]
