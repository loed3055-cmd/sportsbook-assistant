from http import HTTPStatus

from tests.test_grading import WEEK_2_2026

SEASON = 2026
AS_OF = "2026-09-17T18:43:00-04:00"

# Week 2 records (placeholder names; no pushes).
WEEK_2_RECORDS = {
    "edwin": (5, 11),
    "quinn": (8, 8),
    "ann": (5, 11),
    "pat": (8, 8),
    "mo": (7, 9),
    "rory": (8, 8),
}


def _prediction(matchup, favorite, spread, pick, source="edwin", week=2, **extra):
    return {
        "season": SEASON,
        "week": week,
        "matchup": matchup,
        "favorite": favorite,
        "spread": spread,
        "spread_as_of": AS_OF,
        "source": source,
        "pick": pick,
        **extra,
    }


def _week_2_results():
    return [
        {"season": SEASON, "week": 2, "matchup": m, "away_score": a, "home_score": h}
        for m, _fav, _spread, a, h, _winner in WEEK_2_2026
    ]


def _other_side(matchup, winner):
    away, home = matchup.split("@")
    return home if winner == away else away


def test_bulk_create_and_list_round_trip(client):
    payload = {
        "predictions": [
            _prediction(
                "atl@gb",
                "gb",
                -5.5,
                "Atl",
                week=3,
                source="Claude-Chat",
                confidence="High",
                reasoning="GB OL banged up.",
                retrieved_doc_ids=["doc-1", "doc-2"],
            ),
            _prediction("WAS@DAL", "DAL", -3.5, "was", week=3),
        ]
    }
    response = client.post("/predictions/", json=payload)
    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["created"] == 2 and body["updated"] == 0

    first, second = body["predictions"]
    assert first["matchup"] == "ATL@GB"
    assert (first["away_team"], first["home_team"]) == ("ATL", "GB")
    assert first["favorite"] == "GB"
    assert first["pick"] == "ATL"
    assert first["source"] == "claude-chat"
    assert first["confidence"] == "high"
    assert first["retrieved_doc_ids"] == ["doc-1", "doc-2"]
    assert first["result"] is None and first["ats_winner"] is None
    assert second["matchup"] == "WSH@DAL" and second["pick"] == "WSH"

    listed = client.get("/predictions/", params={"season": SEASON, "week": 3, "source": "claude-chat"})
    assert listed.status_code == HTTPStatus.OK
    rows = listed.json()
    assert [r["matchup"] for r in rows] == ["ATL@GB"]
    assert rows[0]["reasoning"] == "GB OL banged up."

    assert len(client.get("/predictions/", params={"season": SEASON, "week": 3}).json()) == 2
    assert client.get("/predictions/", params={"season": SEASON, "week": 4}).json() == []


