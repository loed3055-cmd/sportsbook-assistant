"""Tests for scripts/push_pickem_to_sheets.py.

The HTTP layers are fakes, so nothing here talks to Google or The Odds API, and
nothing imports the `app` package, google-auth, or needs Postgres. Runs under
`make test` or standalone:

    python -m pytest --noconftest -p no:cacheprovider tests/test_push_pickem_to_sheets.py
"""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import make_pickem_sheet as mps  # noqa: E402
import push_pickem_to_sheets as push  # noqa: E402


# Never read the developer's real macOS Keychain: tests that need a key pass one.
REAL_KEYCHAIN_SECRET = mps.keychain_secret


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    monkeypatch.setattr(mps, "keychain_secret", lambda service: None)

SHEET_ID = "sheet-abc123"
API_KEY = "KEY-s3cr3t-0123456789abcdef"
ENV = {"PICKEM_SPREADSHEET_ID": SHEET_ID, "ODDS_API_KEY": API_KEY}

FIXED = ["week", "kickoff_et", "matchup", "favorite", "spread",
         "spread_source", "spread_as_of_et", "ats_winner"]
PLAYERS = ["Edwin - The Favorite (5-11)", "Player2 - Nick2 (7-9)", "Player3 - Nick3 (8-8)",
           "Player4 - Nick4 (10-6)", "Player5 - Nick5 (6-10)", "Player6 - Nick6 (7-9)"]
HEADER = FIXED + PLAYERS


def make_result(week: int = 4, warnings: list[dict] | None = None, header=None,
                season: int = 2026) -> mps.SheetResult:
    header = header or HEADER
    rows = [
        [str(week), "2026-10-01 20:15 ET", "TB@ATL", "ATL", "-3", "DraftKings", "2026-09-30 09:00 ET", ""] + [""] * 6,
        [str(week), "2026-10-04 13:00 ET", "NYJ@MIA", "MIA", "-2.5", "DraftKings", "2026-09-30 09:00 ET", ""] + [""] * 6,
        [str(week), "2026-10-04 16:25 ET", "KC@DEN", "", "", "", "", ""] + [""] * 6,
    ]
    return mps.SheetResult(
        week=week, season=season, generated_at="2026-09-30 09:00 ET", header=header,
        rows=rows, csv="", header_source="tab nfl_week_3_2026_pickem",
        warnings=warnings if warnings is not None else [
            {"kind": "whole_number_line", "game": "TB@ATL", "detail": "ATL -3 can push"},
            {"kind": "no_line", "game": "KC@DEN", "detail": "no odds listed yet"},
        ],
    )


def pool_values(titles):
    """Tab contents the fake API returns for values:batchGet."""
    out = {}
    for t in titles:
        if mps.week_of(t) is not None:
            out[t] = [HEADER, ["3", "2026-09-24 20:15 ET", "ATL@GB", "GB", "-5.5", "DraftKings", "", "ATL",
                               "ATL", "GB", "gb", "atl", "", "zzz"]]
    return out


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code = status
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    """Records every request; answers like the Sheets API v4 would."""

    def __init__(self, tabs: list[str], values: dict | None = None):
        self.tabs = [{"sheetId": 100 + i, "title": t, "index": i} for i, t in enumerate(tabs)]
        self.values = pool_values(tabs) if values is None else values
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, url, json=None, params=None, timeout=None):  # noqa: A002
        url = unquote(url)
        self.calls.append((method, url, json))
        if method == "GET" and "values:batchGet" in url:
            ranges = [part.split("=", 1)[1] for part in url.split("?", 1)[1].split("&")
                      if part.startswith("ranges=")]
            out = []
            for r in ranges:
                title = r.rsplit("!", 1)[0].strip("'").replace("''", "'")
                out.append({"range": r, "values": self.values.get(title, [])})
            return FakeResponse(200, {"valueRanges": out})
        if method == "GET":
            return FakeResponse(200, {"sheets": [{"properties": p} for p in self.tabs]})
        if url.endswith(":batchUpdate"):
            replies = []
            for req in json["requests"]:
                if "duplicateSheet" in req:
                    d = req["duplicateSheet"]
                    props = {"sheetId": 999, "title": d["newSheetName"], "index": d["insertSheetIndex"]}
                    replies.append({"duplicateSheet": {"properties": props}})
                elif "addSheet" in req:
                    props = {"sheetId": 998, "title": req["addSheet"]["properties"]["title"], "index": 0}
                    replies.append({"addSheet": {"properties": props}})
                else:
                    replies.append({})
            return FakeResponse(200, {"replies": replies})
        return FakeResponse(200, {})

    def writes(self):
        return [c for c in self.calls if c[0] == "PUT"]

    def mutations(self):
        return [c for c in self.calls if c[0] != "GET"]

    def structural(self):
        return [r for c in self.calls if c[1].endswith(":batchUpdate") for r in c[2]["requests"]]


@pytest.fixture
def built(monkeypatch):
    """Stub the generator so no Odds API / sheet request happens."""
    calls = []

    def fake_build_sheet(week=None, season=None, **kwargs):
        calls.append({"week": week, "season": season, **kwargs})
        return make_result(week=week or 4, season=season or 2026)

    monkeypatch.setattr(mps, "build_sheet", fake_build_sheet)
    return calls


def run(argv, session=None, env=ENV):
    return push.main(argv, env=env, session_factory=(lambda: session) if session else None)


def no_session():
    raise AssertionError("must not create an authenticated session")


# --- tab naming ---------------------------------------------------------------


def test_tab_name_default_matches_existing_convention():
    assert push.tab_name(4, 2026) == "nfl_week_4_2026_pickem"
    assert mps.week_of(push.tab_name(12, 2026)) == 12


def test_tab_name_follows_the_sheets_own_pattern(built):
    session = FakeSession(["Week 1", "Week 2", "Week 3"])
    assert run(["--week", "4", "--season", "2026"], session) == 0
    dup = [r["duplicateSheet"] for r in session.structural() if "duplicateSheet" in r]
    assert dup == [{"sourceSheetId": 102, "insertSheetIndex": 3, "newSheetName": "Week 4"}]


# --- sheet-driven headers -------------------------------------------------------


