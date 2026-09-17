"""Weather via Open-Meteo: no key, no signup."""

import httpx

from tools import tool

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@tool
def get_weather(city: str) -> str:
    """Looks up the current temperature and the chance of rain for a city."""
    geo = httpx.get(
        _GEOCODING_URL, params={"name": city, "count": 1, "language": "es"}, timeout=5.0
    ).json()
    results = geo.get("results")
    if not results:
        return f"No encontré ninguna ciudad llamada {city!r}."
    place = results[0]

    forecast = httpx.get(
        _FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m",
            "hourly": "precipitation_probability",
            "forecast_days": 1,
            "timezone": "auto",
        },
        timeout=5.0,
    ).json()

    temperature = forecast["current"]["temperature_2m"]
    probabilities = forecast["hourly"]["precipitation_probability"]
    current_hour = int(forecast["current"]["time"][11:13])
    upcoming = probabilities[current_hour : current_hour + 6]
    max_probability = max(upcoming, default=0)

    return (
        f"En {place['name']} la temperatura actual es {temperature}°C. "
        f"Probabilidad de lluvia en las próximas horas: hasta {max_probability}%."
    )
