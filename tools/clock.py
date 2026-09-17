"""Date and time: the local model has no clock and no idea what day it
is, so "qué hora es" or "qué día es hoy" need a tool. Day/month names are
hardcoded instead of using locale.setlocale: this is a long-lived
background daemon and setting the process locale is a global change that
could affect how other libs format numbers or dates.

Named clock.py, not time.py, to avoid confusion with the stdlib `time`.
"""

from datetime import datetime

from tools import tool

# Spoken out loud by Tero, so they stay in Spanish.
_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@tool
def get_time() -> str:
    """Returns the current date and time."""
    now = datetime.now()
    day = _DAYS[now.weekday()]
    month = _MONTHS[now.month - 1]
    return f"Son las {now.strftime('%H:%M')} del {day} {now.day} de {month}."
