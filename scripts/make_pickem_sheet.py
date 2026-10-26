#!/usr/bin/env python3
"""Generate a weekly NFL pick'em sheet from The Odds API's slate + the pool's own sheet.

Matchups, kickoffs and spreads come from The Odds API v4 (env ODDS_API_KEY; one
credit per run), with the spread taken from the first bookmaker in the
preference list (--bookmakers / PICKEM_BOOKMAKERS). The week is resolved from
the season opener (derived, or --season-start / PICKEM_SEASON_START). Player
columns come from the pool spreadsheet itself: names from the latest week tab's
header, and each player's "(W-L)" computed from this season's earlier week tabs
(pick vs. `ats_winner`). Nothing pool-specific is hard-coded.

Stdlib only, so it runs with the system python3 (the repo .venv is a Linux
container venv and does not work on macOS).

    ODDS_API_KEY=... PICKEM_PUBLISHED_CSV_URL=https://docs.google.com/.../pubhtml \\
        python3 scripts/make_pickem_sheet.py --week 3
    python3 scripts/make_pickem_sheet.py --workbook pool.xlsx --week 3
    python3 scripts/make_pickem_sheet.py --workbook pool.xlsx --out week.csv
    python3 scripts/make_pickem_sheet.py --serve 8000

With --workbook (a downloaded copy of the pool sheet) the CSV holds exactly the
14 columns and rows push_pickem_to_sheets.py would write for the same inputs,
and anything the push refuses is an error here too. The new tab's name, from
the pool's own naming pattern, is printed on stderr as `tab_name: <name>`.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from xml.parsers import expat
from zoneinfo import ZoneInfo

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"m": MAIN_NS}
ET_ZONE = ZoneInfo("America/New_York")

FIXED_COLUMNS = ["week", "kickoff_et", "matchup", "favorite", "spread",
                 "spread_source", "spread_as_of_et", "ats_winner"]
N_PLAYERS = 6
# The pool sheet's columns, A:N. The push reads and writes only these.
N_COLUMNS = len(FIXED_COLUMNS) + N_PLAYERS
DEFAULT_TAB_PATTERN = "nfl_week_{week}_{season}_pickem"

# Optional: a publish-to-web URL of the pool sheet, for local runs of this
# stdlib-only generator (the push script reads the sheet through the Sheets API
# instead). No default. Any /pubhtml or /pub?output=csv form is accepted and
# fetched as the whole workbook (/pub?output=xlsx), because the CSV form only
# ever returns the FIRST tab, whose header is stale.
ENV_SHEET_URL = "PICKEM_PUBLISHED_CSV_URL"

# Sent on every outbound request (The Odds API, the published pool sheet).
USER_AGENT = ("sportsbook-assistant-pickem/1.0 (weekly NFL pick'em sheet generator; "
              "+https://github.com/edwin-lobo/sportsbook-assistant)")

# Mirrors TEAM_ALIASES in src/app/services/grading.py (not imported: this module
# stays stdlib-only and never imports `app`). tests/test_make_pickem_sheet.py
# asserts the two stay identical.
TEAM_ALIASES = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR"}
PUSH = "PUSH"

WEEK_RE = re.compile(r"week[ _](\d+)", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
RECORD_RE = re.compile(r"\s*\(\s*\d+\s*-\s*\d+\s*(?:;[^)]*)?\)\s*$")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Total declared uncompressed size of an xlsx's parts (zip-bomb guard). zipfile
# never inflates a member past its declared file_size (a lie fails the CRC).
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
# --serve sends CORS headers for this one origin only (e.g. a Vite dev server).
# No default: without it the service is same-origin only.
ENV_SERVE_ORIGIN = "PICKEM_SERVE_ORIGIN"

ERROR_CODES = {400: "bad_request", 403: "forbidden", 404: "not_found", 413: "too_large",
               415: "unsupported_media_type", 500: "internal_error", 502: "upstream_error"}


class SheetError(Exception):
    """Something went wrong that the caller should see as a clean message."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status

    @property
    def code(self) -> str:
        return ERROR_CODES.get(self.status, "error")


def now_et() -> datetime:
    """Current time in ET. A function so tests can freeze it."""
    return datetime.now(ET_ZONE)


def current_season(now: datetime | None = None) -> int:
    """NFL season year: January/February games belong to the previous year's season."""
    now = now or now_et()
    return now.year if now.month >= 3 else now.year - 1


def normalize_team(code: str) -> str:
    code = (code or "").strip().upper()
    return TEAM_ALIASES.get(code, code)


canon = normalize_team


def join_key(away: str, home: str) -> str:
    return f"{canon(away)}@{canon(home)}"


def col_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


# --- xlsx reading -----------------------------------------------------------


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _refuse_doctype(*_args) -> None:
    raise SheetError("xlsx part contains a <!DOCTYPE> declaration; refusing to parse it "
                     "(entity-expansion guard)")


def _xml_part(zf: zipfile.ZipFile, name: str) -> bytes:
    """One part's bytes, refused if it declares a DOCTYPE.

    System expat on macOS (2.2.8) predates the billion-laughs mitigation, and
    real xlsx parts never carry a DOCTYPE. An expat pre-pass stops at the
    DOCTYPE's first token, before any entity is declared or expanded, whatever
    the part's encoding (UTF-8, UTF-16, ...).
    """
    try:
        data = zf.read(name)
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise SheetError(f"not a readable .xlsx file: corrupt part ({type(exc).__name__})") from None
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _refuse_doctype
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise SheetError(f"not a readable .xlsx file: malformed XML part ({exc.code})") from None
    return data


