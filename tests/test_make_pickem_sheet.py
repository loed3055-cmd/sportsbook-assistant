"""Tests for the shared, stdlib-only logic in scripts/make_pickem_sheet.py.

Nothing here touches the network (The Odds API is a fake urlopen), imports the `app`
package, or needs Postgres. Runs under `make test` or standalone:

    python -m pytest --noconftest -p no:cacheprovider tests/test_make_pickem_sheet.py
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
import urllib.error
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import make_pickem_sheet as mps  # noqa: E402


# Never read the developer's real macOS Keychain: tests that need a key pass one.
REAL_KEYCHAIN_SECRET = mps.keychain_secret


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    monkeypatch.setattr(mps, "keychain_secret", lambda service: None)

ET = mps.ET_ZONE
FIXED = ["week", "kickoff_et", "matchup", "favorite", "spread",
         "spread_source", "spread_as_of_et", "ats_winner"]


def load_grading():
    """src/app/services/grading.py by file path, so the `app` package is never imported."""
    path = REPO / "src" / "app" / "services" / "grading.py"
    spec = importlib.util.spec_from_file_location("_grading_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(week, matchup, ats, picks, kickoff="2026-09-13 13:00 ET"):
    away, home = matchup.split("@")
    return [str(week), kickoff, matchup, home, "-3.5", "DraftKings", "", ats, *picks]


def pool_fixture():
    """A small pool: weeks 1-2 of 2026 plus a stale 2025 tab and a Sheet1 tab."""
    h1 = FIXED + ["Ann - The Alpha (140-100)", "Bo - The Bravo (120-120)",
                  "Cy - The Charlie (90-89; 62 missed)"]
    h2 = FIXED + ["Ann - The Alpha (2-1)", "Bo - The Bravo (1-1; 1 missed)",
                  "Cy - The Charlie (0-1)"]
    week1 = [
        h1,
        # Ann W, Bo W (messy case/space), Cy L
        row(1, "NE@SEA", "NE", ["NE", "Ne ", "SEA"]),
        # WAS/WSH alias on both sides: Ann W, Bo L, Cy W via alias
        row(1, "WAS@PHI", "WAS", ["WSH", "PHI", "was"]),
        # Push: nobody gets a W or L. Bo missed (blank), not a loss.
        row(1, "DEN@KC", "PUSH", ["KC", "", "DEN"]),
        # "NY" resolves to NYJ by prefix within the row; "dale" never guessed.
        row(1, "NYJ@TEN", "TEN", ["NY", "TEN", "dale"]),
    ]
    week2 = [
        h2,
        row(2, "DET@BUF", "BUF", ["BUF", "DET", "buf"]),
        row(2, "CAR@ATL", "", ["ATL", "CAR", "atl"]),   # ungraded
        row(2, "CIN@HOU", "", ["CIN", "CIN", "cin"]),   # ungraded
    ]
    week3_header_only = [FIXED + ["Ann - The Alpha (9-9)", "Bo - The Bravo (9-9)",
                                  "Cy - The Charlie (9-9)"]]
    return {
        "Sheet1": [["notes"]],
        "nfl_week_18_2025_pickem": [FIXED + ["Old - Player (1-1)", "Bo - The Bravo (1-1)",
                                             "Cy - The Charlie (1-1)"],
                                    row(18, "NE@SEA", "SEA", ["SEA", "SEA", "SEA"])],
        "nfl_week_1_2026_pickem": week1,
        "nfl_week_2_2026_pickem": week2,
        "nfl_week_3_2026_pickem": week3_header_only,
    }


# --- stdlib-only guard ----------------------------------------------------------


def test_generator_imports_only_the_standard_library():
    tree = ast.parse((REPO / "scripts" / "make_pickem_sheet.py").read_text())
    top = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            top.add(node.module.split(".")[0])
    top.discard("__future__")
    assert top <= set(sys.stdlib_module_names), top - set(sys.stdlib_module_names)


def test_no_pool_specific_values_are_hard_coded():
    source = (REPO / "scripts" / "make_pickem_sheet.py").read_text()
    push_source = (REPO / "scripts" / "push_pickem_to_sheets.py").read_text()
    for text in (source, push_source):
        assert "2PACX-1v" not in text
        assert "nfl_pickem_2026_2027_full_season" not in text
        assert "The Favorite" not in text
    assert not hasattr(mps, "LIVE_SHEET_CSV")


def test_nothing_defaults_into_examples_dir():
    """examples/ is gitignored: no default path may point into it."""
    for name in ("DEFAULT_SCHEDULE", "HEADER_SOURCE"):
        value = getattr(mps, name, None)
        assert value is None or "examples" not in str(value)
    source = (REPO / "scripts" / "make_pickem_sheet.py").read_text()
    assert '"examples"' not in source and "'examples'" not in source


def test_published_url_comes_only_from_env_without_default(monkeypatch):
    assert mps.ENV_SHEET_URL == "PICKEM_PUBLISHED_CSV_URL"
    monkeypatch.delenv(mps.ENV_SHEET_URL, raising=False)
    assert mps.configured_sheet_url() is None
    monkeypatch.setenv(mps.ENV_SHEET_URL, "  ")
    assert mps.configured_sheet_url() is None


# --- team codes -----------------------------------------------------------------


def test_team_aliases_mirror_grading_service():
    grading = load_grading()
    assert mps.TEAM_ALIASES == grading.TEAM_ALIASES
    for code in ["WAS", "was", " Wsh ", "JAC", "jax", "LA", "la", "det", " Ne "]:
        assert mps.normalize_team(code) == grading.normalize_team(code)


@pytest.mark.parametrize("cell,away,home,expected", [
    ("Ne ", "NE", "SEA", "NE"),
    ("NE", "NE", "SEA", "NE"),
    ("NY", "NYJ", "TEN", "NYJ"),        # prefix, unique within the row
    ("WSH", "WAS", "PHI", "WSH"),       # alias either way round
    ("was", "WSH", "DAL", "WSH"),
    ("jac", "JAX", "DEN", "JAX"),
    ("Lac ", "LV", "LAC", "LAC"),
    ("", "NE", "SEA", ""),              # blank = missed
    ("   ", "NE", "SEA", ""),
    ("NY", "NYJ", "NYG", None),         # ambiguous prefix: never guess
    ("dale", "BAL", "DAL", None),       # not a code or a prefix of one
    ("la", "LV", "NO", None),           # LA -> LAR, not in this row
    ("N", "NE", "SEA", None),           # one letter is too short to trust
])
def test_resolve_pick(cell, away, home, expected):
    assert mps.resolve_pick(cell, away, home) == expected


# --- headers and records --------------------------------------------------------


@pytest.mark.parametrize("header,name", [
    ("Edwin - The Favorite (146-110)", "Edwin - The Favorite"),
    ("Pat - The Pilot (90-89; 62 missed)", "Pat - The Pilot"),
    ("Lee - The Lefty ( 6 - 10 )", "Lee - The Lefty"),
    ("Plain Name", "Plain Name"),
    ("  Spaced (1-2)  ", "Spaced"),
])
def test_strip_record(header, name):
    assert mps.strip_record(header) == name


def test_record_label_shows_missed_only_when_nonzero():
    assert mps.Record(wins=5, losses=11).label() == "(5-11)"
    assert mps.Record(wins=90, losses=89, missed=62).label() == "(90-89; 62 missed)"
    # Pushes are neither a W nor an L and, like the sheet, are not shown.
    assert mps.Record(wins=1, losses=1, pushes=3).label() == "(1-1)"


def test_compute_records_from_prior_weeks_of_the_same_season():
    pool = pool_fixture()
    players = ["Ann - The Alpha", "Bo - The Bravo", "Cy - The Charlie"]
    records, warnings = mps.compute_records(pool, season=2026, week=3, players=players)

    ann, bo, cy = (records[p] for p in players)
    # Week 1: NE@SEA, WAS@PHI, DEN@KC(push), NYJ@TEN ; week 2: DET@BUF graded only.
    assert (ann.wins, ann.losses, ann.pushes, ann.missed) == (3, 1, 1, 0)
    assert (bo.wins, bo.losses, bo.pushes, bo.missed) == (2, 2, 0, 1)
    # Cy: SEA L, was W, DEN push, dale unresolved (not a loss), buf W
    assert (cy.wins, cy.losses, cy.pushes, cy.missed, cy.unresolved) == (2, 1, 1, 0, 1)

    kinds = [w["kind"] for w in warnings]
    ungraded = [w for w in warnings if w["kind"] == "ungraded_week"]
    assert len(ungraded) == 1
    assert "nfl_week_2_2026_pickem" in ungraded[0]["detail"]
    assert "CAR@ATL" in ungraded[0]["detail"] and "CIN@HOU" in ungraded[0]["detail"]
    assert "DET@BUF" not in ungraded[0]["detail"]
    unresolved = [w for w in warnings if w["kind"] == "unresolved_pick"]
    assert len(unresolved) == 1 and unresolved[0]["game"] == "NYJ@TEN"
    assert "dale" in unresolved[0]["detail"] and "Cy - The Charlie" in unresolved[0]["detail"]
    # The 2025 tab and the week-3 tab itself are never counted.
    assert "Old - Player" not in str(warnings)
    assert kinds.count("ungraded_week") == 1


def test_compute_records_only_counts_weeks_before_the_target():
    records, _ = mps.compute_records(pool_fixture(), season=2026, week=2,
                                     players=["Ann - The Alpha"])
    ann = records["Ann - The Alpha"]
    assert (ann.wins, ann.losses) == (2, 1)  # week 1 only


def test_compute_records_matches_player_by_name_when_nickname_changes():
    pool = pool_fixture()
    pool["nfl_week_1_2026_pickem"][0][8] = "Ann - The Underdog (0-0)"
    records, _ = mps.compute_records(pool, season=2026, week=2, players=["Ann - The Alpha"])
    assert records["Ann - The Alpha"].wins == 2


def test_pool_header_uses_latest_tab_names_with_computed_records():
    header, source, warnings = mps.pool_header(pool_fixture(), season=2026, week=4)
    assert header[:8] == FIXED
    # Week 3 (the latest tab) supplies names; records are computed, not copied.
    assert header[8:] == ["Ann - The Alpha (3-1)", "Bo - The Bravo (2-2; 1 missed)",
                          "Cy - The Charlie (2-1)"]
    assert "nfl_week_3_2026_pickem" in source
    assert any(w["kind"] == "ungraded_week" for w in warnings)


def test_pool_header_reads_latest_tab_not_first_tab():
    """Regression: the old /pub?output=csv read returned the FIRST tab's stale header."""
    pool = pool_fixture()
    pool["nfl_week_3_2026_pickem"][0][10] = "Cy - The Newcomer (0-0)"
    header, _, _ = mps.pool_header(pool, season=2026, week=4)
    assert header[10].startswith("Cy - The Newcomer ")