def test_reads_pool_tabs_through_the_api_and_passes_them_to_the_generator(built):
    session = FakeSession(["Sheet1", "nfl_week_1_2026_pickem", "nfl_week_2_2026_pickem",
                           "nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--season", "2026"], session) == 0
    reads = [c[1] for c in session.calls if "values:batchGet" in c[1]]
    assert len(reads) == 1                      # one batched read, not one per tab
    assert "Sheet1" not in reads[0]             # only week tabs are read
    pool = built[0]["pool"]
    assert set(pool) == {"nfl_week_1_2026_pickem", "nfl_week_2_2026_pickem", "nfl_week_3_2026_pickem"}
    assert pool["nfl_week_3_2026_pickem"][0] == HEADER


# --- creates when absent ------------------------------------------------------


def test_creates_tab_from_previous_week_template_when_absent(built, capsys):
    session = FakeSession(["nfl_week_1_2026_pickem", "nfl_week_2_2026_pickem", "nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 0

    dup = [r["duplicateSheet"] for r in session.structural() if "duplicateSheet" in r]
    assert dup == [{"sourceSheetId": 102, "insertSheetIndex": 3, "newSheetName": "nfl_week_4_2026_pickem"}]

    clears = [c for c in session.calls if c[1].endswith("values:batchClear")]
    assert clears and clears[0][2]["ranges"] == ["'nfl_week_4_2026_pickem'!A2:N"]

    (_, url, body), = session.writes()
    assert "'nfl_week_4_2026_pickem'!A1:N4" in url
    assert body["values"][0] == HEADER
    assert len(body["values"]) == 4
    order = [c[1] for c in session.calls]
    assert order.index(clears[0][1]) < order.index(url)
    assert "created nfl_week_4_2026_pickem" in capsys.readouterr().out


def test_writes_raw_with_week_and_spread_as_json_numbers(built):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 0
    (_, url, body), = session.writes()
    assert "valueInputOption=RAW" in url and "USER_ENTERED" not in url
    first, second, third = body["values"][1:]
    assert first[0] == 4 and isinstance(first[0], int)
    assert first[4] == -3 and isinstance(first[4], int)
    assert second[4] == -2.5 and isinstance(second[4], float)
    assert third[4] == ""                                  # no line: stays blank
    json.dumps(body)                                        # serializable as sent


def test_adds_bare_tab_when_no_template_exists(built):
    session = FakeSession(["Sheet1"])
    assert run(["--week", "4", "--allow-header-fallback"], session) == 0
    kinds = [next(iter(r)) for r in session.structural()]
    assert kinds == ["addSheet"]
    assert session.structural()[0]["addSheet"]["properties"]["title"] == "nfl_week_4_2026_pickem"
    assert len(session.writes()) == 1


def freeze(monkeypatch, *args):
    monkeypatch.setattr(mps, "now_et", lambda: datetime(*args, tzinfo=mps.ET_ZONE))


def test_default_week_is_resolved_from_the_season_opener(built, monkeypatch):
    freeze(monkeypatch, 2026, 9, 30, 6, 0)
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run([], session) == 0
    assert built[0]["week"] == 4 and built[0]["season"] == 2026


# --- off-season guard -----------------------------------------------------------


@pytest.mark.parametrize("now", [
    (2027, 1, 20, 6, 0),    # mid-January Wednesday, after week 18 of 2026 (ended Jan 13 03:00 ET)
    (2027, 7, 14, 6, 0),    # July: months before 2027's week 1
    (2026, 8, 26, 6, 0),    # 14 days before 2026's week 1 starts (Sep 9 03:00 ET)
])
def test_off_season_cron_creates_nothing_and_exits_0(built, capsys, monkeypatch, now):
    freeze(monkeypatch, *now)
    session = FakeSession(["nfl_week_17_2026_pickem", "nfl_week_18_2026_pickem"])
    assert run([], session) == 0
    out = capsys.readouterr().out
    assert "off-season: nothing to create" in out
    assert session.calls == []      # not even a read
    assert built == []              # and no Odds API credit spent


def test_wednesday_of_week_1_creates_week_1(built, monkeypatch):
    freeze(monkeypatch, 2026, 9, 9, 6, 0)
    session = FakeSession(["nfl_week_18_2025_pickem"])
    assert run([], session) == 0
    assert built[0]["week"] == 1 and built[0]["season"] == 2026
    dup = [r["duplicateSheet"] for r in session.structural() if "duplicateSheet" in r]
    assert dup and dup[0]["newSheetName"] == "nfl_week_1_2026_pickem"


def test_wednesday_a_week_before_week_1_is_off_season(built, monkeypatch, capsys):
    """Creating week 1 eight days early would write stale lines and lock the
    real week-1 run out with exit 3."""
    freeze(monkeypatch, 2026, 9, 2, 9, 0)
    session = FakeSession(["nfl_week_18_2025_pickem"])
    assert run([], session) == 0
    assert "off-season: nothing to create" in capsys.readouterr().out
    assert built == [] and session.calls == []


def test_week_1_wednesday_1300z_cron_creates_week_1(built, monkeypatch):
    freeze(monkeypatch, 2026, 9, 9, 9, 0)      # 13:00Z = 09:00 EDT
    session = FakeSession(["nfl_week_18_2025_pickem"])
    assert run([], session) == 0
    assert built[0]["week"] == 1 and built[0]["season"] == 2026


def test_explicit_week_still_works_in_the_off_season(built, monkeypatch, capsys):
    freeze(monkeypatch, 2027, 7, 14, 6, 0)
    session = FakeSession(["nfl_week_18_2026_pickem"])
    assert run(["--week", "1", "--season", "2027"], session) == 0
    assert built[0]["week"] == 1
    assert "off-season" not in capsys.readouterr().out
    assert len(session.writes()) == 1


class FakeOddsAPI:
    """/events and /odds in The Odds API v4's documented shape; records requests."""

    def __init__(self, events, odds, fail=None):
        self.events, self.odds, self.fail, self.requests = events, odds, fail, []

    def urlopen(self, req, timeout=None):
        self.requests.append(req.full_url)
        path = urlparse(req.full_url).path
        if self.fail:
            raise self.fail(path, req.full_url)
        payload = self.events if path.endswith("/events") else self.odds

        class Resp:
            headers = {"x-requests-remaining": "499", "x-requests-last": "1"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode()

        return Resp()


PIT_CLE = {"id": "e1", "sport_key": "americanfootball_nfl", "sport_title": "NFL",
           "commence_time": "2026-10-02T00:15:00Z",
           "home_team": "Cleveland Browns", "away_team": "Pittsburgh Steelers"}
PIT_CLE_ODDS = {**PIT_CLE, "bookmakers": [{
    "key": "draftkings", "title": "DraftKings", "last_update": "2026-09-30T13:00:00Z",
    "markets": [{"key": "spreads", "outcomes": [
        {"name": "Pittsburgh Steelers", "price": -110, "point": -3.5},
        {"name": "Cleveland Browns", "price": -110, "point": 3.5}]}]}]}


@pytest.fixture
def odds_api(monkeypatch):
    api = FakeOddsAPI([PIT_CLE], [PIT_CLE_ODDS])
    monkeypatch.setattr(mps.urllib.request, "urlopen", api.urlopen)
    monkeypatch.setattr(mps, "now_et", lambda: datetime(2026, 9, 30, 6, 0, tzinfo=mps.ET_ZONE))
    mps._ODDS_CACHE.clear()
    yield api
    mps._ODDS_CACHE.clear()


def test_wednesday_cron_resolves_to_the_new_week(odds_api, capsys):
    """Frozen Wednesday 06:00 ET after week 3's MNF: no --week pushes week 4.

    This is the Actions contract: no week argument, ODDS_API_KEY and
    PICKEM_SPREADSHEET_ID from env, opener derived (no PICKEM_SEASON_START).
    """
    session = FakeSession(["nfl_week_2_2026_pickem", "nfl_week_3_2026_pickem"])
    assert run([], session) == 0
    (_, url, body), = session.writes()
    assert "'nfl_week_4_2026_pickem'!A1:N2" in url
    assert body["values"][1][:7] == [4, "2026-10-01 20:15 ET", "PIT@CLE", "PIT", -3.5, "DraftKings",
                                     "2026-09-30 09:00 ET"]
    odds_calls = [u for u in odds_api.requests if urlparse(u).path.endswith("/odds")]
    assert len(odds_calls) == 1 and "commenceTimeFrom=2026-09-30T07%3A00%3A00Z" in odds_calls[0]
    captured = capsys.readouterr()
    assert API_KEY not in captured.out + captured.err
    assert "remaining 499" in captured.err


def test_missing_odds_api_key_is_a_config_error_before_any_call(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session, env={"PICKEM_SPREADSHEET_ID": SHEET_ID}) == 2
    assert "ODDS_API_KEY" in capsys.readouterr().err
    assert session.calls == [] and built == []


def test_bookmakers_and_season_start_come_from_flags_or_env(built):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    env = {**ENV, "PICKEM_BOOKMAKERS": "betmgm", "PICKEM_SEASON_START": "2026-09-10"}
    assert run(["--week", "4"], session, env=env) == 0
    assert built[-1]["bookmakers"] == ["betmgm"]
    assert str(built[-1]["season_start"]) == "2026-09-10"
    assert built[-1]["api_key"] == API_KEY
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--bookmakers", "fanduel,draftkings", "--season-start", "2026-09-17"],
               session, env=env) == 0
    assert built[-1]["bookmakers"] == ["fanduel", "draftkings"]
    assert str(built[-1]["season_start"]) == "2026-09-17"


@pytest.mark.parametrize("argv,env_extra", [
    (["--bookmakers", "draft kings"], {}),
    (["--season-start", "Sept 10"], {}),
    ([], {"PICKEM_SEASON_START": "2025-09-04"}),     # stale: not this season
])
def test_bad_odds_config_is_exit_2_before_any_call(built, capsys, argv, env_extra):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--season", "2026", *argv], session, env={**ENV, **env_extra}) == 2
    assert session.calls == [] and built == []


def _http_error(url, status):
    body = json.dumps({"message": f"bad key {API_KEY}", "error_code": "INVALID_KEY"}).encode()
    return urllib.error.HTTPError(url, status, f"HTTP {status} {url}", {}, io.BytesIO(body))


@pytest.mark.parametrize("fail,code", [
    (lambda path, url: _http_error(url, 401), 2),
    (lambda path, url: _http_error(url, 500), 1),
    (lambda path, url: urllib.error.URLError(f"no route to {url}"), 1),
    (lambda path, url: RuntimeError(f"unexpected {url}"), 1),
])
def test_odds_api_key_never_printed_on_failure(odds_api, capsys, fail, code):
    odds_api.fail = fail
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == code
    captured = capsys.readouterr()
    assert API_KEY not in captured.out + captured.err
    assert "apiKey=" not in captured.out + captured.err
    assert "The Odds API" in captured.err
    assert session.mutations() == []


# --- refuses when present -----------------------------------------------------


def test_refuses_when_tab_exists(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem", "nfl_week_4_2026_pickem"])
    assert run(["--week", "4"], session) == 3
    err = capsys.readouterr().err
    assert "nfl_week_4_2026_pickem" in err and "already exists" in err and "--force" in err
    assert session.mutations() == []


def test_refuses_alternate_week_name(built):
    session = FakeSession(["Week 3", "Week 4"])
    assert run(["--week", "4", "--season", "2026"], session) == 3
    assert session.mutations() == []


# --- --force ------------------------------------------------------------------


def test_force_rewrites_existing_tab_in_place(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem", "nfl_week_4_2026_pickem"])
    assert run(["--week", "4", "--force"], session) == 0
    assert session.structural() == []
    clears = [c for c in session.calls if c[1].endswith("values:batchClear")]
    assert clears[0][2]["ranges"] == ["'nfl_week_4_2026_pickem'!A2:N"]
    (_, url, body), = session.writes()
    assert "'nfl_week_4_2026_pickem'!A1:N4" in url
    assert body["values"][0] == HEADER
    assert "overwrote" in capsys.readouterr().out


# --- dry run ------------------------------------------------------------------


def test_dry_run_only_reads(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--dry-run"], session) == 0
    assert session.mutations() == []
    assert all(c[0] == "GET" for c in session.calls)
    out = capsys.readouterr().out
    assert "nfl_week_4_2026_pickem" in out and "would" in out.lower()


def test_dry_run_hides_names_and_values_unless_asked(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--dry-run"], session) == 0
    out = capsys.readouterr().out
    assert "Player2" not in out and "Edwin" not in out and "TB@ATL,ATL" not in out

    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--dry-run", "--show-values"], session) == 0
    out = capsys.readouterr().out
    assert "Player2 - Nick2 (7-9)" in out and "TB@ATL" in out


def test_dry_run_with_workbook_needs_no_credentials(built, tmp_path):
    wb = tmp_path / "pool.xlsx"
    wb.write_bytes(b"PK-not-really")
    assert push.main(["--week", "4", "--dry-run", "--workbook", str(wb)], env={"ODDS_API_KEY": API_KEY},
                     session_factory=no_session) == 0
    assert built[0]["workbook"] == b"PK-not-really"


def test_dry_run_without_sheet_or_workbook_is_a_config_error(built, capsys):
    assert push.main(["--week", "4", "--dry-run"], env={}, session_factory=no_session) == 2
    assert "PICKEM_SPREADSHEET_ID" in capsys.readouterr().err
    assert built == []


def test_workbook_is_only_allowed_with_dry_run(built, tmp_path):
    wb = tmp_path / "pool.xlsx"
    wb.write_bytes(b"PK")
    assert push.main(["--week", "4", "--workbook", str(wb)], env=ENV, session_factory=no_session) == 2


# --- payload shape ------------------------------------------------------------


def test_payload_has_14_columns_in_order(built):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 0
    (_, _, body), = session.writes()
    values = body["values"]
    assert body["majorDimension"] == "ROWS"
    assert values[0][:8] == FIXED
    assert all(len(row) == 14 for row in values)
    assert values[1][:5] == [4, "2026-10-01 20:15 ET", "TB@ATL", "ATL", -3]


def test_rejects_bad_header_without_echoing_names(monkeypatch, capsys):
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: make_result(header=HEADER[:13]))
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 1
    err = capsys.readouterr().err
    assert "14" in err and "13" in err
    assert "Player" not in err and "Edwin" not in err
    assert session.mutations() == []


def test_rejects_blank_player_header_by_index(monkeypatch, capsys):
    header = HEADER[:9] + [" "] + HEADER[10:]
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: make_result(header=header))
    assert run(["--week", "4"], FakeSession(["nfl_week_3_2026_pickem"])) == 1
    err = capsys.readouterr().err
    assert "J" in err and "Player" not in err


@pytest.mark.parametrize("payload", [
    "=IMPORTXML(\"http://evil\")", "+1+1", "-1+cmd", "@SUM(A1)", "  =1", "\t=1", "\r=1", "\n=1",
    "＝1", "＋1", "－1", "＠1",
])
def test_rejects_cells_that_could_be_formulas_without_echoing_them(monkeypatch, capsys, payload):
    bad = make_result()
    bad.rows[0][2] = payload
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: bad)
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 1
    err = capsys.readouterr().err
    assert "C2" in err
    assert payload.strip() not in err
    assert session.mutations() == []


def test_rejects_formula_like_player_header(monkeypatch, capsys):
    header = HEADER[:8] + ["=HYPERLINK(1)"] + HEADER[9:]
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: make_result(header=header))
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 1
    assert "I1" in capsys.readouterr().err
    assert session.mutations() == []


def test_non_numeric_spread_is_rejected(monkeypatch, capsys):
    bad = make_result()
    bad.rows[0][4] = "abc"
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: bad)
    assert run(["--week", "4"], FakeSession(["nfl_week_3_2026_pickem"])) == 1
    assert "E2" in capsys.readouterr().err


# --- warnings -----------------------------------------------------------------


def test_generator_warnings_go_to_stdout(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 0
    out = capsys.readouterr().out
    assert "whole_number_line" in out and "TB@ATL" in out
    assert "no_line" in out and "KC@DEN" in out


def test_warnings_redact_player_names_and_cells(monkeypatch, capsys):
    warn = [{"kind": "unresolved_pick", "game": "NYJ@TEN",
             "detail": "nfl_week_1_2026_pickem column M (Player5 - Nick5): 'dale'",
             "public_detail": "nfl_week_1_2026_pickem column M"},
            {"kind": "ungraded_week", "game": None,
             "detail": "nfl_week_2_2026_pickem: 16 of 16 games have no ats_winner: DET@BUF"}]
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: make_result(warnings=warn))
    assert run(["--week", "4"], FakeSession(["nfl_week_3_2026_pickem"]),
               env={**ENV, "GITHUB_ACTIONS": "true"}) == 0
    out = capsys.readouterr().out
    assert "unresolved_pick" in out and "column M" in out
    assert "Player5" not in out and "dale" not in out
    assert "ungraded_week" in out and "DET@BUF" in out
    assert "::warning title=pickem ungraded_week::" in out


def test_header_fallback_is_fatal_unless_allowed(monkeypatch, capsys):
    warn = [{"kind": "header_fallback", "game": None, "detail": "no pick'em tab"}]
    monkeypatch.setattr(mps, "build_sheet", lambda **kw: make_result(warnings=warn))
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 1
    assert "header_fallback" in capsys.readouterr().err
    assert session.writes() == []

    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--allow-header-fallback"], session) == 0
    assert len(session.writes()) == 1


# --- configuration / credentials ------------------------------------------------


def test_missing_spreadsheet_id_gives_clear_error(built, capsys):
    assert push.main(["--week", "4"], env={}, session_factory=lambda: FakeSession([])) == 2
    assert "PICKEM_SPREADSHEET_ID" in capsys.readouterr().err
    assert built == []


def test_old_service_account_env_var_is_not_used(built, capsys):
    env = {"GOOGLE_SERVICE_ACCOUNT_JSON": "{}"}
    assert push.main(["--week", "4"], env=env, session_factory=lambda: FakeSession([])) == 2


@pytest.mark.parametrize("raw,expected", [
    ("1AbC-d_E", "1AbC-d_E"),
    ("https://docs.google.com/spreadsheets/d/1AbC-d_E/edit#gid=0", "1AbC-d_E"),
])
def test_spreadsheet_id_from_id_or_edit_url(raw, expected):
    assert push.load_config({"PICKEM_SPREADSHEET_ID": raw}) == expected


@pytest.mark.parametrize("raw", ["2PACX-1vabc", "https://docs.google.com/spreadsheets/d/e/2PACX-1v/pubhtml",
                                 "not an id!"])
def test_spreadsheet_id_rejects_publish_ids(raw, capsys):
    with pytest.raises(push.PushError) as exc:
        push.load_config({"PICKEM_SPREADSHEET_ID": raw})
    assert exc.value.code == 2
    assert raw not in str(exc.value)


def fake_google_modules(monkeypatch, default):
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    exceptions = types.ModuleType("google.auth.exceptions")
    transport = types.ModuleType("google.auth.transport")
    transport_requests = types.ModuleType("google.auth.transport.requests")

    class GoogleAuthError(Exception):
        pass

    class DefaultCredentialsError(GoogleAuthError):
        pass

    exceptions.GoogleAuthError = GoogleAuthError
    exceptions.DefaultCredentialsError = DefaultCredentialsError
    auth.default = default(exceptions)
    auth.exceptions = exceptions
    transport_requests.AuthorizedSession = lambda creds: ("session", creds)
    google.auth = auth
    for name, mod in {"google": google, "google.auth": auth, "google.auth.exceptions": exceptions,
                      "google.auth.transport": transport,
                      "google.auth.transport.requests": transport_requests}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return exceptions


def test_make_session_uses_application_default_credentials(monkeypatch):
    seen = {}

    def default(_exc):
        def _default(scopes=None):
            seen["scopes"] = scopes
            return "creds", "project"
        return _default

    fake_google_modules(monkeypatch, default)
    assert push.make_session() == ("session", "creds")
    assert seen["scopes"] == ["https://www.googleapis.com/auth/spreadsheets"]


def test_make_session_auth_error_reports_type_only(monkeypatch):
    def default(exc):
        def _default(scopes=None):
            raise exc.DefaultCredentialsError(f"file /secret/{SHEET_ID}.json not found")
        return _default

    fake_google_modules(monkeypatch, default)
    with pytest.raises(push.PushError) as err:
        push.make_session()
    assert err.value.code == 2
    assert "DefaultCredentialsError" in str(err.value)
    assert SHEET_ID not in str(err.value) and "secret" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


# --- API failures -------------------------------------------------------------


class FailingWriteSession(FakeSession):
    def request(self, method, url, json=None, params=None, timeout=None):  # noqa: A002
        if method == "PUT":
            self.calls.append((method, unquote(url), json))
            return FakeResponse(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                "message": f"No access to spreadsheets/{SHEET_ID}"}})
        return super().request(method, url, json=json, params=params, timeout=timeout)


