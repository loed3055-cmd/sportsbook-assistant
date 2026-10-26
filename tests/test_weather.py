"""Venue lookup and the NWS / Open-Meteo forecast service.

No network: ``forecast_for`` takes a ``urlopen`` and these tests pass a fake that
answers in the documented response shapes (NWS api.weather.gov /points and
hourly forecast GeoJSON; Open-Meteo /v1/forecast hourly arrays).
"""

import json
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.services import stadiums, weather
from app.services.stadiums import NEUTRAL_VENUES, STADIUMS, venue_for

UTC = timezone.utc
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)  # Sunday 1:00 PM ET

NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO",
    "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WSH",
}

NWS_FORECAST_URL = "https://api.weather.gov/gridpoints/BUF/40,58/forecast/hourly"

NWS_POINTS = {"properties": {"forecastHourly": NWS_FORECAST_URL, "gridId": "BUF"}}


def _nws_period(start, temp, wind, precip, short, gust=None):
    end = start + timedelta(hours=1)
    period = {
        # NWS uses local offsets; parsing must respect them.
        "startTime": start.astimezone(timezone(timedelta(hours=-4))).isoformat(),
        "endTime": end.astimezone(timezone(timedelta(hours=-4))).isoformat(),
        "temperature": temp,
        "temperatureUnit": "F",
        "windSpeed": wind,
        "windDirection": "W",
        "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": precip},
        "shortForecast": short,
    }
    if gust is not None:
        period["windGust"] = gust
    return period


NWS_HOURLY = {
    "properties": {
        "periods": [
            _nws_period(KICKOFF - timedelta(hours=1), 50, "8 mph", 10, "Cloudy"),
            _nws_period(KICKOFF, 48, "15 to 22 mph", 60, "Rain Showers Likely", gust="30 mph"),
            _nws_period(KICKOFF + timedelta(hours=1), 47, "12 mph", 40, "Chance Rain Showers"),
        ]
    }
}


def _om_times(center, hours=3):
    return [(center + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(-1, hours - 1)]


OPEN_METEO = {
    "latitude": 51.6,
    "longitude": -0.07,
    "hourly_units": {"temperature_2m": "°F", "wind_speed_10m": "mp/h"},
    "hourly": {
        "time": _om_times(KICKOFF),
        "temperature_2m": [55.0, 53.4, 52.0],
        "precipitation_probability": [5, 20, 35],
        "wind_speed_10m": [9.1, 11.2, 12.0],
        "wind_gusts_10m": [15.0, 21.7, 24.0],
    },
}


class FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]


class FakeWeatherAPI:
    def __init__(self):
        self.requests = []
        self.fail = None  # exception to raise on any request

    def urls(self):
        return [r.full_url for r in self.requests]

    def urlopen(self, req, timeout=None):
        self.requests.append(req)
        if self.fail is not None:
            raise self.fail
        url = urlparse(req.full_url)
        if url.netloc == "api.weather.gov" and url.path.startswith("/points/"):
            return FakeResp(NWS_POINTS)
        if req.full_url == NWS_FORECAST_URL:
            return FakeResp(NWS_HOURLY)
        if url.netloc == "api.open-meteo.com" and url.path == "/v1/forecast":
            return FakeResp(OPEN_METEO)
        raise AssertionError(f"unexpected url {req.full_url}")


@pytest.fixture
def api():
    return FakeWeatherAPI()


def _forecast(venue, api, kickoff=KICKOFF, now=NOW):
    return weather.forecast_for(venue, kickoff, now=now, urlopen=api.urlopen)


# --- venues -----------------------------------------------------------------


def test_every_nfl_team_has_a_home_stadium():
    assert set(STADIUMS) == NFL_TEAMS


def test_venue_is_the_home_team_stadium_with_alias_normalization():
    assert venue_for("buf@kc").name == "GEHA Field at Arrowhead Stadium"
    assert venue_for("DAL@WAS") is STADIUMS["WSH"]


def test_shared_stadiums():
    assert venue_for("DAL@NYG") is venue_for("MIA@NYJ")
    assert venue_for("SF@LA") is venue_for("KC@LAC")