def test_week_one_carries_the_latest_header_through():
    pool = {k: v for k, v in pool_fixture().items() if "2025" in k}
    header, source, _ = mps.pool_header(pool, season=2026, week=1)
    assert header[8:] == ["Old - Player (1-1)", "Bo - The Bravo (1-1)", "Cy - The Charlie (1-1)"]
    assert "carried" in source


def test_pool_header_without_any_pickem_tab_raises():
    with pytest.raises(mps.SheetError):
        mps.pool_header({"Sheet1": [["a", "b"]]}, season=2026, week=4)


# --- tab naming -----------------------------------------------------------------


@pytest.mark.parametrize("titles,pattern", [
    (["Sheet1", "nfl_week_1_2026_pickem", "nfl_week_3_2026_pickem"], "nfl_week_{week}_{season}_pickem"),
    (["Week 1", "Week 2"], "Week {week}"),
    (["NFL Week 2 - 2026", "NFL Week 3 - 2026"], "NFL Week {week} - {season}"),
    ([], "nfl_week_{week}_{season}_pickem"),
    (["Sheet1", "Standings"], "nfl_week_{week}_{season}_pickem"),
])
def test_detect_tab_pattern(titles, pattern):
    assert mps.detect_tab_pattern(titles) == pattern


def test_tab_title_formats_pattern():
    assert mps.tab_title("nfl_week_{week}_{season}_pickem", 4, 2026) == "nfl_week_4_2026_pickem"
    assert mps.tab_title("Week {week}", 4, 2026) == "Week 4"


# --- sheet URLs -----------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://docs.google.com/spreadsheets/d/e/2PACX-abc_1/pubhtml",
     "https://docs.google.com/spreadsheets/d/e/2PACX-abc_1/pub?output=xlsx"),
    ("https://docs.google.com/spreadsheets/d/e/2PACX-abc_1/pub?output=csv",
     "https://docs.google.com/spreadsheets/d/e/2PACX-abc_1/pub?output=xlsx"),
    ("https://docs.google.com/spreadsheets/d/1AbC-d_E/edit#gid=0",
     "https://docs.google.com/spreadsheets/d/1AbC-d_E/export?format=xlsx"),
    ("https://example.com/pool.xlsx", "https://example.com/pool.xlsx"),
])
def test_workbook_export_url(url, expected):
    assert mps.workbook_export_url(url) == expected




