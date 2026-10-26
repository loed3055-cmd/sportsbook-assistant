#!/usr/bin/env python3
"""Turn a generated week CSV into one Sheets `batchUpdate` that adds the week's tab.

For the connector path (the Wednesday watchdog), where there is no service
account and push_pickem_to_sheets.py can't write. The Google Sheets connector's
value-writing tools parse input like the Sheets UI (dates, formulas), so this
emits typed `updateCells` requests instead. Those store values literally, the
same as the push's valueInputOption=RAW.

It reuses push_pickem_to_sheets's own rules, so both paths write the same thing:
`validate` (14 pool columns, numeric week/spread, formula cells refused), the
tab name from the sheet's naming pattern, the never-overwrite check, and the
duplicate-the-latest-week-tab template choice.

    python3 scripts/make_pickem_sheet.py --workbook pool.xlsx --out week.csv
    # meta.json: Sheets connector get_spreadsheet with fields ["sheets.properties"]
    python3 scripts/pickem_sheet_batch.py --csv week.csv --meta meta.json --out batch.json
    # then send batch.json's "requests" in ONE update_spreadsheet call

The batch is atomic: duplicate (or add) the tab, clear the copied week's values,
write the new week. Output and logs never contain cell values or player names.

Exit codes: 0 ok, 1 invalid CSV (validation), 2 bad input files/arguments,
3 the week's tab already exists. Stdlib only; runs on macOS's python3 (3.9+).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_pickem_sheet as mps  # noqa: E402
import push_pickem_to_sheets as push  # noqa: E402

EXIT_OK, EXIT_INVALID, EXIT_INPUT, EXIT_EXISTS = 0, 1, 2, 3


class TabExists(Exception):
    """A tab for this week is already in the sheet; players may have entered picks."""


def result_from_csv(text: str, season: int) -> SimpleNamespace:
    """The CSV as the minimal SheetResult shape `push.validate` reads."""
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        raise push.PushError("the CSV is empty")
    header, body = rows[0], rows[1:]
    try:
        week = int(float(body[0][mps.FIXED_COLUMNS.index("week")])) if body else 0
    except (ValueError, IndexError):
        raise push.PushError("cell A2 must be a number") from None
    return SimpleNamespace(header=header, rows=body, week=week, season=season, warnings=[])


def tabs_from_meta(meta: dict) -> list[dict]:
    """[{title, sheetId, index}] from a get_spreadsheet reply with sheets.properties."""
    tabs = []
    for position, sheet in enumerate(meta.get("sheets") or []):
        props = sheet.get("properties") or {}
        if "title" not in props:
            raise ValueError("every sheet needs properties.title")
        tabs.append({"title": props["title"], "sheetId": int(props.get("sheetId", 0)),
                     "index": int(props.get("index", position))})
    # JSON may omit a zero sheetId, and only one tab can have id 0. More than one
    # missing means the metadata is incomplete; don't guess which tab is which.
    missing = sum(1 for sheet in meta.get("sheets") or []
                  if "sheetId" not in ((sheet.get("properties")) or {}))
    if missing > 1:
        raise ValueError(f"sheetId missing for {missing} tabs; re-read with fields [\"sheets.properties\"]")
    return tabs


def _cell(value) -> dict:
    if value == "" or value is None:
        return {}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def build_batch(csv_text: str, meta: dict, season: int) -> dict:
    """The requests that add this week's tab, plus a summary with no cell values."""
    result = result_from_csv(csv_text, season)
    if result.header[len(mps.FIXED_COLUMNS):] == mps.placeholder_header()[len(mps.FIXED_COLUMNS):]:
        raise push.PushError("header_fallback: the CSV has placeholder player columns, not the "
                             "pool's players; rebuild it from a workbook with a pick'em tab")
    values = push.validate(result, allow_header_fallback=False)
    tabs = tabs_from_meta(meta)
    titles = [t["title"] for t in tabs]
    title = push.tab_name(result.week, season, mps.detect_tab_pattern(titles))

    existing = push.conflicting_tab(tabs, title, result.week, season)
    if existing is not None:
        raise TabExists(f"tab {existing['title']!r} already exists for week {result.week}; "
                        "not overwriting it because players may have entered picks")

    new_id = max([t["sheetId"] for t in tabs], default=0) + 1
    template = push.template_tab(tabs, result.week, season)
    requests: list[dict] = []
    if template is not None:
        requests.append({"duplicateSheet": {
            "sourceSheetId": template["sheetId"],
            "insertSheetIndex": template["index"] + 1,
            "newSheetId": new_id,
            "newSheetName": title,
        }})
        # Drop the copied week's games and picks; formatting and validation stay.
        requests.append({"updateCells": {
            "range": {"sheetId": new_id, "startRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": push.N_COLUMNS},
            "fields": "userEnteredValue",
        }})
    else:
        requests.append({"addSheet": {"properties": {
            "sheetId": new_id, "title": title, "gridProperties": {"frozenRowCount": 1},
        }}})
    requests.append({"updateCells": {
        "start": {"sheetId": new_id, "rowIndex": 0, "columnIndex": 0},
        "rows": [{"values": [_cell(v) for v in row]} for row in values],
        "fields": "userEnteredValue",
    }})
    return {
        "tab": title,
        "newSheetId": new_id,
        "template": template["title"] if template else None,
        "week": result.week,
        "season": season,
        "rows": len(values) - 1,
        "requests": requests,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--csv", type=Path, required=True, help="week CSV from make_pickem_sheet.py")
    p.add_argument("--meta", type=Path, required=True,
                   help="get_spreadsheet reply with fields [\"sheets.properties\"], as JSON")
    p.add_argument("--season", type=int, help="season year (default: the current NFL season)")
    p.add_argument("--out", help="write the plan JSON here (default: stdout)")
    args = p.parse_args(argv)
    season = args.season or mps.current_season(mps.now_et())
    if args.out and mps.inside_repo(args.out):
        print("error: refusing to write the batch (it holds pool data) inside the repo; "
              "write it to a scratch directory", file=sys.stderr)
        return EXIT_INPUT

    try:
        csv_text = args.csv.read_text(encoding="utf-8-sig")
        meta = json.loads(args.meta.read_text(encoding="utf-8"))
        plan = build_batch(csv_text, meta, season)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: could not read --csv/--meta ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_INPUT
    except TabExists as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_EXISTS
    except push.PushError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID

    text = json.dumps(plan, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    how = f"duplicating {plan['template']}" if plan["template"] else "as a bare tab"
    print(f"batch ready: create {plan['tab']} {how}, {plan['rows']} games, "
          f"{len(plan['requests'])} requests in one update_spreadsheet call", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