def test_failed_write_rolls_back_new_tab_and_fails_loud(built, capsys):
    session = FailingWriteSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 1
    err = capsys.readouterr().err
    assert "HTTP 403 PERMISSION_DENIED" in err
    assert "No access" not in err                   # full message only with --show-values
    assert SHEET_ID not in err
    deletes = [r["deleteSheet"] for r in session.structural() if "deleteSheet" in r]
    assert deletes == [{"sheetId": 999}]


def test_api_error_message_only_with_show_values_and_scrubbed(built, capsys):
    session = FailingWriteSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4", "--show-values"], session) == 1
    err = capsys.readouterr().err
    assert "HTTP 403 PERMISSION_DENIED" in err and "No access" in err
    assert SHEET_ID not in err and "<spreadsheet-id>" in err


class NonJsonErrorSession(FakeSession):
    def request(self, method, url, json=None, params=None, timeout=None):  # noqa: A002
        resp = FakeResponse(400)
        resp.text = f"<html>bad request for {SHEET_ID}: Player2 - Nick2</html>"
        resp.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
        return resp


def test_non_json_api_error_body_is_not_echoed(built, capsys):
    assert run(["--week", "4"], NonJsonErrorSession([])) == 1
    err = capsys.readouterr().err
    assert "HTTP 400" in err
    assert "Player2" not in err and SHEET_ID not in err and "html" not in err