# --- The Odds API: fakes ----------------------------------------------------------
#
# Fixtures follow the documented v4 response shape
# (https://the-odds-api.com/liveapi/guides/v4/): /events returns
# [{id, sport_key, sport_title, commence_time, home_team, away_team}], /odds the
# same plus bookmakers[{key, title, last_update, markets[{key, outcomes[{name,
# price, point}]}]}]. No network: urllib.request.urlopen is replaced.

API_KEY = "KEY-s3cr3t-0123456789abcdef"
SPORT_PATH = "/v4/sports/americanfootball_nfl"


def event(eid, away, home, commence):
    return {"id": eid, "sport_key": "americanfootball_nfl", "sport_title": "NFL",
            "commence_time": commence, "home_team": home, "away_team": away}


def book(key, title, updated, points):
    """points: {team name: point}. Prices are the usual -110 juice."""
    return {"key": key, "title": title, "last_update": updated, "markets": [{
        "key": "spreads", "last_update": updated,
        "outcomes": [{"name": name, "price": -110, "point": pt} for name, pt in points.items()],
    }]}


def odds_event(eid, away, home, commence, books):
    return {**event(eid, away, home, commence), "bookmakers": books}


WEEK4_EVENTS = [
    event("e1", "Pittsburgh Steelers", "Cleveland Browns", "2026-10-02T00:15:00Z"),
    event("e2", "Indianapolis Colts", "Washington Commanders", "2026-10-04T13:30:00Z"),
    event("e3", "Jacksonville Jaguars", "Cincinnati Bengals", "2026-10-04T17:00:00Z"),
    event("e4", "New England Patriots", "Buffalo Bills", "2026-10-04T17:00:00Z"),
    event("e5", "New York Jets", "Miami Dolphins", "2026-10-04T17:00:00Z"),
]
WEEK4_ODDS = [
    # No DraftKings line: falls back to FanDuel. Whole number: can push.
    odds_event("e1", "Pittsburgh Steelers", "Cleveland Browns", "2026-10-02T00:15:00Z", [
        book("fanduel", "FanDuel", "2026-09-30T13:05:00Z",
             {"Pittsburgh Steelers": -3, "Cleveland Browns": 3}),
    ]),
    odds_event("e2", "Indianapolis Colts", "Washington Commanders", "2026-10-04T13:30:00Z", [
        book("draftkings", "DraftKings", "2026-09-30T12:00:00Z",
             {"Indianapolis Colts": -4.5, "Washington Commanders": 4.5}),
    ]),
    # e3 (JAX@CIN) has no odds yet, so the odds endpoint omits it.
    odds_event("e4", "New England Patriots", "Buffalo Bills", "2026-10-04T17:00:00Z", [
        book("fanduel", "FanDuel", "2026-09-30T11:00:00Z",
             {"New England Patriots": 6, "Buffalo Bills": -6}),
        book("draftkings", "DraftKings", "2026-09-30T12:34:56Z",
             {"New England Patriots": 5.5, "Buffalo Bills": -5.5}),
    ]),
    # Only a bookmaker outside the preference list.
    odds_event("e5", "New York Jets", "Miami Dolphins", "2026-10-04T17:00:00Z", [
        book("williamhill_us", "Caesars", "2026-09-30T12:00:00Z",
             {"New York Jets": 2.5, "Miami Dolphins": -2.5}),
    ]),
]


class FakeResp:
    def __init__(self, payload, headers):
        self._body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        self.headers = headers
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


class FakeOddsAPI:
    """Answers /events and /odds like The Odds API v4 would; records every request."""

    def __init__(self, events=None, odds=None):
        self.events = WEEK4_EVENTS if events is None else events
        self.odds = WEEK4_ODDS if odds is None else odds
        self.requests = []
        self.fail = None          # callable(path, url) -> exception to raise, or None
        self.remaining = 497

    def paths(self):
        return [urlparse(r.full_url).path for r in self.requests]

    def params(self, path_suffix):
        for r in self.requests:
            if urlparse(r.full_url).path.endswith(path_suffix):
                return {k: v[0] for k, v in parse_qs(urlparse(r.full_url).query).items()}
        return None

    def urlopen(self, req, timeout=None):
        self.requests.append(req)
        path = urlparse(req.full_url).path
        if self.fail is not None:
            exc = self.fail(path, req.full_url)
            if exc is not None:
                raise exc
        if path == f"{SPORT_PATH}/events":
            return FakeResp(self.events, {"x-requests-remaining": str(self.remaining),
                                          "x-requests-used": "3", "x-requests-last": "0"})
        if path == f"{SPORT_PATH}/odds":
            cost = "1" if self.odds else "0"
            return FakeResp(self.odds, {"x-requests-remaining": str(self.remaining - int(cost)),
                                        "x-requests-used": "4", "x-requests-last": cost})
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeOddsAPI()
    monkeypatch.setattr(mps.urllib.request, "urlopen", api.urlopen)
    monkeypatch.setattr(mps, "now_et", lambda: datetime(2026, 9, 30, 6, 0, tzinfo=ET))
    for var in (mps.ENV_SHEET_URL, mps.ENV_BOOKMAKERS, mps.ENV_SEASON_START):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(mps.ENV_ODDS_KEY, API_KEY)
    mps._ODDS_CACHE.clear()
    yield api
    mps._ODDS_CACHE.clear()


# --- ESPN is gone ---------------------------------------------------------------


def test_no_espn_left_in_the_pickem_scripts():
    for name in ("make_pickem_sheet.py", "push_pickem_to_sheets.py"):
        text = (REPO / "scripts" / name).read_text().lower()
        assert "espn" not in text, name
    assert not hasattr(mps, "fetch_espn_payload")


# --- team names -> sheet codes ----------------------------------------------------


SHEET_CODES = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
               "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
               "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WSH"}


def test_team_table_maps_all_32_teams_to_the_sheets_codes():
    assert len(mps.TEAM_CODES) == 32
    assert set(mps.TEAM_CODES.values()) == SHEET_CODES
    # Codes are already canonical, so records and grading join without aliases.
    assert all(mps.normalize_team(c) == c for c in mps.TEAM_CODES.values())


@pytest.mark.parametrize("name,code", [
    ("Kansas City Chiefs", "KC"), ("Washington Commanders", "WSH"), ("Jacksonville Jaguars", "JAX"),
    ("Los Angeles Rams", "LAR"), ("Los Angeles Chargers", "LAC"), ("Las Vegas Raiders", "LV"),
    ("  san francisco   49ERS ", "SF"), ("London Monarchs", None), ("", None),
])
def test_team_code_lookup(name, code):
    assert mps.team_code(name) == code


# --- configuration ------------------------------------------------------------------


