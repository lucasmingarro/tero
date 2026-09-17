"""Opening URLs and searching known sites: the model builds the URL, there
is no browser agent."""

import re
import urllib.parse
import webbrowser
from typing import Literal

from tools import tool


@tool
def open_url(url: str) -> str:
    """Opens a URL in the default browser."""
    webbrowser.open(url)
    return f"Abrí {url}."


def _slug(text: str) -> str:
    # Keeps accented vowels and ñ: the query comes from Spanish speech.
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9áéíóúñ ]", "", text)
    return re.sub(r"\s+", "-", text)


@tool
def search_site(
    site: Literal["mercadolibre", "google", "youtube", "amazon", "maps"], query: str
) -> str:
    """Searches `query` on the given site and opens the result in the browser."""
    if site == "mercadolibre":
        url = f"https://listado.mercadolibre.com.ar/{urllib.parse.quote(_slug(query))}"
    elif site == "youtube":
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    elif site == "amazon":
        url = f"https://www.amazon.com/s?k={urllib.parse.quote(query)}"
    elif site == "maps":
        url = f"https://www.google.com/maps?q={urllib.parse.quote(query)}"
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    webbrowser.open(url)
    # The real link goes in the result on purpose: if the user then asks
    # "mandalo al celular" or "pasame el link", the model has the actual
    # URL to copy instead of inventing an address from memory.
    return f"Abrí la búsqueda de {query!r} en {site}. URL: {url}"
