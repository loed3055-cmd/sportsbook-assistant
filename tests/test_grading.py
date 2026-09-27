import pytest

from app.services.grading import (
    PUSH,
    PickResult,
    ats_winner,
    canonical_matchup,
    grade_pick,
    normalize_team,
    parse_matchup,
)

# (matchup, favorite, spread, away_score, home_score, expected ATS winner)
# Week 2, 2026 finals with ESPN lines from examples/nfl_week_2_2026_pickem.csv.
WEEK_2_2026 = [
    ("DET@BUF", "BUF", -5.5, 31, 41, "BUF"),
    ("CAR@ATL", "CAR", -2.5, 34, 3, "CAR"),
    ("CIN@HOU", "HOU", -2.5, 20, 6, "CIN"),
    ("CLE@TB", "TB", -8.5, 23, 19, "CLE"),
    ("GB@NYJ", "GB", -3.5, 20, 17, "NYJ"),
    ("MIN@CHI", "CHI", -4.5, 9, 3, "MIN"),
    ("NO@BAL", "BAL", -7.5, 24, 17, "NO"),
    ("PHI@TEN", "PHI", -7.0, 24, 20, "TEN"),
    ("PIT@NE", "NE", -5.5, 3, 20, "NE"),
    ("JAX@DEN", "DEN", -2.5, 13, 20, "DEN"),
    ("LV@LAC", "LAC", -6.5, 26, 14, "LV"),
    ("MIA@SF", "SF", -13.5, 13, 35, "SF"),
    ("SEA@ARI", "SEA", -3.5, 31, 7, "SEA"),
    ("WSH@DAL", "DAL", -3.5, 20, 37, "DAL"),
    ("IND@KC", "KC", -6.5, 30, 33, "IND"),
    ("NYG@LAR", "LAR", -7.0, 6, 28, "LAR"),
]


@pytest.mark.parametrize("matchup,favorite,spread,away,home,expected", WEEK_2_2026)
def test_week_2_2026_ats_winners(matchup, favorite, spread, away, home, expected):
    away_team, home_team = parse_matchup(matchup)
    winner = ats_winner(
        away_team=away_team,
        home_team=home_team,
        favorite=favorite,
        spread=spread,
        away_score=away,
        home_score=home,
    )
    assert winner == expected


def test_week_2_2026_has_no_pushes():
    winners = [row[-1] for row in WEEK_2_2026]
    assert PUSH not in winners


@pytest.mark.parametrize(
    "fav_margin,spread,expected",
    [
        (7, -7.0, PUSH),  # whole-number line, exact margin -> push
        (3, -3, PUSH),
        (8, -7.0, "KC"),  # favorite covers
        (6, -7.0, "BUF"),  # favorite wins but doesn't cover
        (-3, -7.0, "BUF"),  # favorite loses outright
        (0, 0.0, PUSH),  # pick'em line, tie game
        (1, 0.0, "KC"),
    ],
)
def test_ats_winner_margin_rules(fav_margin, spread, expected):
    # KC is the home favorite; BUF the road underdog.
    winner = ats_winner(
        away_team="BUF",
        home_team="KC",
        favorite="KC",
        spread=spread,
        away_score=20,
        home_score=20 + fav_margin,
    )
    assert winner == expected


def test_spread_sign_is_ignored():
    kwargs = {"away_team": "PHI", "home_team": "TEN", "favorite": "PHI", "away_score": 24, "home_score": 20}
    assert ats_winner(spread=-3.5, **kwargs) == ats_winner(spread=3.5, **kwargs) == "PHI"


def test_road_favorite_covering():
    assert (
        ats_winner(away_team="CAR", home_team="ATL", favorite="car", spread=-2.5, away_score=34, home_score=3)
        == "CAR"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [("WAS", "WSH"), ("was", "WSH"), ("JAC", "JAX"), ("LA", "LAR"), ("det", "DET"), ("Car", "CAR"), (" gb ", "GB")],
)
def test_normalize_team_aliases_and_case(raw, expected):
    assert normalize_team(raw) == expected


def test_canonical_matchup_normalizes_both_sides():
    assert canonical_matchup("was@dal") == "WSH@DAL"
    assert canonical_matchup("Jac@Den") == "JAX@DEN"
    assert canonical_matchup("nyg@LA") == "NYG@LAR"


@pytest.mark.parametrize("bad", ["ATLGB", "ATL@GB@NO", "@GB", "GB@gb"])
def test_parse_matchup_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_matchup(bad)


def test_ats_winner_rejects_favorite_outside_matchup():
    with pytest.raises(ValueError):
        ats_winner(away_team="DET", home_team="BUF", favorite="KC", spread=-5.5, away_score=1, home_score=2)


def test_grade_pick():
    assert grade_pick("det", "DET") is PickResult.WIN
    assert grade_pick("was", "WSH") is PickResult.WIN
    assert grade_pick("BUF", "DET") is PickResult.LOSS
    assert grade_pick("BUF", PUSH) is PickResult.PUSH