def test_api_key_from_argument_env_then_keychain(monkeypatch):
    assert mps.ENV_ODDS_KEY == "ODDS_API_KEY"
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    no_keychain = lambda service: None  # noqa: E731
    with pytest.raises(mps.ConfigError) as exc:
        mps.odds_api_key(keychain=no_keychain)
    assert "ODDS_API_KEY" in str(exc.value)
    assert mps.KEYCHAIN_SERVICE in str(exc.value)  # the error says how to store it
    assert mps.odds_api_key(env={"ODDS_API_KEY": " k1 "}, keychain=no_keychain) == "k1"
    assert mps.odds_api_key("k2", env={"ODDS_API_KEY": "k1"}) == "k2"

    asked = []

    def keychain(service):
        asked.append(service)
        return " k3\n"

    # The keychain is only a fallback: env and argument win without consulting it.
    assert mps.odds_api_key(env={"ODDS_API_KEY": "k1"}, keychain=keychain) == "k1"
    assert asked == []
    assert mps.odds_api_key(env={}, keychain=keychain) == "k3"
    assert asked == [mps.KEYCHAIN_SERVICE]


def test_keychain_secret_off_macos_never_runs_anything(monkeypatch):
    monkeypatch.setattr(mps.sys, "platform", "linux")
    monkeypatch.setattr(mps.subprocess, "run", lambda *a, **k: pytest.fail("ran a command"))
    assert REAL_KEYCHAIN_SECRET("anything") is None


@pytest.mark.parametrize(
    "returncode, stdout, expected",
    [(0, "abc123\n", "abc123"), (44, "", None), (0, "  \n", None)],  # 44: item not found
)
def test_keychain_secret_on_macos(monkeypatch, returncode, stdout, expected):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return mps.subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(mps.sys, "platform", "darwin")
    monkeypatch.setattr(mps.subprocess, "run", run)
    assert REAL_KEYCHAIN_SECRET("svc") == expected
    assert calls == [["/usr/bin/security", "find-generic-password", "-s", "svc", "-w"]]


