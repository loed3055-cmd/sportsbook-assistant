"""/weather endpoints: refresh (fetch + upsert) and list. No network."""

from datetime import datetime, timedelta, timezone
from http import HTTPStatus

import pytest

from app.routers import weather as weather_router
from tests.test_weather import KICKOFF, NFL_TEAMS, NOW, FakeWeatherAPI

SEASON, WEEK = 2026, 5


@pytest.fixture
def api(monkeypatch):
    fake = FakeWeatherAPI()
    monkeypatch.setattr(weather_router, "urlopen", fake.urlopen)
    monkeypatch.setattr(weather_router, "utcnow", lambda: NOW)
    return fake


def _refresh(client, *games, week=WEEK):
    return client.post(
        "/weather/refresh",
        json={"season": SEASON, "week": week, "games": list(games)},
    )


def _game(matchup, venue=None, kickoff=KICKOFF):
    game = {"matchup": matchup, "kickoff_utc": kickoff.isoformat()}
    if venue:
        game["venue"] = venue
    return game


def test_refresh_mixed_slate(client, api):
    response = _refresh(
        client,
        _game("mia@buf"),  # outdoor, NWS
        _game("GB@DET"),  # dome
        _game("JAX@CLE", venue="tottenham"),  # London, Open-Meteo
    )
    assert response.status_code == HTTPStatus.OK, response.text
    rows = {r["matchup"]: r for r in response.json()}
    assert set(rows) == {"MIA@BUF", "GB@DET", "JAX@CLE"}

    buf = rows["MIA@BUF"]
    assert (buf["status"], buf["provider"], buf["roof"]) == ("ok", "nws", "open")
    assert buf["venue_name"] == "Highmark Stadium" and buf["city"] == "Orchard Park, NY"
    assert (buf["temp_f"], buf["wind_mph"], buf["gust_mph"], buf["precip_prob"]) == (48, 22, 30, 60)

    det = rows["GB@DET"]
    assert (det["status"], det["provider"], det["roof"], det["temp_f"]) == ("indoor", None, "dome", None)

    london = rows["JAX@CLE"]
    assert (london["status"], london["provider"]) == ("ok", "open-meteo")
    assert london["venue_name"] == "Tottenham Hotspur Stadium"
    assert datetime.fromisoformat(london["kickoff_utc"]) == KICKOFF
    assert datetime.fromisoformat(london["fetched_at"]).utcoffset() == timedelta(0)


def test_non_utc_kickoff_is_stored_as_utc(client, api):
    eastern = KICKOFF.astimezone(timezone(timedelta(hours=-4)))  # 13:00-04:00
    (row,) = _refresh(client, _game("MIA@BUF", kickoff=eastern)).json()
    assert row["kickoff_utc"] in ("2026-10-04T17:00:00Z", "2026-10-04T17:00:00+00:00")


def test_refresh_twice_updates_in_place(client, api):
    _refresh(client, _game("MIA@BUF"))
    api.fail = OSError("upstream down")
    response = _refresh(client, _game("MIA@BUF"))
    (row,) = response.json()
    assert (row["status"], row["error"]) == ("error", "network")
    assert row["temp_f"] is None  # stale values cleared, not kept
    listed = client.get("/weather/", params={"season": SEASON}).json()
    assert len(listed) == 1

    api.fail = None
    (row,) = _refresh(client, _game("MIA@BUF")).json()
    assert (row["status"], row["error"], row["temp_f"]) == ("ok", None, 48)


def test_list_filters(client, api):
    _refresh(client, _game("MIA@BUF"), _game("GB@DET"))
    _refresh(client, _game("NE@NYJ"), week=WEEK + 1)

    assert len(client.get("/weather/", params={"season": SEASON}).json()) == 3
    assert [r["matchup"] for r in client.get("/weather/", params={"season": SEASON, "week": WEEK}).json()] == [
        "GB@DET",
        "MIA@BUF",
    ]
    only = client.get("/weather/", params={"season": SEASON, "matchup": "mia@buf"}).json()
    assert [r["matchup"] for r in only] == ["MIA@BUF"]


def test_list_rejects_bad_matchup(client):
    assert client.get("/weather/", params={"matchup": "MIABUF"}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_out_of_range_game_is_stored_without_fetching(client, api):
    far = NOW + timedelta(days=12)
    (row,) = _refresh(client, _game("MIA@BUF", kickoff=far)).json()
    assert row["status"] == "out_of_range" and row["provider"] == "nws"
    assert api.requests == []


@pytest.mark.parametrize(
    "game",
    [
        {"matchup": "MIA@XYZ", "kickoff_utc": KICKOFF.isoformat()},  # unknown home team
        {"matchup": "MIA@BUF", "kickoff_utc": KICKOFF.isoformat(), "venue": "atlantis"},
        {"matchup": "MIABUF", "kickoff_utc": KICKOFF.isoformat()},
        {"matchup": "MIA@BUF", "kickoff_utc": "2026-10-04T17:00:00"},  # naive
    ],
)
def test_refresh_rejects_bad_games(client, api, game):
    response = _refresh(client, game)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert api.requests == []


def test_refresh_caps_games_per_request(client, api):
    teams = sorted(NFL_TEAMS)
    games = [_game(f"{teams[i]}@{teams[i + 1]}") for i in range(21)]  # distinct, all valid
    response = _refresh(client, *games)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert api.requests == []


def test_open_meteo_rows_carry_attribution(client, api):
    rows = {r["matchup"]: r for r in _refresh(client, _game("MIA@BUF"), _game("JAX@CLE", venue="tottenham")).json()}
    assert rows["MIA@BUF"]["attribution"] is None
    assert "Open-Meteo.com" in rows["JAX@CLE"]["attribution"]


def test_refresh_rejects_duplicate_games(client, api):
    response = _refresh(client, _game("MIA@BUF"), _game("mia@buf"))
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
