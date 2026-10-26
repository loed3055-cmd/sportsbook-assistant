#!/usr/bin/env python3
"""Push one week's pick'em sheet into a pool's Google Sheet as a new tab.

Everything pool-specific comes from the spreadsheet itself, read through the
Sheets API v4: player names from the latest week tab's header, each player's
"(W-L)" computed from this season's earlier week tabs, and the new tab's name
from the existing tabs' naming pattern (default `nfl_week_{week}_{season}_pickem`).
Matchups, kickoffs and spreads come from The Odds API via make_pickem_sheet.build_sheet
(ODDS_API_KEY; 1 credit per run).

With no --week, the week comes from the season opener. In the off-season (after
week 18, or more than 6 days before week 1) it prints "off-season: nothing to
create" and exits 0 without touching the sheet or The Odds API.

The new tab is a duplicate of the most recent earlier week tab, so the pool's
table, column widths, frozen header and numeric validation carry over. Its old
values are then cleared and the new week is written with valueInputOption=RAW
(week and spread sent as JSON numbers). With no earlier week tab, it falls back
to a bare addSheet.

An existing tab for the week is never touched unless --force is passed:
players may already have entered picks there.

Credentials are Google Application Default Credentials (google.auth.default):
Workload Identity Federation in GitHub Actions, GOOGLE_APPLICATION_CREDENTIALS
pointing at a key file, or `gcloud auth application-default login
--impersonate-service-account=...` locally.

    ODDS_API_KEY=... PICKEM_SPREADSHEET_ID=1AbC... \\
        python scripts/push_pickem_to_sheets.py [--week N] [--dry-run]

Logs never contain player names, cell contents or the spreadsheet ID, unless
--show-values is passed (for local dry runs only).

Needs requirements/pickem.txt (google-auth, requests), except for
`--dry-run --workbook pool.xlsx`. make_pickem_sheet.py stays stdlib-only; this
module is the only place that imports a Google client.

Exit codes: 0 ok (including off-season no-op), 1 generation/validation/API
failure, 2 configuration error (incl. missing or rejected ODDS_API_KEY),
3 the week's tab already exists (rerun with --force to overwrite).
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_pickem_sheet as mps  # noqa: E402

ENV_SHEET = "PICKEM_SPREADSHEET_ID"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API = "https://sheets.googleapis.com/v4/spreadsheets"

FIXED_COLUMNS = mps.FIXED_COLUMNS
N_COLUMNS = 14
LAST_COL = "N"
WEEK_COL, SPREAD_COL = 0, 4

# A string cell starting with one of these could be evaluated as a formula by
# Sheets or by a spreadsheet the data is later exported to. RAW input already
# stores strings literally; this is defence in depth.
FORMULA_PREFIXES = ("=", "+", "-", "@", "＝", "＋", "－", "＠")
CONTROL_PREFIXES = ("\t", "\r", "\n")

EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_EXISTS = 0, 1, 2, 3


class PushError(Exception):
    """Clean, user-facing failure. Messages never carry names, cell values or the sheet ID."""

    def __init__(self, message: str, code: int = EXIT_FAIL):
        super().__init__(message)
        self.code = code


def tab_name(week: int, season: int, pattern: str = mps.DEFAULT_TAB_PATTERN) -> str:
    return mps.tab_title(pattern, week, season)


# --- configuration --------------------------------------------------------------


def load_config(env: Mapping[str, str]) -> str:
    """The spreadsheet ID (or edit URL) from env, validated. Never echoed back."""
    raw = (env.get(ENV_SHEET) or "").strip()
    if not raw:
        raise PushError(
            f"missing environment variable {ENV_SHEET}: the ID from the spreadsheet's edit URL "
            "(/spreadsheets/d/<ID>/edit). For a dry run without Google access, pass --workbook "
            "with a downloaded copy of the pool sheet instead.",
            EXIT_CONFIG,
        )
    if "/spreadsheets/d/e/" in raw or raw.startswith("2PACX-"):
        raise PushError(
            f"{ENV_SHEET} looks like a publish-to-web ID (2PACX-...). The API needs the "
            "spreadsheet's own ID from its edit URL.",
            EXIT_CONFIG,
        )
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
    sheet_id = m.group(1) if m else raw
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sheet_id):
        raise PushError(f"{ENV_SHEET} is not a valid spreadsheet ID or edit URL", EXIT_CONFIG)
    return sheet_id


def make_session():
    """AuthorizedSession from Application Default Credentials. Imported lazily."""
    try:
        import google.auth
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        raise PushError(
            "google-auth is not installed: pip install -r requirements/pickem.txt", EXIT_CONFIG
        ) from None
    try:
        creds, _project = google.auth.default(scopes=SCOPES)
    except GoogleAuthError as exc:
        # Type only: messages can quote file paths or account details.
        raise PushError(
            f"could not load Google application default credentials ({type(exc).__name__}). In "
            "GitHub Actions use google-github-actions/auth; locally run `gcloud auth "
            "application-default login --impersonate-service-account=...` or set "
            "GOOGLE_APPLICATION_CREDENTIALS.",
            EXIT_CONFIG,
        ) from None
    return AuthorizedSession(creds)


# --- Sheets API ---------------------------------------------------------------


class SheetsClient:
    """The Sheets v4 calls this job needs, over any requests-like session."""

    def __init__(self, session, spreadsheet_id: str, sleep: Callable[[float], None] = time.sleep,
                 show_values: bool = False):
        self.session = session
        self.sheet_id = spreadsheet_id
        self.base = f"{API}/{spreadsheet_id}"
        self.sleep = sleep
        self.show_values = show_values

    def _scrub(self, text: str) -> str:
        return (text or "").replace(self.sheet_id, "<spreadsheet-id>")

    def _call(self, method: str, url: str, body: dict | None, what: str, attempts: int = 3) -> dict:
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.request(method, url, json=body, timeout=30)
            except Exception as exc:  # noqa: BLE001 - requests/google-auth/transport errors
                # Type only, and no chained traceback: both would carry the
                # /spreadsheets/<ID>/ URL into the (public) Actions log.
                raise PushError(f"Sheets API {what} failed ({type(exc).__name__})") from None
            status = resp.status_code
            # 429 means the request was not processed, so any call may retry.
            # 5xx is only retried for reads: a structural write might have landed.
            retryable = status == 429 or (status >= 500 and method == "GET")
            if 200 <= status < 300:
                try:
                    return resp.json() or {}
                except ValueError:
                    return {}
            if retryable and attempt < attempts:
                self.sleep(2 ** attempt)
                continue
            raise PushError(f"Sheets API {what} failed: {self._describe_error(resp)}")
        raise AssertionError("unreachable")

    def _describe_error(self, resp) -> str:
        """HTTP code plus Google's `error.status`. The free-text `message` can quote
        tab or cell contents, so it is added (ID-scrubbed) only with --show-values."""
        status = resp.status_code
        try:
            error = resp.json().get("error", {})
            code = str(error.get("status", "") or "")
            message = str(error.get("message", "") or "")
        except (ValueError, AttributeError, TypeError):
            code, message = "", ""
        if not re.fullmatch(r"[A-Z_]{1,64}", code):
            code = ""  # only a well-formed status enum is safe to print
        out = f"HTTP {status}" + (f" {code}" if code else "")
        if self.show_values and message:
            out += f": {self._scrub(message)}"
        elif not self.show_values:
            out += " (re-run locally with --show-values for Google's message)"
        return out

    def tabs(self) -> list[dict]:
        data = self._call("GET", f"{self.base}?fields=sheets.properties(sheetId,title,index)",
                          None, "list tabs")
        return [s["properties"] for s in data.get("sheets", [])]

    def read_tabs(self, titles: list[str]) -> dict[str, list[list[str]]]:
        """A1:N of each tab, as displayed, in one batched read."""
        if not titles:
            return {}
        ranges = "&".join(f"ranges={quote(a1(t, f'A1:{LAST_COL}'), safe='')}" for t in titles)
        data = self._call("GET", f"{self.base}/values:batchGet?majorDimension=ROWS&{ranges}",
                          None, f"read {len(titles)} week tab(s)")
        out = {}
        for title, vr in zip(titles, data.get("valueRanges", [])):
            out[title] = [[str(c) for c in row] for row in vr.get("values", [])]
        return out

    def batch_update(self, requests: list[dict], what: str) -> list[dict]:
        return self._call("POST", f"{self.base}:batchUpdate", {"requests": requests}, what).get("replies", [])

    def clear(self, a1_range: str) -> None:
        self._call("POST", f"{self.base}/values:batchClear", {"ranges": [a1_range]}, f"clear {a1_range}")

    def write(self, a1_range: str, values: list[list]) -> None:
        # RAW stores strings literally (no formula evaluation). week and spread
        # are sent as JSON numbers so the pool tabs' number validation on
        # columns A and E still passes.
        url = f"{self.base}/values/{quote(a1_range, safe='')}?valueInputOption=RAW"
        self._call("PUT", url, {"range": a1_range, "majorDimension": "ROWS", "values": values},
                   f"write {a1_range}")


def a1(title: str, cells: str) -> str:
    return "'" + title.replace("'", "''") + "'!" + cells


def pool_titles(tabs: list[dict], season: int) -> list[str]:
    """Week tabs worth reading: all of `season`'s, plus the latest earlier one (week 1 headers)."""
    keyed = [(k, title) for title, k in mps.week_tab_keys([t["title"] for t in tabs], season).items()]
    this_season = [title for k, title in keyed if k[0] == season]
    earlier = sorted((k, title) for k, title in keyed if k[0] < season)
    return this_season + ([earlier[-1][1]] if earlier else [])