def test_keychain_secret_survives_missing_security_binary(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(mps.sys, "platform", "darwin")
    monkeypatch.setattr(mps.subprocess, "run", run)
    assert REAL_KEYCHAIN_SECRET("svc") is None


def test_bookmaker_preference_flag_env_and_default():
    assert mps.bookmaker_preference(None, env={}) == ["draftkings", "fanduel", "betmgm"]
    assert mps.bookmaker_preference(None, env={"PICKEM_BOOKMAKERS": " FanDuel, betmgm ,"}) == \
        ["fanduel", "betmgm"]
    assert mps.bookmaker_preference("betmgm", env={"PICKEM_BOOKMAKERS": "fanduel"}) == ["betmgm"]
    for bad in ("draft kings", "fanduel;rm", ","):
        with pytest.raises(mps.ConfigError):
            mps.bookmaker_preference(bad, env={})


@pytest.mark.parametrize("season,opener", [
    (2026, date(2026, 9, 10)),   # Labor Day Sep 7
    (2025, date(2025, 9, 4)),    # Labor Day Sep 1
    (2027, date(2027, 9, 9)),    # Labor Day Sep 6
])
def test_season_opener_is_derived_as_thursday_after_labor_day(season, opener):
    assert mps.season_opener(season, env={}) == opener


def test_season_opener_override_from_flag_or_env():
    env = {"PICKEM_SEASON_START": "2026-09-17"}
    assert mps.season_opener(2026, env=env) == date(2026, 9, 17)
    assert mps.season_opener(2026, "2026-09-24", env=env) == date(2026, 9, 24)
    with pytest.raises(mps.ConfigError) as exc:
        mps.season_opener(2026, "09/17/2026", env={})
    assert "YYYY-MM-DD" in str(exc.value)
    with pytest.raises(mps.ConfigError) as exc:
        mps.season_opener(2027, env=env)          # a stale value from last season
    assert "2027" in str(exc.value)


# --- week resolution ------------------------------------------------------------------


def test_week_windows_run_wednesday_0300_to_wednesday_0259_et():
    opener = date(2026, 9, 10)
    start, end = mps.week_window(opener, 1)
    assert start == datetime(2026, 9, 9, 3, 0, tzinfo=ET)
    assert end == datetime(2026, 9, 16, 2, 59, 59, tzinfo=ET)
    start, end = mps.week_window(opener, 4)
    assert (start, end) == (datetime(2026, 9, 30, 3, 0, tzinfo=ET), datetime(2026, 10, 7, 2, 59, 59, tzinfo=ET))
    # Across the end of DST (Nov 1 2026) the window stays at 03:00 local.
    start, _ = mps.week_window(opener, 9)
    assert mps.iso_z(start) == "2026-11-04T08:00:00Z"
    assert mps.iso_z(mps.week_window(opener, 4)[0]) == "2026-09-30T07:00:00Z"


@pytest.mark.parametrize("now,week", [
    (datetime(2026, 9, 30, 6, 0, tzinfo=ET), 4),    # Wednesday 06:00 ET, the cron slot
    (datetime(2026, 9, 30, 3, 0, tzinfo=ET), 4),    # the flip itself
    (datetime(2026, 9, 30, 2, 59, tzinfo=ET), 3),
    (datetime(2026, 9, 28, 22, 30, tzinfo=ET), 3),  # during MNF: week 3 still in progress
    (datetime(2026, 9, 29, 12, 0, tzinfo=ET), 3),   # Tuesday
    (datetime(2026, 9, 9, 20, 20, tzinfo=ET), 1),   # a Wednesday-night opener is week 1
    (datetime(2026, 8, 1, 12, 0, tzinfo=ET), 1),    # before the season
    (datetime(2027, 2, 1, 12, 0, tzinfo=ET), 18),   # after the regular season
])
def test_resolve_week_from_season_opener(now, week):
    assert mps.resolve_week(date(2026, 9, 10), now) == week


@pytest.mark.parametrize("now,week", [
    (datetime(2026, 9, 30, 6, 0, tzinfo=ET), 4),
    (datetime(2026, 9, 9, 6, 0, tzinfo=ET), 1),     # Wednesday of week 1
    (datetime(2026, 9, 3, 3, 0, tzinfo=ET), 1),     # exactly 6 days before week 1 starts
    (datetime(2026, 9, 3, 2, 59, tzinfo=ET), None),
    (datetime(2026, 9, 2, 6, 0, tzinfo=ET), None),   # the Wednesday a week before week 1
    (datetime(2026, 7, 15, 6, 0, tzinfo=ET), None),  # July
    (datetime(2027, 1, 13, 2, 59, tzinfo=ET), 18),  # last minute of week 18
    (datetime(2027, 1, 13, 3, 0, tzinfo=ET), None),  # week 18 is over
    (datetime(2027, 1, 20, 6, 0, tzinfo=ET), None),  # mid-January
])
def test_season_week_is_none_in_the_off_season(now, week):
    assert mps.season_week(date(2026, 9, 10), now) == week


def test_xlsx_schedule_upcoming_week_flips_on_wednesday():
    def tab(week, kickoffs):
        return [["week", "kickoff_et", "matchup"]] + [[str(week), k, f"A{i}@B{i}"] for i, k in enumerate(kickoffs)]

    sheets = {"Week 3": tab(3, ["2026-09-24 20:15 ET", "2026-09-28 20:15 ET"]),
              "Week 4": tab(4, ["2026-10-01 20:15 ET", "2026-10-05 20:15 ET"])}
    assert mps.upcoming_week(sheets, now=datetime(2026, 9, 30, 6, 0, tzinfo=ET)) == 4
    assert mps.upcoming_week(sheets, now=datetime(2026, 9, 27, 13, 0, tzinfo=ET)) == 3


@pytest.mark.parametrize("now,season", [
    (datetime(2026, 9, 30, tzinfo=ET), 2026),
    (datetime(2027, 1, 10, tzinfo=ET), 2026),   # January belongs to the prior season
    (datetime(2027, 3, 1, tzinfo=ET), 2027),
])
def test_current_season(now, season):
    assert mps.current_season(now) == season


# --- fetch_week_slate ------------------------------------------------------------------


def test_slate_is_one_odds_call_plus_free_events_call_bounded_to_the_week(fake_api):
    mps.fetch_week_slate(2026, 4)
    assert fake_api.paths() == [f"{SPORT_PATH}/events", f"{SPORT_PATH}/odds"]
    for req in fake_api.requests:
        assert req.full_url.startswith("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/")
        ua = req.get_header("User-agent")
        assert ua and "pickem" in ua.lower()
    assert fake_api.params("/odds") == {
        "apiKey": API_KEY, "regions": "us", "markets": "spreads", "oddsFormat": "american",
        "dateFormat": "iso", "commenceTimeFrom": "2026-09-30T07:00:00Z",
        "commenceTimeTo": "2026-10-07T06:59:59Z",
    }
    assert fake_api.params("/events") == {
        "apiKey": API_KEY, "dateFormat": "iso", "commenceTimeFrom": "2026-09-30T07:00:00Z",
        "commenceTimeTo": "2026-10-07T06:59:59Z",
    }
    # Cached for the rest of the process: never spends a second credit.
    mps.fetch_week_slate(2026, 4)
    assert len(fake_api.requests) == 2


def test_slate_logs_remaining_credits_but_never_the_key(fake_api, capsys):
    mps.fetch_week_slate(2026, 4)
    captured = capsys.readouterr()
    assert "remaining 496" in captured.err and "cost 1" in captured.err
    assert API_KEY not in captured.out + captured.err
    assert "apiKey" not in captured.out + captured.err


def test_slate_games_sorted_with_spread_from_preferred_bookmaker(fake_api):
    games = mps.fetch_week_slate(2026, 4)
    assert [g.matchup for g in games] == ["PIT@CLE", "IND@WSH", "JAX@CIN", "NE@BUF", "NYJ@MIA"]
    pit, ind, jax, ne, nyj = games
    assert pit.kickoff_et == "2026-10-01 20:15 ET"
    # DraftKings has no PIT@CLE line: falls back to FanDuel.
    assert (pit.favorite, pit.spread, pit.spread_source) == ("PIT", "-3", "FanDuel")
    assert pit.spread_as_of_et == "2026-09-30 09:05 ET"
    assert (ind.favorite, ind.spread, ind.spread_source) == ("IND", "-4.5", "DraftKings")
    assert ind.kickoff_et == "2026-10-04 09:30 ET"
    # DraftKings wins over FanDuel for NE@BUF; the favorite is the negative point.
    assert (ne.favorite, ne.spread, ne.spread_source, ne.spread_as_of_et) == \
        ("BUF", "-5.5", "DraftKings", "2026-09-30 08:34 ET")
    assert jax.has_odds is False and jax.spread == "" and jax.spread_source == ""
    assert nyj.has_odds is True and nyj.spread == "" and nyj.spread_source == ""


def test_bookmaker_preference_order_is_honoured(fake_api):
    games = mps.fetch_week_slate(2026, 4, bookmakers=["fanduel", "draftkings"])
    ne = next(g for g in games if g.matchup == "NE@BUF")
    assert (ne.favorite, ne.spread, ne.spread_source) == ("BUF", "-6", "FanDuel")
    games = mps.fetch_week_slate(2026, 4, bookmakers=["williamhill_us"])
    nyj = next(g for g in games if g.matchup == "NYJ@MIA")
    assert (nyj.favorite, nyj.spread, nyj.spread_source) == ("MIA", "-2.5", "Caesars")
    assert len(fake_api.requests) == 2   # a different preference re-uses the cached payloads


def test_unknown_team_name_is_a_warning_not_a_crash(fake_api):
    fake_api.events = [event("x1", "London Monarchs", "Kansas City Chiefs", "2026-10-04T17:00:00Z")]
    fake_api.odds = [odds_event("x1", "London Monarchs", "Kansas City Chiefs", "2026-10-04T17:00:00Z", [
        book("draftkings", "DraftKings", "2026-09-30T12:00:00Z",
             {"London Monarchs": 7.5, "Kansas City Chiefs": -7.5})])]
    (game,) = mps.fetch_week_slate(2026, 4)
    assert game.home == "KC" and game.favorite == "KC" and game.spread == "-7.5"
    assert game.away == "London Monarchs"
    assert [w["kind"] for w in game.warnings] == ["unknown_team"]
    assert "London Monarchs" in game.warnings[0]["detail"]


def test_pickem_line_is_zero_with_no_favorite(fake_api):
    fake_api.odds = [odds_event("e2", "Indianapolis Colts", "Washington Commanders", "2026-10-04T13:30:00Z", [
        book("draftkings", "DraftKings", "2026-09-30T12:00:00Z",
             {"Indianapolis Colts": 0, "Washington Commanders": 0})])]
    ind = next(g for g in mps.fetch_week_slate(2026, 4) if g.matchup == "IND@WSH")
    assert (ind.favorite, ind.spread, ind.spread_source) == ("", "0", "DraftKings")


def test_malformed_bookmaker_is_skipped_for_the_next_one(fake_api):
    fake_api.odds = [odds_event("e2", "Indianapolis Colts", "Washington Commanders", "2026-10-04T13:30:00Z", [
        book("draftkings", "DraftKings", "2026-09-30T12:00:00Z",
             {"Indianapolis Colts": -4.5, "Washington Commanders": -4.5}),      # both negative
        book("fanduel", "FanDuel", "2026-09-30T12:00:00Z",
             {"Indianapolis Colts": -4, "Washington Commanders": 4})])]
    ind = next(g for g in mps.fetch_week_slate(2026, 4) if g.matchup == "IND@WSH")
    assert (ind.favorite, ind.spread, ind.spread_source) == ("IND", "-4", "FanDuel")


def test_unexpected_payload_shape_fails_loud(fake_api):
    fake_api.events = {"message": "not a list"}
    with pytest.raises(mps.SheetError) as exc:
        mps.fetch_week_slate(2026, 4)
    assert exc.value.status == 502
    mps._ODDS_CACHE.clear()
    fake_api.events = [{"id": "e1", "home_team": "Cleveland Browns"}]
    with pytest.raises(mps.SheetError):
        mps.fetch_week_slate(2026, 4)


def test_empty_slate_is_a_clear_error_that_spends_no_credit(fake_api):
    fake_api.events = []
    with pytest.raises(mps.SheetError) as exc:
        mps.fetch_week_slate(2026, 4)
    message = str(exc.value)
    assert "PICKEM_SEASON_START" in message and "--season-start" in message
    assert fake_api.paths() == [f"{SPORT_PATH}/events"]      # the paid /odds call was skipped


def test_missing_api_key_fails_before_any_request(fake_api, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY")
    with pytest.raises(mps.ConfigError) as exc:
        mps.fetch_week_slate(2026, 4)
    assert "ODDS_API_KEY" in str(exc.value)
    assert fake_api.requests == []


# --- the key never leaks ------------------------------------------------------------------


def http_error(url, status, message):
    body = json.dumps({"message": message, "error_code": "X"}).encode()
    return urllib.error.HTTPError(url, status, f"status {status} for {url}", {}, io.BytesIO(body))


FAILURES = {
    "401": lambda path, url: http_error(url, 401, f"API key {API_KEY} is not valid"),
    "429": lambda path, url: http_error(url, 429, "Usage quota has been reached"),
    "500-on-odds": lambda path, url: http_error(url, 500, f"boom {url}") if path.endswith("/odds") else None,
    "urlerror": lambda path, url: urllib.error.URLError(f"cannot connect: {url}"),
    "valueerror": lambda path, url: ValueError(f"unknown url type: {url}"),
    "timeout": lambda path, url: TimeoutError(f"timed out reading {url}"),
}


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_api_key_never_reaches_stdout_or_stderr_on_failure(fake_api, capsys, failure):
    fake_api.fail = FAILURES[failure]
    with pytest.raises(mps.SheetError) as exc:
        mps.fetch_week_slate(2026, 4)
    assert API_KEY not in str(exc.value) and "apiKey=" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    mps._ODDS_CACHE.clear()

    # And end to end through the CLI, which prints the error.
    assert mps.main(["--week", "4", "--season", "2026", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert API_KEY not in captured.out + captured.err
    assert "error:" in captured.err


def test_api_key_is_redacted_from_any_text():
    text = f"GET https://api.the-odds-api.com/v4/x?apiKey={API_KEY}&regions=us failed ({API_KEY})"
    out = mps.redact(text, API_KEY)
    assert API_KEY not in out and "regions=us" in out
    assert "apiKey=abc" not in mps.redact("u?apiKey=abc&x=1", None)


def test_rejected_key_is_a_config_error(fake_api):
    fake_api.fail = FAILURES["401"]
    with pytest.raises(mps.ConfigError) as exc:
        mps.fetch_week_slate(2026, 4)
    assert "ODDS_API_KEY" in str(exc.value) and "401" in str(exc.value)


# --- build_sheet end to end ----------------------------------------------------------------


def test_build_sheet_on_wednesday_uses_odds_api_slate_and_pool_records(fake_api):
    result = mps.build_sheet(pool=pool_fixture())
    assert result.week == 4 and result.season == 2026
    assert [r[2] for r in result.rows] == ["PIT@CLE", "IND@WSH", "JAX@CIN", "NE@BUF", "NYJ@MIA"]
    assert all(len(r) == len(result.header) == 11 for r in result.rows)
    assert result.rows[0][:8] == ["4", "2026-10-01 20:15 ET", "PIT@CLE", "PIT", "-3", "FanDuel",
                                  "2026-09-30 09:05 ET", ""]
    assert result.rows[2][3:8] == ["", "", "", "", ""]      # JAX@CIN: no odds, no invented line
    assert result.header[8] == "Ann - The Alpha (3-1)"
    assert result.header[9] == "Bo - The Bravo (2-2; 1 missed)"
    kinds = [(w["kind"], w["game"]) for w in result.warnings]
    assert ("whole_number_line", "PIT@CLE") in kinds
    assert ("no_line", "JAX@CIN") in kinds and ("no_line", "NYJ@MIA") in kinds
    missing = [w for w in result.warnings if w["kind"] == "missing_games"]
    assert len(missing) == 1 and missing[0]["count"] == 1 and "1 of 5" in missing[0]["detail"]
    assert {"ungraded_week", "unresolved_pick"} <= {w["kind"] for w in result.warnings}
    assert fake_api.paths().count(f"{SPORT_PATH}/odds") == 1


def test_build_sheet_passes_bookmakers_and_season_start(fake_api):
    result = mps.build_sheet(season=2026, pool=pool_fixture(), bookmakers="fanduel",
                             season_start="2026-09-10")
    ne = next(r for r in result.rows if r[2] == "NE@BUF")
    assert ne[3:6] == ["BUF", "-6", "FanDuel"]
    # A season that opens a week later shifts which week "now" is.
    mps._ODDS_CACHE.clear()
    result = mps.build_sheet(season=2026, pool=pool_fixture(), season_start="2026-09-17")
    assert result.week == 3


def test_build_sheet_pickem_line_warns_instead_of_whole_number(fake_api):
    fake_api.odds = [odds_event("e2", "Indianapolis Colts", "Washington Commanders", "2026-10-04T13:30:00Z", [
        book("draftkings", "DraftKings", "2026-09-30T12:00:00Z",
             {"Indianapolis Colts": 0, "Washington Commanders": 0})])]
    result = mps.build_sheet(week=4, season=2026, pool=pool_fixture())
    kinds = {(w["kind"], w["game"]) for w in result.warnings}
    assert ("pickem_line", "IND@WSH") in kinds and ("whole_number_line", "IND@WSH") not in kinds


def test_build_sheet_with_optional_xlsx_schedule_joins_odds_api_lines(fake_api, monkeypatch):
    schedule = {"Week 4": [["week", "kickoff_et", "matchup"],
                           ["4", "2026-10-01 20:15 ET", "PIT@CLE"],
                           ["4", "2026-10-04 09:30 ET", "IND@WAS"],     # sheet spells WAS
                           ["4", "2026-10-04 13:00 ET", "XXX@YYY"]]}
    monkeypatch.setattr(mps, "load_sheets", lambda source: schedule)
    result = mps.build_sheet(week=4, season=2026, schedule=Path("sched.xlsx"), pool=pool_fixture())
    assert [r[2] for r in result.rows] == ["PIT@CLE", "IND@WSH", "XXX@YYY"]
    assert result.rows[1][3:6] == ["IND", "-4.5", "DraftKings"]
    assert any(w["kind"] == "unmatched" and w["game"] == "XXX@YYY" for w in result.warnings)


def test_build_sheet_from_uploaded_workbook_computes_records(fake_api, monkeypatch):
    pool = pool_fixture()
    monkeypatch.setattr(mps, "load_sheets", lambda source: pool)
    result = mps.build_sheet(week=4, season=2026, workbook=b"PK-fake")
    assert result.header[8] == "Ann - The Alpha (3-1)"


def test_build_sheet_without_a_pool_falls_back_to_placeholders(fake_api):
    result = mps.build_sheet(week=4, season=2026)
    assert len(result.header) == 14
    assert result.header[8:] == [f"player_{i}" for i in range(1, 7)]
    fallback = [w for w in result.warnings if w["kind"] == "header_fallback"]
    assert fallback and mps.ENV_SHEET_URL in fallback[0]["detail"]


def test_build_sheet_reads_pool_from_configured_url(fake_api, monkeypatch):
    pool = pool_fixture()
    seen = []
    monkeypatch.setenv(mps.ENV_SHEET_URL, "https://docs.google.com/spreadsheets/d/e/2PACX-x/pubhtml")
    monkeypatch.setattr(mps, "fetch_pool", lambda url: seen.append(url) or pool)
    result = mps.build_sheet(week=4, season=2026)
    assert seen == ["https://docs.google.com/spreadsheets/d/e/2PACX-x/pubhtml"]
    assert result.header[8] == "Ann - The Alpha (3-1)"


def test_build_sheet_rejects_week_out_of_range(fake_api):
    with pytest.raises(mps.SheetError):
        mps.build_sheet(week=19, season=2026, pool=pool_fixture())
    assert fake_api.requests == []


def test_cli_dry_run_prints_csv_and_credit_log(fake_api, capsys, tmp_path):
    header_csv = tmp_path / "h.csv"
    header_csv.write_text(",".join(FIXED + [f"P{i}" for i in range(1, 7)]) + "\n")
    assert mps.main(["--week", "4", "--season", "2026", "--dry-run", "--headers", str(header_csv),
                     "--bookmakers", "fanduel,draftkings"]) == 0
    captured = capsys.readouterr()
    assert "4,2026-10-01 20:15 ET,PIT@CLE,PIT,-3,FanDuel" in captured.out
    assert "remaining" in captured.err
    assert API_KEY not in captured.out + captured.err


# --- CSV formula-injection guard ---------------------------------------------------


@pytest.mark.parametrize("cell", [
    "=HYPERLINK(1)", "+1+1", "-1+cmd", "@SUM(A1)", "  =1", "\t=1", "\r=1", "\n=1",
    "＝1", "＋1", "－1", "＠1",
])
def test_csv_cell_prefixes_formula_like_text(cell):
    assert mps.csv_safe_row([cell] * 14)[2] == "'" + cell
    assert mps.csv_safe_row([cell] * 14)[0] == "'" + cell      # text in the week column too


@pytest.mark.parametrize("week,spread", [("4", "-6.5"), ("4", "-3"), ("12", "0"), ("4", "3.5"), ("4", "")])
def test_csv_numeric_week_and_spread_stay_unprefixed(week, spread):
    row = [week, "2026-10-01 20:15 ET", "PIT@CLE", "PIT", spread] + [""] * 9
    assert mps.csv_safe_row(row) == row


def test_csv_negative_number_outside_numeric_columns_is_prefixed():
    row = ["4", "", "PIT@CLE", "", "-3"] + [""] * 8 + ["-3"]
    assert mps.csv_safe_row(row)[13] == "'-3"


def test_build_sheet_csv_guards_formula_like_player_names(fake_api, tmp_path, capsys):
    header_csv = tmp_path / "h.csv"
    names = ["=cmd|' /C calc'!A0", "@SUM(1)", "＋x", "\tP4", "P5", "P6"]
    with header_csv.open("w", newline="") as fh:
        import csv as _csv
        _csv.writer(fh).writerow(FIXED + names)
    out = tmp_path / "out.csv"
    assert mps.main(["--week", "4", "--season", "2026", "--headers", str(header_csv),
                     "--bookmakers", "fanduel,draftkings", "--out", str(out)]) == 0
    import csv as _csv
    rows = list(_csv.reader(io.StringIO(out.read_text())))
    assert rows[0][8:] == ["'=cmd|' /C calc'!A0", "'@SUM(1)", "'＋x", "'\tP4", "P5", "P6"]
    assert rows[1][:5] == ["4", "2026-10-01 20:15 ET", "PIT@CLE", "PIT", "-3"]
    result = mps.build_sheet(week=4, season=2026, headers=str(header_csv), bookmakers="draftkings")
    assert result.header[8] == names[0]                     # JSON preview keeps the raw value
    assert "'=cmd" in result.csv                            # --serve's csv field is guarded
    ne = next(r for r in _csv.reader(io.StringIO(result.csv)) if r[2] == "NE@BUF")
    assert ne[4] == "-5.5"


# --- --serve hardening -------------------------------------------------------------

import http.client  # noqa: E402
import threading  # noqa: E402
import zipfile  # noqa: E402

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def make_xlsx(sheet_xml: bytes | None = None, extra: dict | None = None) -> bytes:
    """A minimal xlsx: one tab 'Week 1' with inline strings."""
    sheet_xml = sheet_xml or (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>week</t></is></c></row></sheetData></worksheet>'
    ).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN}" xmlns:r="r"><sheets>'
                                       '<sheet name="Week 1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_minimal_xlsx_fixture_loads():
    assert mps.load_sheets(make_xlsx()) == {"Week 1": [["week"]]}


LAUGHS = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
          b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
          b'<worksheet xmlns="' + MAIN.encode() + b'"><sheetData><row r="1"><c r="A1" t="inlineStr">'
          b'<is><t>&lol2;</t></is></c></row></sheetData></worksheet>')


@pytest.mark.parametrize("xml", [
    LAUGHS,
    b"\xff\xfe" + LAUGHS.decode().replace('<?xml version="1.0"?>', "").encode("utf-16-le"),
])
def test_xml_part_with_doctype_is_refused(xml):
    with pytest.raises(mps.SheetError) as exc:
        mps.load_sheets(make_xlsx(sheet_xml=xml))
    assert exc.value.status == 400 and "DOCTYPE" in str(exc.value)


def test_lowercase_doctype_is_refused_as_malformed_xml():
    with pytest.raises(mps.SheetError) as exc:
        mps.load_sheets(make_xlsx(sheet_xml=LAUGHS.replace(b"<!DOCTYPE", b"<!doctype")))
    assert exc.value.status == 400


def test_doctype_in_shared_strings_is_refused():
    extra = {"xl/sharedStrings.xml": b'<!DOCTYPE x [<!ENTITY a "b">]><sst xmlns="' + MAIN.encode() + b'"/>'}
    with pytest.raises(mps.SheetError):
        mps.load_sheets(make_xlsx(extra=extra))


def test_xlsx_over_the_uncompressed_limit_is_refused(monkeypatch):
    assert mps.MAX_UNCOMPRESSED_BYTES == 50 * 1024 * 1024
    monkeypatch.setattr(mps, "MAX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(mps.SheetError) as exc:
        mps.load_sheets(make_xlsx(extra={"xl/media/big.bin": b"\0" * 1001}))
    assert exc.value.status == 400 and "uncompressed" in str(exc.value)


def test_xlsx_zip_bomb_at_real_limit_is_refused():
    bomb = make_xlsx(extra={"xl/media/big.bin": b"\0" * (50 * 1024 * 1024 + 1)})
    assert len(bomb) < 1024 * 1024                          # tiny on the wire
    with pytest.raises(mps.SheetError):
        mps.load_sheets(bomb)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.delenv("PICKEM_SERVE_ORIGIN", raising=False)
    httpd = mps.ThreadingHTTPServer(("127.0.0.1", 0), mps.Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, headers=None, body=None, host=None, content_length="auto"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    if content_length == "auto" and body is not None:
        conn.putheader("Content-Length", str(len(body)))
    elif content_length not in ("auto", None):
        conn.putheader("Content-Length", content_length)
    conn.endheaders(body)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data


def test_serve_sends_no_wildcard_cors_by_default(server):
    for method in ("GET", "OPTIONS"):
        status, headers, _ = request(server, method, "/health")
        assert status in (200, 204)
        assert "access-control-allow-origin" not in headers


def test_serve_allows_only_the_configured_origin(server, monkeypatch):
    monkeypatch.setenv("PICKEM_SERVE_ORIGIN", "http://localhost:5173")
    status, headers, _ = request(server, "OPTIONS", "/sheet", headers={"Origin": "http://localhost:5173"})
    assert status == 204
    assert headers["access-control-allow-origin"] == "http://localhost:5173"
    assert headers.get("vary") == "Origin"
    _, headers, _ = request(server, "GET", "/health")
    assert headers["access-control-allow-origin"] == "http://localhost:5173"   # never "*"


@pytest.mark.parametrize("host_fmt", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}"])
def test_serve_accepts_loopback_host_headers(server, host_fmt):
    status, _, body = request(server, "GET", "/health", host=host_fmt.format(port=server))
    assert status == 200 and json.loads(body) == {"status": "ok"}


@pytest.mark.parametrize("host_fmt", ["evil.example:{port}", "127.0.0.1", "localhost",
                                      "127.0.0.1:1", "", "localhost:{port}.evil.example"])
@pytest.mark.parametrize("method", ["GET", "POST", "OPTIONS"])
def test_serve_rejects_other_host_headers(server, host_fmt, method):
    status, headers, body = request(server, method, "/sheet", host=host_fmt.format(port=server),
                                    body=b"" if method == "POST" else None)
    assert status == 403
    assert json.loads(body)["detail"] == "forbidden"
    assert "access-control-allow-origin" not in headers


@pytest.mark.parametrize("content_length", [None, "-1", "abc", "1e3", ""])
def test_serve_post_rejects_bad_content_length(server, content_length):
    status, _, body = request(server, "POST", "/sheet", content_length=content_length,
                              headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert status == 400
    assert json.loads(body)["detail"] == "bad_request"


def multipart(fields: dict[str, bytes]) -> tuple[bytes, str]:
    boundary = "pickemboundary"
    out = b""
    for name, data in fields.items():
        extra = '; filename="pool.xlsx"' if name == "workbook" else ""
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"{extra}\r\n\r\n").encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


@pytest.mark.parametrize("workbook", ["doctype", "bomb"])
def test_serve_post_rejects_dangerous_workbooks_before_spending_a_credit(server, monkeypatch, workbook):
    calls = []
    monkeypatch.setattr(mps, "fetch_week_slate", lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(mps, "MAX_UNCOMPRESSED_BYTES", 5000)
    data = make_xlsx(sheet_xml=LAUGHS) if workbook == "doctype" else make_xlsx(
        extra={"xl/media/big.bin": b"\0" * 6000})
    body, ctype = multipart({"workbook": data, "week": b"4"})
    status, _, resp = request(server, "POST", "/sheet", body=body, headers={"Content-Type": ctype})
    assert status == 400 and json.loads(resp)["detail"] == "bad_request"
    assert calls == []


# --- yearless tab names ------------------------------------------------------------


def test_yearless_tabs_are_the_current_season_only_in_a_pool_without_years():
    assert mps.week_tab_keys(["Sheet1", "Week 1", "Week 2"], 2027) == {"Week 1": (2027, 1),
                                                                       "Week 2": (2027, 2)}
    keys = mps.week_tab_keys(["Week 1", "nfl_week_18_2026_pickem", "nfl_week_1_2027_pickem"], 2027)
    assert keys == {"nfl_week_18_2026_pickem": (2026, 18), "nfl_week_1_2027_pickem": (2027, 1)}


def test_last_seasons_yearless_tab_never_counts_in_this_seasons_records():
    old_header = FIXED + ["Ann - The Alpha (0-0)", "Bo - The Bravo (0-0)", "Cy - The Charlie (0-0)"]
    pool = {
        # A leftover yearless tab from 2026 where everyone won: must not count in 2027.
        "Week 1": [old_header, row(1, "NE@SEA", "NE", ["NE", "NE", "NE"])],
        "nfl_week_18_2026_pickem": [old_header, row(18, "NE@SEA", "SEA", ["SEA", "SEA", "SEA"])],
        "nfl_week_1_2027_pickem": [old_header, row(1, "DET@BUF", "BUF", ["BUF", "DET", ""],
                                                   kickoff="2027-09-12 13:00 ET")],
    }
    header, source, _ = mps.pool_header(pool, season=2027, week=2)
    assert header[8:] == ["Ann - The Alpha (1-0)", "Bo - The Bravo (0-1)", "Cy - The Charlie (0-0; 1 missed)"]
    assert "1 earlier week tab(s) of 2027" in source