def test_repost_updates_existing_pick(client):
    client.post("/predictions/", json={"predictions": [_prediction("ATL@GB", "GB", -5.5, "ATL", week=3)]})
    response = client.post(
        "/predictions/", json={"predictions": [_prediction("ATL@GB", "GB", -6.0, "GB", week=3)]}
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["created"] == 0 and body["updated"] == 1
    rows = client.get("/predictions/", params={"season": SEASON, "week": 3}).json()
    assert len(rows) == 1
    assert rows[0]["pick"] == "GB" and rows[0]["spread"] == -6.0


def test_pick_not_in_matchup_rejected(client):
    response = client.post(
        "/predictions/", json={"predictions": [_prediction("ATL@GB", "GB", -5.5, "DET")]}
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert client.get("/predictions/").json() == []


def test_favorite_not_in_matchup_rejected(client):
    response = client.post(
        "/predictions/", json={"predictions": [_prediction("ATL@GB", "KC", -5.5, "GB")]}
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_positive_spread_and_bad_confidence_rejected(client):
    positive = client.post("/predictions/", json={"predictions": [_prediction("ATL@GB", "GB", 5.5, "GB")]})
    assert positive.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    bad_conf = client.post(
        "/predictions/",
        json={"predictions": [_prediction("ATL@GB", "GB", -5.5, "GB", confidence="lock")]},
    )
    assert bad_conf.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_duplicate_in_batch_rejected(client):
    dupes = [_prediction("ATL@GB", "GB", -5.5, "GB"), _prediction("atl@gb", "GB", -5.5, "ATL")]
    response = client.post("/predictions/", json={"predictions": dupes})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert client.get("/predictions/").json() == []


def test_push_on_whole_number_line(client):
    client.post(
        "/predictions/",
        json={
            "predictions": [
                _prediction("PHI@TEN", "PHI", -7, "PHI", week=5, source="edwin"),
                _prediction("PHI@TEN", "PHI", -6.5, "PHI", week=5, source="claude-chat"),
            ]
        },
    )
    client.post(
        "/predictions/results",
        json={"results": [{"season": SEASON, "week": 5, "matchup": "PHI@TEN", "away_score": 27, "home_score": 20}]},
    )
    rows = {r["source"]: r for r in client.get("/predictions/", params={"week": 5}).json()}
    assert rows["edwin"]["result"] == "P" and rows["edwin"]["ats_winner"] == "PUSH"
    assert rows["claude-chat"]["result"] == "W" and rows["claude-chat"]["ats_winner"] == "PHI"

    table = client.get("/predictions/standings", params={"season": SEASON, "week": 5}).json()
    by_source = {s["source"]: s for s in table["standings"]}
    assert (by_source["edwin"]["wins"], by_source["edwin"]["losses"], by_source["edwin"]["pushes"]) == (0, 0, 1)


def test_results_upsert_and_list(client):
    first = client.post("/predictions/results", json={"results": _week_2_results()})
    assert first.status_code == HTTPStatus.OK
    assert len(first.json()) == 16
    client.post(
        "/predictions/results",
        json={"results": [{"season": SEASON, "week": 2, "matchup": "was@dal", "away_score": 21, "home_score": 37}]},
    )
    rows = client.get("/predictions/results", params={"season": SEASON, "week": 2}).json()
    assert len(rows) == 16
    wsh = next(r for r in rows if r["matchup"] == "WSH@DAL")
    assert (wsh["away_team"], wsh["away_score"]) == ("WSH", 21)


def test_week_2_2026_standings_match_pickem_sheet(client):
    """Picks built so each player hits their known week 2 record; standings must reproduce it."""

    predictions = []
    for source, (wins, _losses) in WEEK_2_RECORDS.items():
        for i, (matchup, favorite, spread, _a, _h, winner) in enumerate(WEEK_2_2026):
            pick = winner if i < wins else _other_side(matchup, winner)
            predictions.append(_prediction(matchup, favorite, spread, pick.lower(), source=source))

    assert client.post("/predictions/", json={"predictions": predictions}).status_code == HTTPStatus.OK

    # Before finals are in, everything is pending.
    pending = client.get("/predictions/standings", params={"season": SEASON}).json()
    assert all(s["pending"] == 16 and s["wins"] == 0 for s in pending["standings"])

    client.post("/predictions/results", json={"results": _week_2_results()})

    response = client.get("/predictions/standings", params={"season": SEASON, "week": 2})
    assert response.status_code == HTTPStatus.OK
    table = response.json()
    assert table["season"] == SEASON and table["week"] == 2
    records = {s["source"]: (s["wins"], s["losses"], s["pushes"], s["pending"]) for s in table["standings"]}
    assert records == {src: (w, l, 0, 0) for src, (w, l) in WEEK_2_RECORDS.items()}

    # Sorted by wins desc, then losses, then source name.
    assert [s["source"] for s in table["standings"]] == ["pat", "quinn", "rory", "mo", "ann", "edwin"]
    edwin = next(s for s in table["standings"] if s["source"] == "edwin")
    assert edwin["weeks"] == [{"week": 2, "wins": 5, "losses": 11, "pushes": 0, "pending": 0}]

    only_edwin = client.get("/predictions/standings", params={"season": SEASON, "source": "Edwin"}).json()
    assert [s["source"] for s in only_edwin["standings"]] == ["edwin"]


def test_standings_season_and_per_week_breakdown(client):
    client.post(
        "/predictions/",
        json={
            "predictions": [
                _prediction("DET@BUF", "BUF", -5.5, "BUF", week=2),  # W
                _prediction("GB@NYJ", "GB", -3.5, "GB", week=2),  # L
                _prediction("ATL@GB", "GB", -5.5, "ATL", week=3),  # W (GB wins by 3)
                _prediction("CAR@CLE", "CAR", -2.5, "CAR", week=3),  # pending, no final
            ]
        },
    )
    client.post(
        "/predictions/results",
        json={
            "results": [
                {"season": SEASON, "week": 2, "matchup": "DET@BUF", "away_score": 31, "home_score": 41},
                {"season": SEASON, "week": 2, "matchup": "GB@NYJ", "away_score": 20, "home_score": 17},
                {"season": SEASON, "week": 3, "matchup": "ATL@GB", "away_score": 20, "home_score": 23},
            ]
        },
    )
    table = client.get("/predictions/standings", params={"season": SEASON}).json()
    assert table["week"] is None
    (edwin,) = table["standings"]
    assert (edwin["wins"], edwin["losses"], edwin["pushes"], edwin["pending"]) == (2, 1, 0, 1)
    assert edwin["weeks"] == [
        {"week": 2, "wins": 1, "losses": 1, "pushes": 0, "pending": 0},
        {"week": 3, "wins": 1, "losses": 0, "pushes": 0, "pending": 1},
    ]


def test_standings_requires_season(client):
    assert client.get("/predictions/standings").status_code == HTTPStatus.UNPROCESSABLE_ENTITY