def conflicting_tab(tabs: list[dict], title: str, week: int, season: int) -> dict | None:
    """A tab that already holds this week: exact name, or any week-`week` tab of `season`."""
    keys = mps.week_tab_keys([t["title"] for t in tabs], season)
    for t in tabs:
        if t["title"].lower() == title.lower() or keys.get(t["title"]) == (season, week):
            return t
    return None


def template_tab(tabs: list[dict], week: int, season: int) -> dict | None:
    """Most recent week tab before this week (an earlier season counts too)."""
    best, best_key = None, None
    keys = mps.week_tab_keys([t["title"] for t in tabs], season)
    for t in tabs:
        key = keys.get(t["title"])
        if key and key < (season, week) and (best_key is None or key > best_key):
            best, best_key = t, key
    return best


# --- validation / output ------------------------------------------------------


def _number(text: str, cell: str, integer: bool):
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise PushError(f"cell {cell} must be a number") from None
    if integer:
        if not value.is_integer():
            raise PushError(f"cell {cell} must be a whole number")
        return int(value)
    return int(value) if value.is_integer() else value


def validate(result: mps.SheetResult, allow_header_fallback: bool) -> list[list]:
    """The rows to write, typed for RAW input. Errors cite positions, never values."""
    header = result.header
    if len(header) != N_COLUMNS:
        raise PushError(f"header must have the pool's {N_COLUMNS} columns; got {len(header)}")
    wrong = [mps.col_letter(i) for i, (got, want) in enumerate(zip(header, FIXED_COLUMNS)) if got != want]
    if wrong:
        raise PushError(f"header columns {', '.join(wrong)} are not the pool's fixed columns "
                        f"({', '.join(FIXED_COLUMNS)})")
    blank = [mps.col_letter(i) for i in range(8, N_COLUMNS) if not str(header[i]).strip()]
    if blank:
        raise PushError(f"player header cell(s) {', '.join(c + '1' for c in blank)} are blank")
    if not result.rows:
        raise PushError(f"week {result.week} has no games")

    typed: list[list] = [list(header)]
    for r, row in enumerate(result.rows, start=2):
        if len(row) != N_COLUMNS:
            raise PushError(f"row {r} has {len(row)} columns, expected {N_COLUMNS}")
        out = list(row)
        out[WEEK_COL] = _number(row[WEEK_COL], f"A{r}", integer=True)
        if out[WEEK_COL] != result.week:
            raise PushError(f"row {r} is not for week {result.week}")
        spread = str(row[SPREAD_COL]).strip()
        out[SPREAD_COL] = _number(spread, f"E{r}", integer=False) if spread else ""
        typed.append(out)

    for r, row in enumerate(typed, start=1):
        for c, cell in enumerate(row):
            if isinstance(cell, str) and (cell.startswith(CONTROL_PREFIXES)
                                          or cell.lstrip().startswith(FORMULA_PREFIXES)):
                raise PushError(f"cell {mps.col_letter(c)}{r} starts with a formula or control "
                                "character; refusing to write it")

    if not allow_header_fallback and any(w["kind"] == "header_fallback" for w in result.warnings):
        raise PushError(
            "header_fallback: player names could not be read from the pool sheet, so placeholder "
            "columns would be written. Fix the sheet, or pass --allow-header-fallback."
        )
    return typed