def test_neutral_venue_overrides_home_team():
    assert venue_for("JAX@CLE", venue=" Tottenham ") is NEUTRAL_VENUES["tottenham"]


def test_unknown_venue_or_team_rejected():
    with pytest.raises(ValueError, match="unknown venue"):
        venue_for("JAX@CLE", venue="atlantis")
    with pytest.raises(ValueError, match="no stadium"):
        venue_for("JAX@XYZ")


def test_venue_coordinates_are_plausible():
    for venue in [*STADIUMS.values(), *NEUTRAL_VENUES.values()]:
        assert -90 <= venue.lat <= 90 and -180 <= venue.lon <= 180, venue
        assert venue.roof in ("open", "retractable", "dome"), venue
    assert all(v.country == "US" for v in STADIUMS.values())
    assert all(v.country != "US" for v in NEUTRAL_VENUES.values())


# --- provider selection -------------------------------------------------------


def test_us_venues_use_nws_and_international_use_open_meteo():
    assert weather.provider_for(STADIUMS["BUF"]) == "nws"
    assert weather.provider_for(NEUTRAL_VENUES["munich"]) == "open-meteo"


# --- forecasts ------------------------------------------------------------------


def test_dome_is_indoor_without_any_http(api):
    result = _forecast(STADIUMS["DET"], api)
    assert result.status == "indoor"
    assert result.provider is None and result.temp_f is None
    assert api.requests == []


def test_nws_forecast_picks_the_kickoff_hour(api):
    result = _forecast(STADIUMS["BUF"], api)
    assert result.status == "ok"
    assert result.provider == "nws"
    assert (result.temp_f, result.wind_mph, result.gust_mph, result.precip_prob) == (48, 22, 30, 60)
    assert result.short_forecast == "Rain Showers Likely"
    points_url = api.urls()[0]
    assert points_url == "https://api.weather.gov/points/42.7738,-78.787"
    # NWS requires an identifying User-Agent.
    assert api.requests[0].get_header("User-agent")


def test_nws_user_agent_from_env(api, monkeypatch):
    monkeypatch.setenv("WEATHER_USER_AGENT", "sportsbook-test (ops@example.com)")
    _forecast(STADIUMS["BUF"], api)
    assert {r.get_header("User-agent") for r in api.requests} == {"sportsbook-test (ops@example.com)"}


def test_retractable_roof_still_gets_a_forecast(api):
    result = _forecast(STADIUMS["DAL"], api)
    assert result.status == "ok" and result.provider == "nws"


def test_open_meteo_forecast_for_international_game(api):
    result = _forecast(NEUTRAL_VENUES["tottenham"], api)
    assert result.status == "ok"
    assert result.provider == "open-meteo"
    assert (result.temp_f, result.wind_mph, result.gust_mph, result.precip_prob) == (53.4, 11.2, 21.7, 20)
    assert result.short_forecast is None
    (req,) = api.requests
    query = {k: v[0] for k, v in parse_qs(urlparse(req.full_url).query).items()}
    assert query["temperature_unit"] == "fahrenheit"
    assert query["wind_speed_unit"] == "mph"
    assert query["timezone"] == "UTC"
    assert set(query["hourly"].split(",")) == {
        "temperature_2m", "precipitation_probability", "wind_speed_10m", "wind_gusts_10m"
    }


def test_open_meteo_picks_nearest_hour(api):
    result = _forecast(NEUTRAL_VENUES["tottenham"], api, kickoff=KICKOFF + timedelta(minutes=40))
    assert result.temp_f == 52.0


@pytest.mark.parametrize(
    "venue, days_out, status",
    [
        (STADIUMS["BUF"], 5, "ok"),
        (STADIUMS["BUF"], 10, "out_of_range"),  # beyond NWS hourly (~6 days)
        (NEUTRAL_VENUES["tottenham"], 10, "ok"),  # Open-Meteo reaches ~15 days
        (NEUTRAL_VENUES["tottenham"], 20, "out_of_range"),
    ],
)
def test_forecast_horizon(api, venue, days_out, status):
    result = _forecast(venue, api, now=KICKOFF - timedelta(days=days_out))
    assert result.status == status
    if status == "out_of_range":
        assert api.requests == []