class MalformedReplySession(FakeSession):
    """batchUpdate answers 200 but without the duplicateSheet reply."""

    def request(self, method, url, json=None, params=None, timeout=None):  # noqa: A002
        if url.endswith(":batchUpdate"):
            self.calls.append((method, unquote(url), json))
            return FakeResponse(200, {"replies": [{"unexpected": {"secret": SHEET_ID}}]})
        return super().request(method, url, json=json, params=params, timeout=timeout)


def test_unexpected_exception_is_exit_1_without_traceback(built, capsys):
    session = MalformedReplySession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == ("error: unexpected KeyError; re-run locally with --show-values "
                                    "for details")
    assert "Traceback" not in captured.err + captured.out
    assert SHEET_ID not in captured.err + captured.out


class ConnectionFailure(Exception):
    pass


class ExplodingSession(FakeSession):
    def request(self, method, url, json=None, params=None, timeout=None):  # noqa: A002
        raise ConnectionFailure(f"Max retries exceeded with url: /v4/spreadsheets/{SHEET_ID}/values")


def test_transport_errors_report_type_only(built, capsys):
    assert run(["--week", "4"], ExplodingSession([])) == 1
    captured = capsys.readouterr()
    assert "ConnectionFailure" in captured.err
    assert SHEET_ID not in captured.err + captured.out