def print_warnings(result: mps.SheetResult, env: Mapping[str, str], show_values: bool) -> None:
    annotate = env.get("GITHUB_ACTIONS") == "true"
    lined = sum(1 for r in result.rows if r[4])
    print(f"week {result.week} {result.season}: {len(result.rows)} games, {lined} with a line; "
          f"headers from {result.header_source}")
    for w in result.warnings:
        where = f" {w['game']}" if w.get("game") else ""
        detail = w["detail"] if show_values else w.get("public_detail", w["detail"])
        line = f"[{w['kind']}]{where}: {detail}"
        print(f"warning {line}")
        if annotate:
            print(f"::warning title=pickem {w['kind']}::{line}")


def to_csv(values: list[list]) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerows(values)
    return buf.getvalue()


# --- push ---------------------------------------------------------------------


def push(client: SheetsClient, values: list[list], title: str, week: int, season: int, force: bool) -> str:
    data_range = a1(title, f"A2:{LAST_COL}")
    full_range = a1(title, f"A1:{LAST_COL}{len(values)}")
    tabs = client.tabs()  # re-read right before writing: the existence check must be fresh

    existing = conflicting_tab(tabs, title, week, season)
    if existing is not None:
        if not force:
            raise PushError(
                f"tab {existing['title']!r} already exists for week {week}; not overwriting it because "
                "players may have entered picks. Pass --force to clear and rewrite it.",
                EXIT_EXISTS,
            )
        if existing["title"] != title:
            raise PushError(f"--force only rewrites a tab named exactly {title!r}; found "
                            f"{existing['title']!r}. Rename or remove it by hand.")
        client.clear(data_range)
        client.write(full_range, values)
        return f"overwrote {title} ({len(values) - 1} games) with --force"

    template = template_tab(tabs, week, season)
    if template is not None:
        replies = client.batch_update([{"duplicateSheet": {
            "sourceSheetId": template["sheetId"],
            "insertSheetIndex": template["index"] + 1,
            "newSheetName": title,
        }}], f"duplicate {template['title']}")
        new_id = replies[0]["duplicateSheet"]["properties"]["sheetId"]
        how = f"from template {template['title']}"
    else:
        replies = client.batch_update([{"addSheet": {"properties": {
            "title": title,
            "gridProperties": {"frozenRowCount": 1},
        }}}], f"add {title}")
        new_id = replies[0]["addSheet"]["properties"]["sheetId"]
        how = "as a bare tab (no earlier week tab to copy)"

    try:
        if template is not None:
            client.clear(data_range)  # drop the template week's games and picks
        client.write(full_range, values)
    except PushError as exc:
        # Don't leave a half-built tab that would block the next run.
        try:
            client.batch_update([{"deleteSheet": {"sheetId": new_id}}], f"roll back {title}")
            note = f"; removed the partial tab {title}"
        except PushError as rollback_exc:
            note = f"; ALSO failed to remove the partial tab {title} ({rollback_exc}), delete it by hand"
        raise PushError(f"{exc}{note}") from None
    return f"created {title} {how} ({len(values) - 1} games)"


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None,
         session_factory: Callable[[], object] | None = None) -> int:
    env = os.environ if env is None else env
    session_factory = session_factory or make_session

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--week", type=int, help="NFL week (default: the current week from the season opener; "
                                            "a Wednesday run after MNF gets the new week; nothing in the "
                                            "off-season)")
    p.add_argument("--season", type=int, help="season year (default: the current NFL season)")
    p.add_argument("--schedule", type=Path,
                   help="optional xlsx schedule to take matchups from instead of The Odds API's slate")
    p.add_argument("--bookmakers", help=f"bookmaker keys in preference order (default: "
                                        f"${mps.ENV_BOOKMAKERS}, else {','.join(mps.DEFAULT_BOOKMAKERS)})")
    p.add_argument("--season-start", metavar="YYYY-MM-DD",
                   help=f"season opening day (default: ${mps.ENV_SEASON_START}, else the Thursday "
                        "after Labor Day)")
    p.add_argument("--dry-run", action="store_true",
                   help="build and describe what would be written; only read-only API calls")
    p.add_argument("--workbook", type=Path,
                   help="with --dry-run: read the pool from a downloaded .xlsx instead of the API")
    p.add_argument("--show-values", action="store_true",
                   help="print player names, warning details and the rows (local use; never in CI)")
    p.add_argument("--force", action="store_true",
                   help="clear and rewrite the week's tab if it already exists (destroys entered picks)")
    p.add_argument("--allow-header-fallback", action="store_true",
                   help="write placeholder player columns if names can't be read from the sheet")
    args = p.parse_args(argv)

    try:
        if args.workbook and not args.dry_run:
            raise PushError("--workbook is only for --dry-run; real runs read the sheet through the API",
                            EXIT_CONFIG)
        now = mps.now_et()
        season = args.season or mps.current_season(now)

        # Validate everything configurable before any network call.
        sheet_id = None if args.workbook else load_config(env)
        try:
            api_key = mps.odds_api_key(env=env)
            bookmakers = mps.bookmaker_preference(args.bookmakers, env=env)
            opener = mps.season_opener(season, args.season_start, env=env)
        except mps.ConfigError as exc:
            raise PushError(str(exc), EXIT_CONFIG) from None

        week = args.week
        if week is None:
            week = mps.season_week(opener, now)
            if week is None:
                print(f"off-season: nothing to create (season {season}, opening day "
                      f"{opener.isoformat()}; pass --week to force one)")
                return EXIT_OK
            if args.schedule:
                week = None  # the schedule file decides, as before

        client, tabs, pool, workbook, titles = None, None, None, None, []
        if args.workbook:
            try:
                workbook = args.workbook.read_bytes()
            except OSError as exc:
                raise PushError(f"could not read --workbook ({type(exc).__name__})", EXIT_CONFIG) from None
            try:
                titles = list(mps.load_sheets(workbook))
            except mps.SheetError:
                titles = []
        else:
            client = SheetsClient(session_factory(), sheet_id, show_values=args.show_values)
            tabs = client.tabs()
            titles = [t["title"] for t in tabs]
            pool = client.read_tabs(pool_titles(tabs, season))

        try:
            result = mps.build_sheet(week=week, season=season, schedule=args.schedule,
                                     workbook=workbook, pool=pool, bookmakers=bookmakers,
                                     season_start=opener, api_key=api_key)
        except mps.ConfigError as exc:
            raise PushError(f"could not build the sheet: {mps.redact(exc, api_key)}", EXIT_CONFIG) from None
        except mps.SheetError as exc:
            raise PushError(f"could not build the sheet: {mps.redact(exc, api_key)}") from None

        print_warnings(result, env, args.show_values)
        values = validate(result, args.allow_header_fallback)
        title = tab_name(result.week, result.season, mps.detect_tab_pattern(titles))

        if args.dry_run:
            exists = conflicting_tab(tabs, title, result.week, result.season) if tabs is not None else None
            state = ("the tab ALREADY EXISTS, so a real run would exit 3 (or rewrite it with --force)"
                     if exists else "no tab for this week exists yet" if tabs is not None
                     else "tab existence not checked (workbook mode)")
            print(f"DRY RUN: would create tab {title} (duplicating the latest earlier week tab if one "
                  f"exists), clear A2:{LAST_COL}, and write {len(values)} rows x {N_COLUMNS} columns to "
                  f"A1:{LAST_COL}{len(values)} with valueInputOption=RAW; {state}.")
            if args.show_values:
                sys.stdout.write(to_csv(values))
            else:
                print("(player names and rows hidden; pass --show-values to print them)")
            return EXIT_OK

        print(push(client, values, title, result.week, result.season, args.force))
        return EXIT_OK
    except PushError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - last resort: Actions logs are public
        # No traceback and no str(exc): either could carry names, cells or the ID.
        print(f"error: unexpected {type(exc).__name__}; re-run locally with --show-values for details",
              file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