def test_recent_kickoff_gets_the_current_hour_from_nws(api):
    # NWS hourly periods start at the current hour; a game that kicked off 2h
    # ago (inside GRACE) gets the current forecast rather than an error.
    result = _forecast(STADIUMS["BUF"], api, kickoff=KICKOFF - timedelta(hours=2), now=KICKOFF)
    assert (result.status, result.temp_f) == ("ok", 48)


def test_open_meteo_without_a_nearby_hour_is_an_error(api):
    result = _forecast(NEUTRAL_VENUES["tottenham"], api, kickoff=KICKOFF + timedelta(hours=5))
    assert (result.status, result.error) == ("error", "no_forecast_for_kickoff")


def test_past_kickoff_is_out_of_range(api):
    result = _forecast(STADIUMS["BUF"], api, now=KICKOFF + timedelta(days=1))
    assert result.status == "out_of_range"
    assert api.requests == []


@pytest.mark.parametrize(
    "exc, code",
    [
        (urllib.error.URLError("connection refused to proxy.internal:3128"), "network"),
        (urllib.error.HTTPError(NWS_FORECAST_URL, 503, "Service Unavailable", {}, None), "upstream_http_503"),
        (TimeoutError("timed out"), "timeout"),
    ],
)
def test_upstream_failure_is_an_error_code_not_raw_text(api, exc, code):
    api.fail = exc
    result = _forecast(STADIUMS["BUF"], api)
    assert (result.status, result.provider, result.error) == ("error", "nws", code)


def test_kickoff_hour_missing_from_nws_periods_is_an_error(api, monkeypatch):
    monkeypatch.setitem(NWS_HOURLY["properties"], "periods", [])
    result = _forecast(STADIUMS["BUF"], api)
    assert (result.status, result.error) == ("error", "no_forecast_for_kickoff")


def test_malformed_payload_is_bad_payload(api, monkeypatch):
    monkeypatch.setitem(NWS_POINTS, "properties", {})
    assert _forecast(STADIUMS["BUF"], api).error == "bad_payload"


@pytest.mark.parametrize(
    "forecast_url",
    [
        "file:///etc/passwd",
        "http://api.weather.gov/gridpoints/BUF/40,58/forecast/hourly",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_nws_forecast_url_must_be_https_api_weather_gov(api, monkeypatch, forecast_url):
    monkeypatch.setitem(NWS_POINTS["properties"], "forecastHourly", forecast_url)
    result = _forecast(STADIUMS["BUF"], api)
    assert (result.status, result.error) == ("error", "bad_payload")
    assert [r.full_url for r in api.requests if r.full_url == forecast_url] == []


def test_oversized_response_rejected(api, monkeypatch):
    monkeypatch.setattr(weather, "MAX_BODY_BYTES", 10)
    assert _forecast(STADIUMS["BUF"], api).error == "bad_payload"


def test_attribution_only_for_open_meteo(api):
    assert _forecast(STADIUMS["BUF"], api).attribution is None
    london = _forecast(NEUTRAL_VENUES["tottenham"], api)
    assert london.attribution == "Weather data by Open-Meteo.com (https://open-meteo.com/), CC BY 4.0"


@pytest.mark.parametrize(
    "raw, expected",
    [("10 mph", 10), ("5 to 15 mph", 15), ("0 mph", 0), ("", None), (None, None), ("calm", None)],
)
def test_parse_wind(raw, expected):
    assert weather.parse_wind_mph(raw) == expected


def test_naive_kickoff_rejected(api):
    with pytest.raises(ValueError, match="timezone"):
        _forecast(STADIUMS["BUF"], api, kickoff=KICKOFF.replace(tzinfo=None))


def test_stadiums_module_is_pure():
    # Like services/grading.py: reference data with no DB or framework imports.
    source = open(stadiums.__file__, encoding="utf-8").read()
    assert "sqlalchemy" not in source and "fastapi" not in source