def test_spreadsheet_id_never_printed_on_success(built, capsys):
    session = FakeSession(["nfl_week_3_2026_pickem"])
    assert run(["--week", "4"], session) == 0
    captured = capsys.readouterr()
    assert SHEET_ID not in captured.err + captured.out


# --- yearless tab names across seasons ----------------------------------------------


def test_last_seasons_yearless_week_1_does_not_block_next_seasons_week_1(built, capsys):
    session = FakeSession(["Week 1", "Week 2", "nfl_week_17_2026_pickem", "nfl_week_18_2026_pickem"])
    assert run(["--week", "1", "--season", "2027"], session) == 0
    dup = [r["duplicateSheet"] for r in session.structural() if "duplicateSheet" in r]
    assert dup == [{"sourceSheetId": 103, "insertSheetIndex": 4, "newSheetName": "nfl_week_1_2027_pickem"}]
    assert set(built[0]["pool"]) == {"nfl_week_18_2026_pickem"}      # no yearless tab in the pool
    session = FakeSession(["Week 1", "nfl_week_18_2026_pickem"])
    assert run(["--week", "1", "--season", "2027", "--dry-run"], session) == 0
    assert "no tab for this week exists yet" in capsys.readouterr().out


def test_yearless_pool_still_detects_its_own_week_tabs(built):
    session = FakeSession(["Week 1", "Week 2", "Week 3", "Week 4"])
    assert run(["--week", "4", "--season", "2026"], session) == 3
    session = FakeSession(["Week 1", "Week 2", "Week 3"])
    assert run(["--week", "4", "--season", "2026"], session) == 0
    assert set(built[-1]["pool"]) == {"Week 1", "Week 2", "Week 3"}