def load_sheets(source: Path | bytes) -> dict[str, list[list[str]]]:
    """Read every tab of an xlsx into {tab name: rows}.

    Handles both shapes seen in this project: a local schedule file uses inline
    strings with absolute rel targets, while a workbook downloaded from Google
    Sheets uses sharedStrings with relative ones. Cells are placed by their `r`
    column reference because Excel omits empty cells entirely -- parsing
    positionally shifts every later column left and silently corrupts picks.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    except (zipfile.BadZipFile, FileNotFoundError) as exc:
        raise SheetError(f"not a readable .xlsx file: {exc}") from exc

    if sum(info.file_size for info in zf.infolist()) > MAX_UNCOMPRESSED_BYTES:
        raise SheetError(f"xlsx expands to more than {MAX_UNCOMPRESSED_BYTES} bytes uncompressed; "
                         "refusing to read it")
    names = set(zf.namelist())
    if "xl/workbook.xml" not in names:
        raise SheetError("file is a zip but not an .xlsx workbook")

    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(_xml_part(zf, "xl/sharedStrings.xml"))
        for si in root.iter(f"{{{MAIN_NS}}}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{MAIN_NS}}}t")))

    def resolve(target: str) -> str:
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join("xl", target))

    rels: dict[str, str] = {}
    for tag in re.findall(r"<Relationship[^>]*>", _xml_part(zf, "xl/_rels/workbook.xml.rels").decode()):
        rid = re.search(r'Id="([^"]+)"', tag)
        tgt = re.search(r'Target="([^"]+)"', tag)
        if rid and tgt:
            rels[rid.group(1)] = resolve(tgt.group(1))

    sheets: dict[str, list[list[str]]] = {}
    for tag in re.findall(r"<sheet[^>]*/?>", _xml_part(zf, "xl/workbook.xml").decode()):
        name_m = re.search(r'name="([^"]+)"', tag)
        rid_m = re.search(r'r:id="([^"]+)"', tag)
        if not (name_m and rid_m) or rid_m.group(1) not in rels:
            continue
        root = ET.fromstring(_xml_part(zf, rels[rid_m.group(1)]))
        rows: list[list[str]] = []
        for row in root.iter(f"{{{MAIN_NS}}}row"):
            cells: dict[int, str] = {}
            for c in row.iter(f"{{{MAIN_NS}}}c"):
                ref = c.get("r") or ""
                val_el = c.find("m:v", NS)
                inline = c.find("m:is/m:t", NS)
                if inline is not None:
                    value = inline.text or ""
                elif val_el is None:
                    value = ""
                elif c.get("t") == "s":
                    value = shared[int(val_el.text)]
                else:
                    value = val_el.text or ""
                cells[_col_index(ref) if ref else len(cells)] = value
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
        sheets[name_m.group(1)] = rows
    return sheets


def week_of(tab_name: str) -> int | None:
    """Week number from 'Week 3', 'nfl_week_3_2026_pickem', 'NFL Week 3 - 2026'."""
    m = WEEK_RE.search(tab_name)
    return int(m.group(1)) if m else None


def week_tab_keys(titles, season: int) -> dict[str, tuple[int, int]]:
    """(season, week) for each week tab among `titles`; other tabs are left out.

    A title with a 4-digit year belongs to that season. A yearless title
    ("Week 3") belongs to `season` only if none of the week tabs carries a
    year; otherwise it belongs to no season and is left out too. So a leftover
    yearless tab can't block next season's week or count in its records.
    """
    weeks = {t: week_of(t) for t in titles}
    weeks = {t: w for t, w in weeks.items() if w is not None}
    stamped = any(YEAR_RE.search(t) for t in weeks)
    out = {}
    for title, week in weeks.items():
        years = YEAR_RE.findall(title)
        if years:
            out[title] = (int(years[0]), week)
        elif not stamped:
            out[title] = (season, week)
    return out


def week_tabs(sheets: dict[str, list[list[str]]]) -> dict[int, list[list[str]]]:
    out: dict[int, list[list[str]]] = {}
    for name, rows in sheets.items():
        wk = week_of(name)
        if wk is not None:
            out[wk] = rows
    return out


def schedule_rows(sheets: dict[str, list[list[str]]], week: int) -> list[dict]:
    tabs = week_tabs(sheets)
    if week not in tabs:
        raise SheetError(f"no tab for week {week} in the schedule file")
    rows = []
    for i, cells in enumerate(tabs[week]):
        if i == 0:
            continue
        matchup = cells[2] if len(cells) > 2 else ""
        if "@" not in matchup:
            continue
        rows.append({"kickoff_et": cells[1] if len(cells) > 1 else "", "matchup": matchup})
    return rows


def upcoming_week(sheets: dict[str, list[list[str]]], now: datetime | None = None) -> int:
    """First week in an xlsx schedule whose last kickoff hasn't passed yet.

    After Monday Night Football kicks off, the week flips, so a Wednesday run
    resolves to the new week.
    """
    now = now or now_et()
    for week in sorted(week_tabs(sheets)):
        stamps = []
        for row in schedule_rows(sheets, week):
            try:
                stamps.append(
                    datetime.strptime(row["kickoff_et"].replace(" ET", ""), "%Y-%m-%d %H:%M").replace(tzinfo=ET_ZONE)
                )
            except ValueError:
                pass
        if stamps and max(stamps) >= now:
            return week
    return max(week_tabs(sheets), default=1)


# --- tab naming ---------------------------------------------------------------


def detect_tab_pattern(titles, default: str = DEFAULT_TAB_PATTERN) -> str:
    """Naming pattern of the pool's week tabs, from the latest one.

    'nfl_week_3_2026_pickem' -> 'nfl_week_{week}_{season}_pickem';
    'Week 3' -> 'Week {week}'. Falls back to `default` with no week tabs.
    """
    best, best_key = None, None
    for title in titles:
        m = WEEK_RE.search(title)
        if not m:
            continue
        years = YEAR_RE.findall(title)
        key = (int(years[0]) if years else 0, int(m.group(1)))
        if best_key is None or key > best_key:
            best, best_key = title, key
    if best is None:
        return default

    spans = [(m.start(1), m.end(1), "{week}") for m in [WEEK_RE.search(best)]]
    year = YEAR_RE.search(best)
    if year:
        spans.append((year.start(1), year.end(1), "{season}"))
    out, pos = [], 0
    for start, end, token in sorted(spans):
        out.append(best[pos:start].replace("{", "{{").replace("}", "}}"))
        out.append(token)
        pos = end
    out.append(best[pos:].replace("{", "{{").replace("}", "}}"))
    return "".join(out)


def tab_title(pattern: str, week: int, season: int) -> str:
    return pattern.format(week=week, season=season)


def intended_tab_name(titles, week: int, season: int) -> str:
    """Name for the new week's tab, following the pool's existing week tabs."""
    return tab_title(detect_tab_pattern(titles), week, season)


def pool_tab_titles(titles, season: int) -> list[str]:
    """The week tabs the pool is read from: all of `season`'s, plus the latest
    earlier-season one (week 1's header). The push reads exactly these through
    the Sheets API; an xlsx is narrowed to the same set, so both give the same header."""
    keyed = [(k, title) for title, k in week_tab_keys(list(titles), season).items()]
    this_season = [title for k, title in keyed if k[0] == season]
    earlier = sorted((k, title) for k, title in keyed if k[0] < season)
    return this_season + ([earlier[-1][1]] if earlier else [])


def pool_slice(pool: dict[str, list[list[str]]], season: int) -> dict[str, list[list[str]]]:
    """`pool` as the push sees it: only pool_tab_titles, and only columns A:N."""
    return {title: [list(r[:N_COLUMNS]) for r in pool[title]] for title in pool_tab_titles(pool, season)}


# --- picks, records, headers --------------------------------------------------


def resolve_pick(cell: str, away: str, home: str) -> str | None:
    """Resolve a messy pick cell against the two teams in its own row.

    Returns the normalized team code, "" for a blank cell (a missed pick), or
    None when the cell can't be resolved -- never a guess. Case-insensitive,
    alias-aware (WAS/WSH, JAC/JAX, LA/LAR), and a 2+ letter prefix is accepted
    only when it matches exactly one side ('NY' in NYJ@TEN, not in NYJ@NYG).
    """
    raw = (cell or "").strip().upper()
    if not raw:
        return ""
    sides = [(normalize_team(t), (t or "").strip().upper()) for t in (away, home)]
    code = normalize_team(raw)
    for norm, _spelled in sides:
        if code == norm:
            return norm
    if len(raw) >= 2:
        hits = {norm for norm, spelled in sides if norm.startswith(raw) or spelled.startswith(raw)}
        if len(hits) == 1:
            return hits.pop()
    return None


def strip_record(header: str) -> str:
    """'Name - Nick (5-11)' or 'Name - Nick (90-89; 62 missed)' -> 'Name - Nick'."""
    return RECORD_RE.sub("", header or "").strip()


@dataclass
class Record:
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    missed: int = 0
    unresolved: int = 0

    def label(self) -> str:
        """The sheet's own format: '(W-L)', plus '; N missed' only when N > 0.

        Pushes count as neither a W nor an L and are not shown: no week tab of
        the pool has ever displayed a push count.
        """
        text = f"{self.wins}-{self.losses}"
        if self.missed:
            text += f"; {self.missed} missed"
        return f"({text})"


def is_pickem_header(row: list[str]) -> bool:
    return len(row) > 8 and [c.strip().lower() for c in row[:8]] == FIXED_COLUMNS


def pickem_tabs(pool: dict[str, list[list[str]]], season: int) -> list[tuple[tuple[int, int], str, list]]:
    """Week tabs with a pick'em header row, sorted by (season, week)."""
    out = []
    keys = week_tab_keys(pool, season)
    for title, rows in pool.items():
        key = keys.get(title)
        if key and rows and is_pickem_header(rows[0]):
            out.append((key, title, rows))
    return sorted(out, key=lambda t: t[0])


def _norm_name(name: str) -> str:
    return " ".join(name.lower().split())


def _short_name(name: str) -> str:
    return _norm_name(name.split(" - ", 1)[0])


def _player_headers(header_row: list[str]) -> list[str]:
    players = [h.strip() for h in header_row[8:]]
    while players and not players[-1]:
        players.pop()
    return players


def compute_records(pool: dict[str, list[list[str]]], season: int, week: int,
                    players: list[str]) -> tuple[dict[str, Record], list[dict]]:
    """Each player's record from the week tabs of `season` before `week`.

    `players` are names without records. A column in an earlier tab is matched
    by full name, else by the part before ' - ' if that is unique (nicknames can
    change). Blank picks are missed, not losses. Unresolvable picks and results
    are reported and not counted. Blank `ats_winner` cells are reported as an
    `ungraded_week` and skipped.
    """
    records = {p: Record() for p in players}
    by_full = {_norm_name(p): p for p in players}
    by_short: dict[str, list[str]] = {}
    for p in players:
        by_short.setdefault(_short_name(p), []).append(p)

    def match(name: str) -> str | None:
        if _norm_name(name) in by_full:
            return by_full[_norm_name(name)]
        hits = by_short.get(_short_name(name), [])
        return hits[0] if len(hits) == 1 else None

    warnings: list[dict] = []
    for (tab_season, tab_week), title, rows in pickem_tabs(pool, season):
        if tab_season != season or tab_week >= week:
            continue
        columns = {}
        for i, header in enumerate(_player_headers(rows[0])):
            player = match(strip_record(header))
            letter = col_letter(8 + i)
            if player is None:
                warnings.append({
                    "kind": "unknown_player", "game": None,
                    "detail": f"{title} column {letter} ({strip_record(header)}) is not in the latest "
                              "header; its picks are not counted",
                    "public_detail": f"{title} column {letter} is not in the latest header; not counted",
                })
                continue
            columns[8 + i] = player

        ungraded, total = [], 0
        for cells in rows[1:]:
            cells = list(cells) + [""] * max(0, 8 + len(columns) + 8 - len(cells))
            matchup = cells[2].strip()
            if "@" not in matchup:
                continue
            total += 1
            away, home = matchup.split("@", 1)
            result = cells[7].strip()
            if not result:
                ungraded.append(matchup)
                continue
            winner = PUSH if result.upper() == PUSH else resolve_pick(result, away, home)
            if not winner:
                warnings.append({
                    "kind": "unresolved_result", "game": matchup,
                    "detail": f"{title}: ats_winner {result!r} is neither team; game not counted",
                    "public_detail": f"{title}: ats_winner is neither team; game not counted",
                })
                continue
            for idx, player in columns.items():
                cell = cells[idx] if idx < len(cells) else ""
                pick = resolve_pick(cell, away, home)
                rec = records[player]
                if pick == "":
                    rec.missed += 1
                elif pick is None:
                    rec.unresolved += 1
                    warnings.append({
                        "kind": "unresolved_pick", "game": matchup,
                        "detail": f"{title} column {col_letter(idx)} ({player}): {cell.strip()!r} is "
                                  "neither team; not counted",
                        "public_detail": f"{title} column {col_letter(idx)}: pick is neither team; "
                                         "not counted",
                    })
                elif winner == PUSH:
                    rec.pushes += 1
                elif pick == winner:
                    rec.wins += 1
                else:
                    rec.losses += 1
        if ungraded:
            warnings.append({
                "kind": "ungraded_week", "game": None,
                "detail": f"{title}: {len(ungraded)} of {total} games have no ats_winner and are not "
                          f"counted: {', '.join(ungraded)}",
            })
    return records, warnings


def latest_header_tab(pool: dict[str, list[list[str]]], season: int, week: int):
    """The latest pick'em tab before (season, week); else the latest one at all."""
    tabs = pickem_tabs(pool, season)
    earlier = [t for t in tabs if t[0] < (season, week)]
    return (earlier or tabs or [None])[-1]


def pool_header(pool: dict[str, list[list[str]]], season: int, week: int) -> tuple[list[str], str, list[dict]]:
    """Header for the new week: names from the latest tab, records computed.

    With no earlier week tab this season (week 1), the latest tab's header is
    carried through unchanged, so week 1 keeps last season's numbers.
    Only the tabs and columns the push reads (pool_slice) are used, whatever
    the source.
    """
    pool = pool_slice(pool, season)
    found = latest_header_tab(pool, season, week)
    if found is None:
        raise SheetError("the pool sheet has no week tab with a pick'em header row "
                         f"({', '.join(FIXED_COLUMNS)}, then player columns)")
    _key, title, rows = found
    players_raw = _player_headers(rows[0])
    prior = [t for t in pickem_tabs(pool, season) if t[0][0] == season and t[0][1] < week]
    if not prior:
        return (FIXED_COLUMNS + players_raw,
                f"tab {title} (records carried through: no earlier week tab for {season})", [])
    names = [strip_record(h) for h in players_raw]
    records, warnings = compute_records(pool, season, week, names)
    header = FIXED_COLUMNS + [f"{n} {records[n].label()}" for n in names]
    return header, f"tab {title}; records from {len(prior)} earlier week tab(s) of {season}", warnings


def configured_sheet_url(env=None) -> str | None:
    env = os.environ if env is None else env
    return (env.get(ENV_SHEET_URL) or "").strip() or None


def workbook_export_url(url: str) -> str:
    """Whole-workbook xlsx URL for a Google Sheets publish or edit URL."""
    m = re.match(r"(https://docs\.google\.com/spreadsheets/d/e/[A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1) + "/pub?output=xlsx"
    m = re.match(r"(https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1) + "/export?format=xlsx"
    return url


def fetch_pool(url: str) -> dict[str, list[list[str]]]:
    """Every tab of a published pool sheet. Errors never echo the URL."""
    req = urllib.request.Request(workbook_export_url(url), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            blob = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SheetError(f"could not read the published pool sheet ({type(exc).__name__})", 502) from None
    return load_sheets(blob)


def placeholder_header() -> list[str]:
    return FIXED_COLUMNS + [f"player_{i}" for i in range(1, N_PLAYERS + 1)]


def resolve_header(season: int, week: int, pool=None, workbook: bytes | None = None,
                   headers: str = "live", titles_out: list | None = None) -> tuple[list[str], str, list[dict]]:
    """Header row, where it came from, and warnings.

    Sources, first match wins: `pool` (tabs already read, e.g. via the Sheets
    API), an uploaded `workbook`, then `headers`: "live" (the sheet in
    PICKEM_PUBLISHED_CSV_URL), a URL, a .csv path (header row used as-is) or an
    .xlsx path. An unreadable upload is an error; anything else falls back to
    placeholder player columns with a `header_fallback` warning. If given,
    `titles_out` is extended with every tab title of the pool that was read.
    """
    titles_out = [] if titles_out is None else titles_out
    label = "pool sheet"
    if workbook:
        pool, label = load_sheets(workbook), "uploaded workbook"
    try:
        if pool is not None:
            pass
        elif headers == "live":
            url = configured_sheet_url()
            if not url:
                raise SheetError(f"no pool sheet configured: set {ENV_SHEET_URL}, or pass --workbook "
                                 "or --headers")
            pool, label = fetch_pool(url), "published pool sheet"
        elif headers.startswith(("http://", "https://")):
            pool, label = fetch_pool(headers), "published pool sheet"
        else:
            path = Path(headers)
            if path.suffix.lower() == ".csv":
                with path.open(newline="") as fh:
                    row = next(csv.reader(fh))
                if not is_pickem_header(row):
                    raise SheetError(f"{path} does not start with a pick'em header row")
                return row, f"{path} (header used as-is)", []
            pool, label = load_sheets(path), str(path)
        titles_out.extend(pool)
        header, source, warnings = pool_header(pool, season, week)
        return header, f"{label}: {source}", warnings
    except (SheetError, OSError, StopIteration) as exc:
        detail = str(exc) if isinstance(exc, SheetError) else type(exc).__name__
        return placeholder_header(), "placeholders", [{
            "kind": "header_fallback", "game": None,
            "detail": f"{detail}; wrote placeholder player columns",
        }]


# --- The Odds API ------------------------------------------------------------
#
# The only upstream this module calls on its own, kept behind fetch_week_slate
# so the source can be swapped again. Stdlib only (urllib); the backend's own
# connector (src/app/services/odds_api.py) is separate.
#
# Per run: GET /events (free, does not count against the quota) to list the
# full slate, then GET /odds with markets=spreads & regions=us, which costs
# markets x regions = 1 credit (0 if it returns no events). Both are cached for
# the rest of the process. The free tier is 500 credits a month.

ODDS_API_BASE = "https://api.the-odds-api.com"
ODDS_SPORT_PATH = "/v4/sports/americanfootball_nfl"
ENV_ODDS_KEY = "ODDS_API_KEY"
# macOS fallback when $ODDS_API_KEY is unset: a Keychain generic password with
# this service name (store it with: security add-generic-password -U -a "$USER" -s <name> -w).
KEYCHAIN_SERVICE = "sportsbook-odds-api"
ENV_BOOKMAKERS = "PICKEM_BOOKMAKERS"
ENV_SEASON_START = "PICKEM_SEASON_START"
DEFAULT_BOOKMAKERS = ("draftkings", "fanduel", "betmgm")
N_WEEKS = 18
# The week-1 tab is made by week 1's own Wednesday run (week 1 starts Wednesday
# 03:00 ET). Anything longer would create it a week early, with stale lines, and
# the next Wednesday's run would then hit exit 3 on the existing tab.
OFF_SEASON_LEAD_DAYS = 6

# The Odds API's full team names -> the pool sheet's codes. Explicit on purpose:
# an unknown name is reported (`unknown_team`), never guessed.
TEAM_CODES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WSH",
}
_TEAM_LOOKUP = {" ".join(k.lower().split()): v for k, v in TEAM_CODES.items()}

_ODDS_CACHE: dict[tuple, list] = {}


class ConfigError(SheetError):
    """Missing or invalid configuration (API key, bookmakers, season start)."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message, status)


class OffSeason(SheetError):
    """No week to generate: after week 18, or well before week 1."""


def log(message: str, secret: str | None = None) -> None:
    """Progress line on stderr. Always redacted."""
    print(redact(message, secret), file=sys.stderr)


def redact(text, secret: str | None) -> str:
    """`text` with the API key and any apiKey= query value replaced."""
    text = str(text or "")
    if secret:
        text = text.replace(secret, "<redacted>")
    return re.sub(r"(apiKey=)[^&\s'\"]*", r"\1<redacted>", text, flags=re.IGNORECASE)


def team_code(name: str) -> str | None:
    return _TEAM_LOOKUP.get(" ".join((name or "").lower().split()))


def keychain_secret(service: str) -> str | None:
    """The macOS Keychain generic password for `service`, or None (other OS, not found)."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    secret = proc.stdout.strip() if proc.returncode == 0 else ""
    return secret or None


def odds_api_key(value: str | None = None, env=None, keychain=None) -> str:
    """The Odds API key: argument, else $ODDS_API_KEY, else the macOS Keychain."""
    env = os.environ if env is None else env
    key = (value or env.get(ENV_ODDS_KEY) or "").strip()
    if not key:
        key = ((keychain or keychain_secret)(KEYCHAIN_SERVICE) or "").strip()
    if not key:
        raise ConfigError(f"missing environment variable {ENV_ODDS_KEY}: a key for The Odds API "
                          "(https://the-odds-api.com, v4). On macOS, store it once with: "
                          f'security add-generic-password -U -a "$USER" -s {KEYCHAIN_SERVICE} -w')
    return key


def bookmaker_preference(value=None, env=None) -> list[str]:
    """Bookmaker keys in preference order: argument, else $PICKEM_BOOKMAKERS, else the default."""
    env = os.environ if env is None else env
    if isinstance(value, (list, tuple)):
        raw = ",".join(value)
    else:
        raw = value if value not in (None, "") else env.get(ENV_BOOKMAKERS)
        if raw is None or not raw.strip():
            return list(DEFAULT_BOOKMAKERS)
    keys = [k.strip().lower() for k in raw.split(",") if k.strip()]
    if not keys or not all(re.fullmatch(r"[a-z0-9_]+", k) for k in keys):
        raise ConfigError(f"--bookmakers / {ENV_BOOKMAKERS} must be comma-separated Odds API "
                          "bookmaker keys, e.g. draftkings,fanduel,betmgm")
    return keys


def season_opener(season: int, value=None, env=None) -> date:
    """Opening day of `season`: argument, else $PICKEM_SEASON_START, else derived.

    Derived as the Thursday after Labor Day (first Monday of September), which
    has held for every recent season. A Wednesday opener in the same week (2026)
    lands in the same week-1 window. Set the override when the league opens later.
    """
    env = os.environ if env is None else env
    if isinstance(value, date):
        opener = value
    else:
        raw = (value or env.get(ENV_SEASON_START) or "").strip()
        if not raw:
            first = date(season, 9, 1)
            labor_day = first + timedelta(days=(0 - first.weekday()) % 7)
            return labor_day + timedelta(days=3)
        try:
            opener = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise ConfigError(f"--season-start / {ENV_SEASON_START} must be YYYY-MM-DD") from None
    if opener.year != season:
        raise ConfigError(f"--season-start / {ENV_SEASON_START} ({opener.isoformat()}) is not in "
                          f"the {season} season; update or unset it")
    return opener


def week_window(opener: date, week: int) -> tuple[datetime, datetime]:
    """Week `week` runs Wednesday 03:00 ET to the next Wednesday 02:59:59 ET.

    Week 1 starts on the Wednesday on or before the opener, so a Wednesday-
    morning run after MNF gets the new week. Local wall-clock times, DST-safe.
    """
    wednesday = opener - timedelta(days=(opener.weekday() - 2) % 7)
    start_day = wednesday + timedelta(days=7 * (week - 1))
    end_day = start_day + timedelta(days=7)
    start = datetime(start_day.year, start_day.month, start_day.day, 3, 0, tzinfo=ET_ZONE)
    end = datetime(end_day.year, end_day.month, end_day.day, 2, 59, 59, tzinfo=ET_ZONE)
    return start, end


def resolve_week(opener: date, now: datetime) -> int:
    """Regular-season week for `now`. Before the season: 1. After it: 18."""
    for week in range(1, N_WEEKS + 1):
        if now <= week_window(opener, week)[1]:
            return week
    return N_WEEKS


def season_week(opener: date, now: datetime, lead_days: int = OFF_SEASON_LEAD_DAYS) -> int | None:
    """Like resolve_week, but None in the off-season: after week 18 ends, or more
    than `lead_days` before week 1 starts."""
    if now < week_window(opener, 1)[0] - timedelta(days=lead_days):
        return None
    if now > week_window(opener, N_WEEKS)[1]:
        return None
    return resolve_week(opener, now)


def iso_z(dt: datetime) -> str:
    """The Odds API's ISO 8601 form: UTC, whole seconds, Z suffix."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _odds_get(endpoint: str, params: dict, api_key: str) -> list:
    """One GET against The Odds API. The key rides in the query string, so no URL,
    exception text or traceback from here is ever printed unredacted."""
    path = f"{ODDS_SPORT_PATH}/{endpoint}"
    cache_key = (endpoint, tuple(sorted(params.items())))
    if cache_key in _ODDS_CACHE:
        return _ODDS_CACHE[cache_key]
    url = f"{ODDS_API_BASE}{path}?{urlencode({'apiKey': api_key, **params})}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            headers = resp.headers
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            message = str(json.loads(exc.read()).get("message") or "")
        except Exception:  # noqa: BLE001
            message = ""
        # Redact the whole text before truncating, so no key prefix can survive,
        # and drop any URL outright.
        message = re.sub(r"https?://\S+", "<url>", redact(message, api_key))[:200]
        detail = f": {message}" if message else ""
        if status == 401:
            raise ConfigError(f"The Odds API rejected {ENV_ODDS_KEY} on {path} (HTTP 401){detail}",
                              502) from None
        if status == 429:
            detail += " (rate limited or out of credits; not retried)"
        raise SheetError(f"The Odds API {path} failed: HTTP {status}{detail}", 502) from None
    except Exception as exc:  # noqa: BLE001 - URLError, timeouts, anything carrying the URL
        raise SheetError(f"could not reach The Odds API {path} ({type(exc).__name__})", 502) from None
    try:
        data = json.loads(body)
    except ValueError:
        raise SheetError(f"The Odds API {path} returned something that is not JSON", 502) from None
    if not isinstance(data, list):
        raise SheetError(f"The Odds API {path} returned an unexpected shape (not a list)", 502)
    get = headers.get if hasattr(headers, "get") else (lambda _k: None)
    log(f"odds-api {path}: {len(data)} event(s), cost {get('x-requests-last') or '?'}, "
        f"remaining {get('x-requests-remaining') or '?'}", api_key)
    _ODDS_CACHE[cache_key] = data
    return data


@dataclass
class Game:
    away: str
    home: str
    kickoff_utc: datetime | None = None
    kickoff_et: str = ""
    favorite: str = ""
    spread: str = ""
    spread_source: str = ""
    spread_as_of_et: str = ""
    has_odds: bool = False
    warnings: list[dict] = field(default_factory=list)

    @property
    def matchup(self) -> str:
        return f"{self.away}@{self.home}"


def _event_fields(event, path: str) -> tuple[str, str, str, str]:
    try:
        eid, away, home, start = (event["id"], event["away_team"], event["home_team"],
                                  event["commence_time"])
        _utc(start)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SheetError(f"The Odds API {path} returned an event without id/teams/commence_time",
                         502) from None
    return str(eid), str(away), str(home), str(start)


def _pick_spread(event: dict, away: str, home: str, bookmakers: list[str]):
    """(favorite name or '', point, title, last_update) from the first preferred
    bookmaker with a usable spreads market, else None."""
    books = {b.get("key"): b for b in event.get("bookmakers") or [] if isinstance(b, dict)}
    for key in bookmakers:
        b = books.get(key)
        if not b:
            continue
        for market in b.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") != "spreads":
                continue
            points = {}
            for o in market.get("outcomes") or []:
                try:
                    points[o["name"]] = float(o["point"])
                except (KeyError, TypeError, ValueError):
                    continue
            if away not in points or home not in points:
                continue
            a, h = points[away], points[home]
            if a == 0 and h == 0:
                fav, point = "", 0.0
            elif a < 0 < h:
                fav, point = away, a
            elif h < 0 < a:
                fav, point = home, h
            else:
                continue  # malformed: fall through to the next bookmaker
            return fav, point, str(b.get("title") or key), b.get("last_update")
    return None


def fetch_week_slate(season: int, week: int, *, api_key: str | None = None,
                     bookmakers=None, season_start=None) -> list[Game]:
    """The week's slate from The Odds API, sorted by kickoff then matchup.

    One free /events call lists every game; one /odds call (1 credit) adds the
    spreads. Games with no odds yet come back with has_odds=False and no line.
    """
    key = odds_api_key(api_key)
    prefs = bookmaker_preference(bookmakers)
    opener = season_opener(season, season_start)
    start, end = week_window(opener, week)
    window = {"commenceTimeFrom": iso_z(start), "commenceTimeTo": iso_z(end)}

    events = _odds_get("events", {"dateFormat": "iso", **window}, key)
    if not events:
        raise SheetError(
            f"The Odds API lists no NFL games from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} ET "
            f"(week {week} of {season}, taking {opener.isoformat()} as opening day). If the season "
            f"opens on another date, set --season-start / {ENV_SEASON_START}. Games that have "
            "already been played are not listed.", status=502)
    odds = _odds_get("odds", {"regions": "us", "markets": "spreads", "oddsFormat": "american",
                              "dateFormat": "iso", **window}, key)

    by_id: dict[str, tuple[dict, dict | None]] = {}
    for ev in events:
        by_id[_event_fields(ev, "/events")[0]] = (ev, None)
    for ev in odds:
        eid = _event_fields(ev, "/odds")[0]
        by_id[eid] = (by_id.get(eid, (ev, None))[0], ev)

    games = []
    for base, priced in by_id.values():
        _eid, away_name, home_name, start_s = _event_fields(base, "/events")
        warnings = []
        codes = []
        for name in (away_name, home_name):
            code = team_code(name)
            if code is None:
                warnings.append({"kind": "unknown_team", "game": None,
                                 "detail": f"{name!r} is not in the 32-team table; written as-is"})
            codes.append(code or name.strip())
        utc = _utc(start_s)
        game = Game(away=codes[0], home=codes[1], kickoff_utc=utc,
                    kickoff_et=utc.astimezone(ET_ZONE).strftime("%Y-%m-%d %H:%M ET"),
                    has_odds=priced is not None, warnings=warnings)
        for w in warnings:
            w["game"] = game.matchup
        if priced is not None:
            picked = _pick_spread(priced, away_name, home_name, prefs)
            if picked:
                fav, point, title, updated = picked
                game.favorite = codes[0] if fav == away_name else codes[1] if fav else ""
                game.spread = f"{point:g}"
                game.spread_source = title
                try:
                    game.spread_as_of_et = _utc(updated).astimezone(ET_ZONE).strftime("%Y-%m-%d %H:%M ET")
                except (TypeError, ValueError, AttributeError):
                    game.spread_as_of_et = ""
        games.append(game)
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(games, key=lambda g: (g.kickoff_utc or far, g.matchup))


# --- building ---------------------------------------------------------------


@dataclass
class SheetResult:
    week: int
    season: int
    generated_at: str
    header: list[str]
    rows: list[list[str]]
    csv: str
    warnings: list[dict] = field(default_factory=list)
    header_source: str = ""
    # Name for the new tab, from the pool's own week-tab naming (default pattern
    # when no pool was read).
    tab_name: str = ""

    def to_dict(self) -> dict:
        return {
            "week": self.week,
            "season": self.season,
            "generated_at": self.generated_at,
            "header_source": self.header_source,
            "header": self.header,
            "rows": self.rows,
            "csv": self.csv,
            "warnings": self.warnings,
        }


# A text cell starting with one of these could be evaluated as a formula when
# the CSV is opened in Excel/Sheets/LibreOffice (CSV injection). Such cells get
# a leading "'". The Sheets push has its own, stricter check (it refuses them).
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "＝", "＋", "－", "＠")
CSV_CONTROL_PREFIXES = ("\t", "\r", "\n")  # checked before lstrip(), which would drop them
CSV_NUMERIC_COLUMNS = (0, 4)  # week, spread
_PLAIN_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")


def csv_safe_row(row: list) -> list[str]:
    """`row` with formula-like text cells prefixed by "'". A plain number in the
    week or spread column (e.g. -6.5) is data, not text, and is left alone."""
    out = []
    for i, cell in enumerate(row):
        text = "" if cell is None else str(cell)
        numeric = i in CSV_NUMERIC_COLUMNS and _PLAIN_NUMBER_RE.fullmatch(text)
        if not numeric and (text.startswith(CSV_CONTROL_PREFIXES)
                            or text.lstrip().startswith(CSV_FORMULA_PREFIXES)):
            text = "'" + text
        out.append(text)
    return out


def build_sheet(
    week: int | None = None,
    season: int | None = None,
    schedule: Path | None = None,
    workbook: bytes | None = None,
    headers: str = "live",
    pool: dict[str, list[list[str]]] | None = None,
    bookmakers=None,
    season_start=None,
    api_key: str | None = None,
) -> SheetResult:
    """Generate one week's sheet. The single entry point for CLI, HTTP and the push script.

    Games come from fetch_week_slate unless an optional xlsx `schedule` is
    given, in which case its rows are joined to the slate's lines. `bookmakers`,
    `season_start` and `api_key` default to their env vars.
    """
    now = now_et()
    season = season or current_season(now)
    sched = load_sheets(schedule) if schedule else None
    if week is None:
        week = (upcoming_week(sched, now) if sched is not None
                else resolve_week(season_opener(season, season_start), now))
    if not 1 <= week <= N_WEEKS:
        raise SheetError(f"week must be between 1 and {N_WEEKS}, got {week}")

    games = fetch_week_slate(season, week, api_key=api_key, bookmakers=bookmakers,
                             season_start=season_start)
    titles: list[str] = []
    header, header_src, header_warnings = resolve_header(season, week, pool=pool, workbook=workbook,
                                                         headers=headers, titles_out=titles)
    warnings: list[dict] = []
    stamp = now.strftime("%Y-%m-%d %H:%M ET")

    for g in games:
        warnings.extend(g.warnings)
    missing = [g.matchup for g in games if not g.has_odds]
    if missing:
        warnings.append({"kind": "missing_games", "game": None, "count": len(missing),
                         "detail": f"{len(missing)} of {len(games)} games have no odds listed yet "
                                   f"(written without a line): {', '.join(missing)}"})

    if sched is not None:
        by_key = {join_key(g.away, g.home): g for g in games}
        planned: list[tuple[Game, bool]] = []
        for row in schedule_rows(sched, week):
            away, home = row["matchup"].split("@", 1)
            game = by_key.get(join_key(away, home))
            if game is None:
                warnings.append({"kind": "unmatched", "game": row["matchup"],
                                 "detail": "not found in The Odds API's slate; row written without a line"})
                planned.append((Game(away=away, home=home, kickoff_et=row["kickoff_et"]), False))
            else:
                if not game.kickoff_et:
                    game.kickoff_et = row["kickoff_et"]
                planned.append((game, True))
    else:
        planned = [(g, True) for g in games]

    out_rows: list[list[str]] = []
    for game, matched in planned:
        matchup = game.matchup
        if matched:
            if not game.spread:
                detail = (f"none of the preferred bookmakers ({', '.join(bookmaker_preference(bookmakers))}) "
                          "lists a spread" if game.has_odds else "no odds listed yet")
                warnings.append({"kind": "no_line", "game": matchup, "detail": detail})
            elif float(game.spread) == 0:
                warnings.append({"kind": "pickem_line", "game": matchup,
                                 "detail": f"{game.spread_source} has it as a pick'em (0); no favorite"})
            elif float(game.spread).is_integer():
                warnings.append({"kind": "whole_number_line", "game": matchup,
                                 "detail": f"{game.favorite} {game.spread} can push"})
        out_rows.append(
            [str(week), game.kickoff_et, matchup, game.favorite, game.spread,
             game.spread_source, game.spread_as_of_et, ""]
            + [""] * max(0, len(header) - 8)
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(csv_safe_row(header))
    writer.writerows(csv_safe_row(r) for r in out_rows)

    return SheetResult(
        week=week, season=season, generated_at=stamp, header=header,
        rows=out_rows, csv=buf.getvalue(), warnings=header_warnings + warnings, header_source=header_src,
        tab_name=intended_tab_name(titles, week, season),
    )


# --- the push's values ----------------------------------------------------------
#
# The exact cells push_pickem_to_sheets.py writes. The generator CLI uses the
# same function for --workbook, so its CSV equals what the push would write.

PUSH_FORMULA_PREFIXES = CSV_FORMULA_PREFIXES
PUSH_CONTROL_PREFIXES = CSV_CONTROL_PREFIXES


class ValidationError(SheetError):
    """The sheet can't be written as is. Messages cite positions, never values."""


def _number(text: str, cell: str, integer: bool):
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValidationError(f"cell {cell} must be a number") from None
    if integer:
        if not value.is_integer():
            raise ValidationError(f"cell {cell} must be a whole number")
        return int(value)
    return int(value) if value.is_integer() else value


def sheet_values(result: SheetResult, allow_header_fallback: bool = False) -> list[list]:
    """Header plus rows, typed for the Sheets API's RAW input (week an int,
    spread a number). Refuses anything the push must not write: not exactly the
    pool's 14 columns, a blank player header, no games, a formula-like or
    control-character cell, or placeholder players without allow_header_fallback."""
    header = result.header
    if len(header) != N_COLUMNS:
        raise ValidationError(f"header must have the pool's {N_COLUMNS} columns; got {len(header)}")
    wrong = [col_letter(i) for i, (got, want) in enumerate(zip(header, FIXED_COLUMNS)) if got != want]
    if wrong:
        raise ValidationError(f"header columns {', '.join(wrong)} are not the pool's fixed columns "
                              f"({', '.join(FIXED_COLUMNS)})")
    blank = [col_letter(i) for i in range(len(FIXED_COLUMNS), N_COLUMNS) if not str(header[i]).strip()]
    if blank:
        raise ValidationError(f"player header cell(s) {', '.join(c + '1' for c in blank)} are blank")
    if not result.rows:
        raise ValidationError(f"week {result.week} has no games")

    typed: list[list] = [list(header)]
    for r, row in enumerate(result.rows, start=2):
        if len(row) != N_COLUMNS:
            raise ValidationError(f"row {r} has {len(row)} columns, expected {N_COLUMNS}")
        out = list(row)
        out[0] = _number(row[0], f"A{r}", integer=True)
        if out[0] != result.week:
            raise ValidationError(f"row {r} is not for week {result.week}")
        spread = str(row[4]).strip()
        out[4] = _number(spread, f"E{r}", integer=False) if spread else ""
        typed.append(out)

    for r, row in enumerate(typed, start=1):
        for c, cell in enumerate(row):
            if isinstance(cell, str) and (cell.startswith(PUSH_CONTROL_PREFIXES)
                                          or cell.lstrip().startswith(PUSH_FORMULA_PREFIXES)):
                raise ValidationError(f"cell {col_letter(c)}{r} starts with a formula or control "
                                      "character; refusing to write it")

    if not allow_header_fallback and any(w["kind"] == "header_fallback" for w in result.warnings):
        raise ValidationError(
            "header_fallback: player names could not be read from the pool sheet, so placeholder "
            "columns would be written. Fix the sheet, or pass --allow-header-fallback."
        )
    return typed


def values_csv(values: list[list]) -> str:
    """sheet_values as CSV text (the push's --show-values output, the generator's --workbook CSV)."""
    buf = io.StringIO()
    csv.writer(buf).writerows(values)
    return buf.getvalue()


# --- HTTP -------------------------------------------------------------------


def parse_multipart(body: bytes, content_type: str) -> dict[str, bytes]:
    """Minimal multipart/form-data parse. `cgi` was removed in Python 3.13."""
    raw = b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    msg = message_from_bytes(raw)
    fields: dict[str, bytes] = {}
    if not msg.is_multipart():
        return fields
    for part in msg.get_payload():
        name = part.get_param("name", header="content-disposition")
        if name:
            fields[name] = part.get_payload(decode=True) or b""
    return fields


class Handler(BaseHTTPRequestHandler):
    server_version = "pickem/1.0"
    schedule_path: Path | None = None
    season: int | None = None
    bookmakers: str | None = None
    season_start: str | None = None

    def log_message(self, fmt, *args):  # quieter, and to stderr
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _cors(self) -> None:
        origin = (os.environ.get(ENV_SERVE_ORIGIN) or "").strip()
        if not origin:
            return  # same-origin only; there is deliberately no "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _host_ok(self) -> bool:
        """DNS-rebinding guard: only loopback names with our own port."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self._forbidden = True
        self._json({"error": "Host header must be 127.0.0.1:<port> or localhost:<port>",
                    "detail": "forbidden"}, status=403)
        return False

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if not getattr(self, "_forbidden", False):
            self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _week_param(self, raw: str | None) -> int | None:
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except ValueError:
            raise SheetError(f"week must be a number, got {raw!r}") from None

    def _respond(self, week: int | None, workbook: bytes | None) -> None:
        try:
            result = build_sheet(
                week=week, season=self.season, schedule=self.schedule_path, workbook=workbook,
                bookmakers=self.bookmakers, season_start=self.season_start,
            )
        except SheetError as exc:
            self._json({"error": str(exc), "detail": exc.code}, status=exc.status)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"internal error ({type(exc).__name__})", "detail": "internal_error"},
                       status=500)
        else:
            self._json(result.to_dict())

    def do_OPTIONS(self):  # noqa: N802
        if not self._host_ok():
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._json({"status": "ok"})
        if parsed.path != "/sheet":
            return self._json({"error": "try /health or /sheet", "detail": "not_found"}, status=404)
        try:
            week = self._week_param(parse_qs(parsed.query).get("week", [None])[0])
        except SheetError as exc:
            return self._json({"error": str(exc), "detail": exc.code}, status=exc.status)
        self._respond(week, None)

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return
        if urlparse(self.path).path != "/sheet":
            return self._json({"error": "POST /sheet", "detail": "not_found"}, status=404)

        raw_length = (self.headers.get("Content-Length") or "").strip()
        if not raw_length.isdigit():  # missing, negative or non-numeric
            self.close_connection = True  # the unread body must not be parsed as a request
            return self._json({"error": "POST needs a non-negative integer Content-Length",
                               "detail": "bad_request"}, status=400)
        length = int(raw_length)
        if length > MAX_UPLOAD_BYTES:
            return self._json({"error": f"upload exceeds {MAX_UPLOAD_BYTES} bytes",
                               "detail": "too_large"}, status=413)
        body = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")

        week, workbook = None, None
        if ctype.startswith("multipart/form-data"):
            try:
                fields = parse_multipart(body, ctype)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"could not parse upload: {exc}", "detail": "bad_request"}, status=400)
            workbook = fields.get("workbook") or None
            if workbook:
                try:
                    load_sheets(workbook)  # size/DOCTYPE/format checks before any Odds API credit
                except SheetError as exc:
                    return self._json({"error": str(exc), "detail": exc.code}, status=exc.status)
            try:
                week = self._week_param((fields.get("week") or b"").decode().strip() or None)
            except SheetError as exc:
                return self._json({"error": str(exc), "detail": exc.code}, status=exc.status)
        elif body:
            return self._json({"error": "expected multipart/form-data with a 'workbook' file and optional 'week' field",
                               "detail": "unsupported_media_type"}, status=415)

        self._respond(week, workbook)


def serve(port: int, schedule: Path | None, season: int | None,
          bookmakers: str | None = None, season_start: str | None = None) -> None:
    Handler.schedule_path = schedule
    Handler.season = season
    Handler.bookmakers = bookmakers
    Handler.season_start = season_start
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"pick'em service on http://127.0.0.1:{port}", file=sys.stderr)
    print("  GET  /health", file=sys.stderr)
    print("  GET  /sheet?week=3", file=sys.stderr)
    print("  POST /sheet   (multipart: workbook=@file.xlsx, week=3)", file=sys.stderr)
    if not (os.environ.get(ENV_ODDS_KEY) or "").strip():
        print(f"  note: {ENV_ODDS_KEY} is not set, so /sheet will fail until it is", file=sys.stderr)
    if not configured_sheet_url():
        print(f"  note: {ENV_SHEET_URL} is not set, so GET /sheet writes placeholder player columns",
              file=sys.stderr)
    print("  ctrl-c to stop\n", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)


# --- CLI --------------------------------------------------------------------


def report(result: SheetResult, destination: str | None) -> None:
    by_kind: dict[str, list[dict]] = {}
    for w in result.warnings:
        by_kind.setdefault(w["kind"], []).append(w)

    print(f"\n  headers from {result.header_source}", file=sys.stderr)
    lined = sum(1 for r in result.rows if r[4])
    print(f"Week {result.week}: {len(result.rows)} games, {lined} with a line", file=sys.stderr)
    labels = {
        "unmatched": "NOT FOUND in The Odds API's slate",
        "no_line": "no line posted yet",
        "missing_games": "games without odds",
        "pickem_line": "pick'em lines (0)",
        "unknown_team": "unknown team names",
        "whole_number_line": "whole-number lines, check these",
        "header_fallback": "header",
        "ungraded_week": "ungraded games (not in records)",
        "unresolved_pick": "unresolvable picks (not in records)",
    }
    for kind, items in by_kind.items():
        detail = ", ".join(f"{w['game']} ({w['detail']})" if w["game"] else w["detail"] for w in items)
        print(f"  {labels.get(kind, kind)} ({len(items)}): {detail}", file=sys.stderr)
    if destination:
        print(f"  wrote {destination}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--week", type=int,
                   help="NFL week number (default: the current week, from the season opener)")
    p.add_argument("--season", type=int, help="season year (default: the current NFL season)")
    p.add_argument("--schedule", type=Path,
                   help="optional xlsx schedule to take matchups from instead of The Odds API's slate")
    p.add_argument("--bookmakers", help=f"bookmaker keys in preference order (default: ${ENV_BOOKMAKERS}, "
                                        f"else {','.join(DEFAULT_BOOKMAKERS)})")
    p.add_argument("--season-start", metavar="YYYY-MM-DD",
                   help=f"season opening day (default: ${ENV_SEASON_START}, else the Thursday after "
                        "Labor Day)")
    p.add_argument("--workbook", type=Path, help="pool workbook (.xlsx) to take players and records from")
    p.add_argument("--out", help="output path, or - for stdout (default: ./<tab name>.csv)")
    p.add_argument("--dry-run", action="store_true", help="print, don't write")
    p.add_argument("--headers", default="live",
                   help=f"pool source: live (default, ${ENV_SHEET_URL}), a published sheet URL, "
                        "an .xlsx path, or a .csv whose header row is used as-is")
    p.add_argument("--serve", type=int, metavar="PORT",
                   help="run as an HTTP service instead of generating a file")
    p.add_argument("--allow-header-fallback", action="store_true",
                   help="with --workbook: write placeholder player columns if no week tab has a "
                        "pick'em header (by default that is an error, as in the push)")
    args = p.parse_args(argv)

    if args.serve:
        serve(args.serve, args.schedule, args.season, args.bookmakers, args.season_start)
        return 0

    try:
        workbook = args.workbook.read_bytes() if args.workbook else None
    except OSError as exc:
        print(f"error: could not read --workbook ({type(exc).__name__})", file=sys.stderr)
        return 1
    try:
        result = build_sheet(
            week=args.week, season=args.season, schedule=args.schedule,
            workbook=workbook, headers=args.headers, bookmakers=args.bookmakers,
            season_start=args.season_start,
        )
        # With a pool workbook (e.g. the watchdog's export of the pool sheet),
        # write exactly the cells push_pickem_to_sheets.py would, and refuse what
        # it refuses.
        text = (values_csv(sheet_values(result, args.allow_header_fallback)) if workbook is not None
                else result.csv)
    except SheetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = args.out or f"{tab_title(DEFAULT_TAB_PATTERN, result.week, result.season)}.csv"
    if args.dry_run or out_path == "-":
        sys.stdout.write(text)
        report(result, None)
    else:
        Path(out_path).write_text(text)
        report(result, out_path)
    # The new tab's name, from the pool's own naming pattern, for whoever writes it.
    print(f"tab_name: {result.tab_name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
