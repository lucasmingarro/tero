"""Distance and route between two places: geocodes with the same
Open-Meteo API weather.py already uses (free, no key) and computes the
real route with the public OSRM demo server (free, no key, no signup).
It also opens the trip in Google Maps so it is visible on screen.
"""

import urllib.parse
import webbrowser

import httpx

from tools import tool

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def _geocode(place: str) -> tuple[float, float, str] | None:
    payload = httpx.get(
        _GEOCODING_URL, params={"name": place, "count": 1, "language": "es"}, timeout=5.0
    ).json()
    results = payload.get("results")
    if not results:
        return None
    found = results[0]
    return found["latitude"], found["longitude"], found["name"]


@tool
def get_trip(origin: str, destination: str) -> str:
    """Computes driving distance and travel time BETWEEN TWO different
    places, and opens the route in Google Maps. It is only for trips: if
    the user just wants to see where a single place is (no separate origin
    and destination), do not use this -- use search_site with site="maps"
    instead.

    origin and destination: city or place names, e.g. "Trelew", "Toay".
    """
    geo_origin = _geocode(origin)
    if geo_origin is None:
        return f"La herramienta 'get_trip' falló: no encontré {origin!r}."
    geo_destination = _geocode(destination)
    if geo_destination is None:
        return f"La herramienta 'get_trip' falló: no encontré {destination!r}."

    lat1, lon1, name1 = geo_origin
    lat2, lon2, name2 = geo_destination

    route = httpx.get(
        f"{_OSRM_URL}/{lon1},{lat1};{lon2},{lat2}",
        params={"overview": "false"},
        timeout=10.0,
    ).json()
    if route.get("code") != "Ok":
        return f"La herramienta 'get_trip' falló: no pude calcular la ruta entre {name1!r} y {name2!r}."

    distance_km = route["routes"][0]["distance"] / 1000
    duration_h = route["routes"][0]["duration"] / 3600

    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={urllib.parse.quote(origin)}&destination={urllib.parse.quote(destination)}"
    )
    webbrowser.open(url)

    return (
        f"De {name1} a {name2} hay {distance_km:.0f} km, unas {duration_h:.1f} horas en auto. "
        f"Ya abrí la ruta en Google Maps. URL: {url}"
    )