# --- xlsx export of a pool (the watchdog's input) -------------------------------------

import csv as _csv  # noqa: E402
import re as _re  # noqa: E402
import zipfile as _zipfile  # noqa: E402
from xml.sax.saxutils import escape as _esc  # noqa: E402

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def xlsx_from_pool(pool: dict[str, list[list[str]]]) -> bytes:
    """An xlsx shaped like a Google Sheets download: sharedStrings, relative rel
    targets, numbers as bare <v>, and empty cells omitted (as Excel does)."""
    strings: list[str] = []
    index: dict[str, int] = {}
    sheets_xml = []
    for rows in pool.values():
        out = []
        for r, cells in enumerate(rows, start=1):
            cs = []
            for c, value in enumerate(cells):
                if value == "":
                    continue                       # Excel omits empty cells entirely
                ref = f"{mps.col_letter(c)}{r}"
                if _re.fullmatch(r"-?\d+(\.\d+)?", value):
                    cs.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    if value not in index:
                        index[value] = len(strings)
                        strings.append(value)
                    cs.append(f'<c r="{ref}" t="s"><v>{index[value]}</v></c>')
            out.append(f'<row r="{r}">{"".join(cs)}</row>')
        sheets_xml.append(f'<worksheet xmlns="{MAIN_NS}"><sheetData>{"".join(out)}</sheetData></worksheet>')
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        entries = "".join(f'<sheet name="{_esc(t)}" sheetId="{i}" r:id="rId{i}"/>'
                          for i, t in enumerate(pool, start=1))
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN_NS}" xmlns:r="r"><sheets>{entries}'
                                       '</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>'
                       for i in range(1, len(pool) + 1))
        zf.writestr("xl/_rels/workbook.xml.rels", f"<Relationships>{rels}</Relationships>")
        for i, xml in enumerate(sheets_xml, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", xml)
        sst = "".join(f'<si><t xml:space="preserve">{_esc(s)}</t></si>' for s in strings)
        zf.writestr("xl/sharedStrings.xml", f'<sst xmlns="{MAIN_NS}">{sst}</sst>')
    return buf.getvalue()


def write_xlsx(tmp_path, pool, name="pool.xlsx") -> Path:
    path = tmp_path / name
    path.write_bytes(xlsx_from_pool(pool))
    return path


def test_xlsx_from_pool_round_trips_through_the_generators_reader():
    pool = {"nfl_week_3_2026_pickem": [HEADER, ["3", "", "ATL@GB", "GB", "-5.5"]]}
    assert mps.load_sheets(xlsx_from_pool(pool)) == pool


def _pick_row(week, matchup, ats, picks, extra=()):
    away, home = matchup.split("@")
    return [str(week), "2026-09-13 13:00 ET", matchup, home, "-3.5", "DraftKings", "", ats, *picks, *extra]


SIX = ["Ann - The Alpha", "Bo - The Bravo", "Cy - The Charlie", "Di - The Delta", "Ed - The Echo",
       "Flo - The Foxtrot"]


def _six_header(record):
    return FIXED + [f"{n} ({record})" for n in SIX]


def six_player_pool():
    """Weeks 1-3 of 2026 (graded, messy, partly ungraded), a stale 2025 tab, a
    non-week tab, and notes past column N in the latest tab, which the Sheets
    API read (A1:N) never sees."""
    return {
        "Standings": [["player", "wins"], ["Ann", "3"]],
        "nfl_week_18_2025_pickem": [_six_header("100-100"), _pick_row(18, "NE@SEA", "SEA", ["SEA"] * 6)],
        "nfl_week_1_2026_pickem": [
            _six_header("100-100"),
            _pick_row(1, "NE@SEA", "NE", ["NE", "Ne ", "SEA", "", "ne", "SEA"]),
            _pick_row(1, "WAS@PHI", "WAS", ["WSH", "PHI", "was", "WAS", "", "PHI"]),
            _pick_row(1, "DEN@KC", "PUSH", ["KC", "", "DEN", "KC", "KC", "DEN"]),
            _pick_row(1, "NYJ@TEN", "TEN", ["NY", "TEN", "dale", "TEN", "TEN", ""]),
        ],
        "nfl_week_2_2026_pickem": [
            _six_header("0-0"),
            _pick_row(2, "DET@BUF", "BUF", ["BUF", "DET", "buf", "BUF", "DET", "BUF"]),
            _pick_row(2, "CAR@ATL", "", ["ATL", "CAR", "atl", "", "", ""]),
        ],
        "nfl_week_3_2026_pickem": [
            _six_header("9-9") + ["", "notes"],
            _pick_row(3, "ATL@GB", "GB", ["GB", "GB", "ATL", "GB", "ATL", "GB"], extra=["", "n/a"]),
        ],
    }


def api_values(pool):
    """What values:batchGet returns for A1:N: at most 14 columns, trailing blanks dropped."""
    out = {}
    for title, rows in pool.items():
        sliced = []
        for r in rows:
            r = list(r[:14])
            while r and r[-1] == "":
                r.pop()
            sliced.append(r)
        out[title] = sliced
    return out


IND_WSH = {"id": "e2", "sport_key": "americanfootball_nfl", "sport_title": "NFL",
           "commence_time": "2026-10-04T17:00:00Z",
           "home_team": "Washington Commanders", "away_team": "Indianapolis Colts"}
IND_WSH_ODDS = {**IND_WSH, "bookmakers": [{
    "key": "fanduel", "title": "FanDuel", "last_update": "2026-09-30T12:00:00Z",
    "markets": [{"key": "spreads", "outcomes": [
        {"name": "Indianapolis Colts", "price": -110, "point": 3},
        {"name": "Washington Commanders", "price": -110, "point": -3}]}]}]}
JAX_CIN = {"id": "e3", "sport_key": "americanfootball_nfl", "sport_title": "NFL",
           "commence_time": "2026-10-04T17:00:00Z",
           "home_team": "Cincinnati Bengals", "away_team": "Jacksonville Jaguars"}


@pytest.fixture
def week4_api(monkeypatch):
    """Wednesday 2026-09-30 06:00 ET (week 4): a half-point line, a whole-number
    line from the second-choice book, and a game with no odds yet."""
    api = FakeOddsAPI([IND_WSH, PIT_CLE, JAX_CIN], [IND_WSH_ODDS, PIT_CLE_ODDS])
    monkeypatch.setattr(mps.urllib.request, "urlopen", api.urlopen)
    monkeypatch.setattr(mps, "now_et", lambda: datetime(2026, 9, 30, 6, 0, tzinfo=mps.ET_ZONE))
    for var in (mps.ENV_SHEET_URL, mps.ENV_BOOKMAKERS, mps.ENV_SEASON_START):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(mps.ENV_ODDS_KEY, API_KEY)
    mps._ODDS_CACHE.clear()
    yield api
    mps._ODDS_CACHE.clear()


def _push_values(pool):
    session = FakeSession(list(pool), values=api_values(pool))
    assert run([], session) == 0
    (_, url, body), = session.writes()
    return url, body["values"]


def test_workbook_csv_equals_what_the_push_writes(week4_api, tmp_path, capsys):
    """The watchdog's fallback (generator CLI on an exported .xlsx, no --week)
    produces exactly the cells the push would write for the same pool and slate."""
    pool = six_player_pool()
    url, pushed = _push_values(pool)
    assert "'nfl_week_4_2026_pickem'!A1:N4" in url
    capsys.readouterr()

    mps._ODDS_CACHE.clear()
    out = tmp_path / "week.csv"
    assert mps.main(["--workbook", str(write_xlsx(tmp_path, pool)), "--out", str(out)]) == 0
    with out.open(newline="") as fh:
        written = list(_csv.reader(fh))

    assert written == [[str(c) for c in row] for row in pushed]
    assert all(len(r) == 14 for r in written)
    assert written[0][8:] == ["Ann - The Alpha (4-1)", "Bo - The Bravo (3-2; 1 missed)",
                              "Cy - The Charlie (2-2)", "Di - The Delta (4-0; 1 missed)",
                              "Ed - The Echo (2-2; 1 missed)", "Flo - The Foxtrot (2-2; 1 missed)"]
    assert [r[2] for r in written[1:]] == ["PIT@CLE", "IND@WSH", "JAX@CIN"]
    assert [r[0] for r in written[1:]] == ["4", "4", "4"]
    assert [r[4] for r in written[1:]] == ["-3.5", "-3", ""]
    err = capsys.readouterr().err
    assert "tab_name: nfl_week_4_2026_pickem" in err.splitlines()
    assert API_KEY not in err


def test_workbook_csv_to_stdout_matches_push_too(week4_api, tmp_path, capsys):
    pool = six_player_pool()
    _, pushed = _push_values(pool)
    capsys.readouterr()
    mps._ODDS_CACHE.clear()
    assert mps.main(["--workbook", str(write_xlsx(tmp_path, pool)), "--out", "-"]) == 0
    captured = capsys.readouterr()
    assert list(_csv.reader(io.StringIO(captured.out))) == [[str(c) for c in r] for r in pushed]


def test_generator_tab_name_follows_the_pools_pattern(week4_api, tmp_path, capsys):
    pool = {"Standings": [["x"]], "Week 3": six_player_pool()["nfl_week_3_2026_pickem"]}
    assert mps.main(["--workbook", str(write_xlsx(tmp_path, pool)), "--out", str(tmp_path / "o.csv")]) == 0
    assert "tab_name: Week 4" in capsys.readouterr().err.splitlines()


def test_generator_workbook_refuses_what_the_push_refuses(week4_api, tmp_path, capsys):
    pool = six_player_pool()
    pool["nfl_week_3_2026_pickem"][0][9] = "=HYPERLINK(1)"
    out = tmp_path / "o.csv"
    assert mps.main(["--workbook", str(write_xlsx(tmp_path, pool)), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "J1" in err and "HYPERLINK" not in err
    assert not out.exists()


def test_generator_workbook_without_a_pickem_tab_fails_unless_allowed(week4_api, tmp_path, capsys):
    path = write_xlsx(tmp_path, {"Sheet1": [["notes"]]})
    out = tmp_path / "o.csv"
    assert mps.main(["--workbook", str(path), "--out", str(out)]) == 1
    assert "header_fallback" in capsys.readouterr().err and not out.exists()
    mps._ODDS_CACHE.clear()
    assert mps.main(["--workbook", str(path), "--out", str(out), "--allow-header-fallback"]) == 0
    with out.open(newline="") as fh:
        assert next(_csv.reader(fh))[8:] == [f"player_{i}" for i in range(1, 7)]


# --- --check-tab --------------------------------------------------------------------


@pytest.fixture
def no_build(monkeypatch):
    """--check-tab must never build the sheet (no Odds API credit)."""
    def boom(*args, **kwargs):
        raise AssertionError("--check-tab must not build the sheet")
    monkeypatch.setattr(mps, "build_sheet", boom)
    monkeypatch.setattr(mps, "fetch_week_slate", boom)


SHEET_ONLY = {"PICKEM_SPREADSHEET_ID": SHEET_ID}      # no ODDS_API_KEY: not needed


def test_check_tab_reports_existing_tab_and_exits_0(no_build, monkeypatch, capsys):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)            # Wednesday noon ET, week 8
    session = FakeSession(["nfl_week_7_2026_pickem", "nfl_week_8_2026_pickem"])
    assert run(["--check-tab"], session, env=SHEET_ONLY) == 0
    captured = capsys.readouterr()
    assert captured.out == "week 8 2026: tab exists\n"
    assert captured.err == ""


def test_check_tab_reports_missing_tab_with_its_name_and_exits_4(no_build, monkeypatch, capsys):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    session = FakeSession(["Sheet1", "nfl_week_6_2026_pickem", "nfl_week_7_2026_pickem"])
    assert run(["--check-tab"], session, env=SHEET_ONLY) == push.EXIT_MISSING == 4
    assert capsys.readouterr().out == "week 8 2026: tab missing (would be named nfl_week_8_2026_pickem)\n"


def test_check_tab_reads_only_tab_metadata(no_build, monkeypatch, capsys):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    session = FakeSession(["nfl_week_7_2026_pickem"])
    assert run(["--check-tab"], session, env=SHEET_ONLY) == 4
    assert len(session.calls) == 1
    method, url, _ = session.calls[0]
    assert method == "GET" and "fields=sheets.properties" in url and "values" not in url
    captured = capsys.readouterr()
    assert SHEET_ID not in captured.out + captured.err


def test_check_tab_uses_the_same_conflict_rule_as_the_push(no_build, monkeypatch, capsys):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    # Any week-8 tab of the season counts, whatever its naming.
    assert run(["--check-tab"], FakeSession(["Week 7", "Week 8"]), env=SHEET_ONLY) == 0
    assert run(["--check-tab"], FakeSession(["Week 7"]), env=SHEET_ONLY) == 4
    assert capsys.readouterr().out.splitlines()[-1] == "week 8 2026: tab missing (would be named Week 8)"
    # A leftover yearless tab doesn't belong to a season once tabs carry years.
    session = FakeSession(["Week 8", "nfl_week_7_2026_pickem"])
    assert run(["--check-tab"], session, env=SHEET_ONLY) == 4
    assert push.conflicting_tab(session.tabs, "nfl_week_8_2026_pickem", 8, 2026) is None


@pytest.mark.parametrize("now", [(2027, 1, 20, 12, 0), (2027, 7, 14, 12, 0), (2026, 9, 2, 12, 0)])
def test_check_tab_off_season_exits_0_without_any_call(no_build, monkeypatch, capsys, now):
    freeze(monkeypatch, *now)
    session = FakeSession(["nfl_week_18_2026_pickem"])
    assert run(["--check-tab"], session, env=SHEET_ONLY) == 0
    out = capsys.readouterr().out
    assert out.startswith("off-season") and len(out.splitlines()) == 1
    assert session.calls == []


def test_check_tab_explicit_week_and_season(no_build, monkeypatch, capsys):
    freeze(monkeypatch, 2027, 7, 14, 12, 0)             # off-season, but --week bypasses it
    session = FakeSession(["nfl_week_18_2026_pickem"])
    assert run(["--check-tab", "--week", "1", "--season", "2027"], session, env=SHEET_ONLY) == 4
    assert "week 1 2027: tab missing (would be named nfl_week_1_2027_pickem)" in capsys.readouterr().out


def test_check_tab_with_workbook_needs_no_google_or_odds_credentials(no_build, monkeypatch, capsys,
                                                                    tmp_path):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    have = write_xlsx(tmp_path, {"nfl_week_7_2026_pickem": [HEADER], "nfl_week_8_2026_pickem": [HEADER]},
                      "have.xlsx")
    lack = write_xlsx(tmp_path, {"Standings": [["x"]], "nfl_week_7_2026_pickem": [HEADER]}, "lack.xlsx")
    assert push.main(["--check-tab", "--workbook", str(have)], env={}, session_factory=no_session) == 0
    assert push.main(["--check-tab", "--workbook", str(lack)], env={}, session_factory=no_session) == 4
    out = capsys.readouterr().out.splitlines()
    assert out == ["week 8 2026: tab exists",
                   "week 8 2026: tab missing (would be named nfl_week_8_2026_pickem)"]


def test_check_tab_makes_no_odds_api_request(odds_api, capsys, monkeypatch):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    assert run(["--check-tab"], FakeSession(["nfl_week_7_2026_pickem"])) == 4
    assert odds_api.requests == []
    assert "remaining" not in capsys.readouterr().err


@pytest.mark.parametrize("argv,env", [
    (["--check-tab"], {}),                                                  # no sheet, no workbook
    (["--check-tab", "--workbook", "/nonexistent/pool.xlsx"], {}),          # unreadable workbook
    (["--check-tab", "--season-start", "Sept 10"], SHEET_ONLY),
    (["--check-tab"], {**SHEET_ONLY, "PICKEM_SEASON_START": "2025-09-04"}),  # stale opener
    (["--check-tab", "--week", "19"], SHEET_ONLY),
    (["--check-tab", "--force"], SHEET_ONLY),
    (["--check-tab", "--dry-run"], SHEET_ONLY),
])
def test_check_tab_config_errors_exit_2_before_any_call(no_build, monkeypatch, capsys, argv, env):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    session = FakeSession(["nfl_week_7_2026_pickem"])
    assert run(argv, session, env=env) == 2
    assert session.calls == []
    assert capsys.readouterr().out == ""


def test_check_tab_not_a_zip_workbook_is_exit_2(no_build, monkeypatch, tmp_path):
    freeze(monkeypatch, 2026, 10, 28, 12, 0)
    bad = tmp_path / "pool.xlsx"
    bad.write_bytes(b"PK-not-really")
    assert push.main(["--check-tab", "--workbook", str(bad)], env={}, session_factory=no_session) == 2


# --- the watchdog path keeps pool data private -------------------------------------


def test_generator_workbook_warnings_hide_names_and_picks_by_default(week4_api, tmp_path, capsys):
    pool = six_player_pool()
    pool["nfl_week_3_2026_pickem"][1][8] = "ZZ-RAW-PICK"  # unresolvable pick text
    path = write_xlsx(tmp_path, pool)
    assert mps.main(["--workbook", str(path), "--out", str(tmp_path / "o.csv")]) == 0
    err = capsys.readouterr().err
    assert "unresolvable picks" in err
    assert "ZZ-RAW-PICK" not in err and "Ann - The Alpha" not in err

    mps._ODDS_CACHE.clear()
    assert mps.main(["--workbook", str(path), "--out", str(tmp_path / "o.csv"), "--show-values"]) == 0
    assert "ZZ-RAW-PICK" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["leak.csv", "scripts/leak.csv"])
def test_generator_workbook_refuses_to_write_pool_data_inside_the_repo(week4_api, tmp_path, capsys, name):
    out = Path(mps.__file__).resolve().parents[1] / name
    path = write_xlsx(tmp_path, six_player_pool())
    assert mps.main(["--workbook", str(path), "--out", str(out)]) == 2
    assert "inside the repo" in capsys.readouterr().err
    assert not out.exists()


def test_generator_default_out_inside_repo_is_refused_with_a_workbook(week4_api, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(Path(mps.__file__).resolve().parents[1])
    assert mps.main(["--workbook", str(write_xlsx(tmp_path, six_player_pool()))]) == 2
    assert "inside the repo" in capsys.readouterr().err
