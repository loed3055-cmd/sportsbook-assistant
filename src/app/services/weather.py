"""Kickoff-hour forecasts at the game venue.

US venues use the National Weather Service (api.weather.gov: no key, but an
identifying User-Agent is required). International venues use Open-Meteo (no
key; free tier is for non-commercial use). Domes are reported as indoor and
never fetched.

Stdlib only (urllib), like scripts/make_pickem_sheet.py: no HTTP client in the
production dependencies. ``forecast_for`` never raises for upstream problems;
it returns a result with ``status="error"`` so one bad venue can't sink a
weekly refresh.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from app.services.stadiums import Venue

logger = logging.getLogger(__name__)

Provider = Literal["nws", "open-meteo"]
Status = Literal["ok", "indoor", "out_of_range", "error"]

NWS_BASE = "https://api.weather.gov"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HOURLY = "temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m"
DEFAULT_USER_AGENT = "sportsbook-assistant (https://github.com/edwin-lobo/sportsbook-assistant)"
OPEN_METEO_ATTRIBUTION = "Weather data by Open-Meteo.com (https://open-meteo.com/), CC BY 4.0"
# Only these hosts are ever fetched, including the forecast URL NWS hands back.
ALLOWED_HOSTS = frozenset({"api.weather.gov", "api.open-meteo.com"})
MAX_BODY_BYTES = 2_000_000

# NWS hourly forecasts cover ~156 hours; Open-Meteo serves 16 days with
# forecast_days=16. Stay a little inside both.
HORIZON: dict[str, timedelta] = {"nws": timedelta(days=6), "open-meteo": timedelta(days=15)}
# A game that kicked off within this window gets the current hour's forecast
# (NWS hourly data starts at the current hour, so the kickoff hour is gone).
GRACE = timedelta(hours=3)
TIMEOUT_S = 20

UrlOpen = Callable[..., object]


@dataclass(frozen=True)
class Forecast:
    status: Status
    provider: Provider | None = None
    temp_f: float | None = None
    wind_mph: float | None = None
    gust_mph: float | None = None
    precip_prob: float | None = None
    short_forecast: str | None = None
    # A stable code, never raw exception text: network | timeout |
    # upstream_http_<status> | bad_payload | no_forecast_for_kickoff | unexpected.
    error: str | None = None
    attribution: str | None = None


class NoForecast(Exception):
    """The provider answered, but has no data for the kickoff hour."""


def provider_for(venue: Venue) -> Provider:
    return "nws" if venue.country == "US" else "open-meteo"


def parse_wind_mph(raw: str | None) -> float | None:
    """NWS wind strings ("10 mph", "5 to 15 mph") -> the upper bound in mph."""

    numbers = re.findall(r"\d+(?:\.\d+)?", raw or "")
    if not numbers:
        return None
    value = max(float(n) for n in numbers)
    return int(value) if value.is_integer() else value


def forecast_for(
    venue: Venue,
    kickoff: datetime,
    *,
    now: datetime | None = None,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> Forecast:
    """Forecast at ``venue`` for the hour of ``kickoff`` (timezone-aware)."""

    if kickoff.tzinfo is None:
        raise ValueError("kickoff must be timezone-aware")
    if venue.roof == "dome":
        return Forecast(status="indoor")

    provider = provider_for(venue)
    now = now or datetime.now(timezone.utc)
    if not (now - GRACE <= kickoff <= now + HORIZON[provider]):
        return Forecast(status="out_of_range", provider=provider)

    fetch = _nws if provider == "nws" else _open_meteo
    try:
        return fetch(venue, max(kickoff, now), urlopen)
    except Exception as exc:  # noqa: BLE001 - one venue must not sink a refresh
        logger.warning("%s forecast failed for %s", provider, venue.name, exc_info=True)
        return Forecast(status="error", provider=provider, error=_error_code(exc))


def _error_code(exc: Exception) -> str:
    if isinstance(exc, NoForecast):
        return "no_forecast_for_kickoff"
    if isinstance(exc, urllib.error.HTTPError):
        return f"upstream_http_{exc.code}"
    if isinstance(exc, TimeoutError) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
    ):
        return "timeout"
    if isinstance(exc, OSError):  # URLError, connection errors
        return "network"
    if isinstance(exc, (KeyError, TypeError, ValueError, IndexError)):
        return "bad_payload"
    return "unexpected"


def _get_json(url: str, urlopen: UrlOpen, accept: str = "application/json") -> dict:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to fetch {parts.scheme}://{parts.hostname}")
    headers = {
        "User-Agent": os.getenv("WEATHER_USER_AGENT") or DEFAULT_USER_AGENT,
        "Accept": accept,
    }
    with urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S) as resp:
        body = resp.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("response body too large")
    return json.loads(body)


def _coord(value: float) -> str:
    # NWS redirects requests with more than 4 decimals or trailing zeros.
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _nws(venue: Venue, kickoff: datetime, urlopen: UrlOpen) -> Forecast:
    geo = "application/geo+json"
    points = _get_json(f"{NWS_BASE}/points/{_coord(venue.lat)},{_coord(venue.lon)}", urlopen, geo)
    hourly = _get_json(points["properties"]["forecastHourly"], urlopen, geo)
    for period in hourly["properties"]["periods"]:
        start = datetime.fromisoformat(period["startTime"])
        end = datetime.fromisoformat(period["endTime"])
        if start <= kickoff < end:
            temp = period.get("temperature")
            if temp is not None and period.get("temperatureUnit") == "C":
                temp = round(temp * 9 / 5 + 32, 1)
            return Forecast(
                status="ok",
                provider="nws",
                temp_f=temp,
                wind_mph=parse_wind_mph(period.get("windSpeed")),
                gust_mph=parse_wind_mph(period.get("windGust")),
                precip_prob=(period.get("probabilityOfPrecipitation") or {}).get("value"),
                short_forecast=period.get("shortForecast"),
            )
    raise NoForecast(f"NWS hourly forecast has no period covering {kickoff.isoformat()}")


def _open_meteo(venue: Venue, kickoff: datetime, urlopen: UrlOpen) -> Forecast:
    query = urllib.parse.urlencode(
        {
            "latitude": venue.lat,
            "longitude": venue.lon,
            "hourly": OPEN_METEO_HOURLY,
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "UTC",
            "forecast_days": 16,
        }
    )
    hourly = _get_json(f"{OPEN_METEO_URL}?{query}", urlopen)["hourly"]
    # timezone=UTC returns naive ISO hours ("2026-10-04T17:00") in UTC.
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
    if not times:
        raise NoForecast("Open-Meteo returned no hourly data")
    i = min(range(len(times)), key=lambda k: abs(times[k] - kickoff))
    if abs(times[i] - kickoff) > timedelta(hours=1):
        raise NoForecast(f"Open-Meteo has no hour near {kickoff.isoformat()}")
    return Forecast(
        status="ok",
        provider="open-meteo",
        temp_f=hourly["temperature_2m"][i],
        wind_mph=hourly["wind_speed_10m"][i],
        gust_mph=hourly["wind_gusts_10m"][i],
        precip_prob=hourly["precipitation_probability"][i],
        attribution=OPEN_METEO_ATTRIBUTION,
    )
