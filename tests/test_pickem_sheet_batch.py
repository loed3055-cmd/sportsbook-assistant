"""scripts/pickem_sheet_batch.py: generated week CSV + sheet metadata -> one Sheets batchUpdate.

The batch is what the Google Sheets connector sends when no service account is
available (the watchdog path). It must write exactly what push_pickem_to_sheets
writes with valueInputOption=RAW: same validation, same typing, same tab choice.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import make_pickem_sheet as mps  # noqa: E402
import pickem_sheet_batch as batch  # noqa: E402
import push_pickem_to_sheets as push  # noqa: E402

PLAYERS = ["ann (40-30)", "bo (38-32)", "cy (35-35)", "di (41-29)", "ed (30-40)", "fy (36-34)"]
HEADER = list(mps.FIXED_COLUMNS) + PLAYERS
ROWS = [
    ["8", "2026-10-29 20:15", "MIA@BUF", "BUF", "-6.5", "draftkings", "2026-10-28 09:00", "", "", "", "", "", "", ""],
    ["8", "2026-11-01 13:00", "GB@DET", "DET", "-3", "fanduel", "2026-10-28 09:00", "", "", "", "", "", "", ""],
    ["8", "2026-11-01 16:25", "NE@KC", "", "", "", "", "", "", "", "", "", "", ""],  # no line yet
]


def _csv(header=HEADER, rows=ROWS) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([header, *rows])
    return buf.getvalue()


def _meta(*titles_ids):
    return {"sheets": [{"properties": {"title": t, "sheetId": sid, "index": i}}
                       for i, (t, sid) in enumerate(titles_ids)]}


META = _meta(("rules", 0), ("nfl_week_5_2026_pickem", 101), ("nfl_week_6_2026_pickem", 202),
             ("standings", 303))


def _build(csv_text=None, meta=None, season=2026):
    return batch.build_batch(csv_text if csv_text is not None else _csv(), META if meta is None else meta,
                             season)


def _written_rows(plan):
    (write,) = [r["updateCells"] for r in plan["requests"] if "updateCells" in r and "rows" in r["updateCells"]]
    out = []
    for row in write["rows"]:
        cells = []
        for cell in row["values"]:
            v = cell.get("userEnteredValue", {})
            cells.append(v.get("numberValue", v.get("stringValue", "")))
        out.append(cells)
    return out


def test_duplicates_latest_earlier_week_and_names_tab_by_pattern():
    plan = _build()
    dup = plan["requests"][0]["duplicateSheet"]
    assert dup["sourceSheetId"] == 202  # week 6 is the latest earlier week
    assert dup["insertSheetIndex"] == 3  # right after week 6
    assert dup["newSheetName"] == "nfl_week_8_2026_pickem" == plan["tab"]
    assert dup["newSheetId"] == plan["newSheetId"]
    assert plan["newSheetId"] not in {0, 101, 202, 303}


def test_follows_the_sheets_own_naming_pattern():
    plan = _build(meta=_meta(("Week 6", 7), ("Week 7", 8)))
    assert plan["tab"] == "Week 8"


def test_clears_template_values_then_writes_typed_cells_in_one_batch():
    plan = _build()
    kinds = [next(iter(r)) for r in plan["requests"]]
    assert kinds == ["duplicateSheet", "updateCells", "updateCells"]
    clear = plan["requests"][1]["updateCells"]
    assert clear["range"] == {"sheetId": plan["newSheetId"], "startRowIndex": 1,
                              "startColumnIndex": 0, "endColumnIndex": 14}
    assert clear["fields"] == "userEnteredValue" and "rows" not in clear
    write = plan["requests"][2]["updateCells"]
    assert write["start"] == {"sheetId": plan["newSheetId"], "rowIndex": 0, "columnIndex": 0}
    assert write["fields"] == "userEnteredValue"


def test_cells_are_typed_exactly_like_the_raw_push():
    plan = _build()
    expected = push.validate(batch.result_from_csv(_csv(), 2026), allow_header_fallback=False)
    assert _written_rows(plan) == expected
    write = plan["requests"][2]["updateCells"]
    first = write["rows"][1]["values"]
    assert first[0] == {"userEnteredValue": {"numberValue": 8}}
    assert first[4] == {"userEnteredValue": {"numberValue": -6.5}}
    # Text that the Sheets UI would parse as a date stays literal text.
    assert first[1] == {"userEnteredValue": {"stringValue": "2026-10-29 20:15"}}
    assert first[7] == {}  # empty cell: cleared, no value
    assert plan["rows"] == 3


def test_existing_week_tab_is_refused():
    meta = _meta(("nfl_week_7_2026_pickem", 1), ("Week 8 2026", 2))
    with pytest.raises(batch.TabExists, match="already exists"):
        _build(meta=meta)


def test_bare_tab_when_no_earlier_week_tab():
    plan = _build(meta=_meta(("rules", 0)))
    add = plan["requests"][0]["addSheet"]["properties"]
    assert add["title"] == "nfl_week_8_2026_pickem"
    assert add["sheetId"] == plan["newSheetId"]
    assert add["gridProperties"] == {"frozenRowCount": 1}
    assert [next(iter(r)) for r in plan["requests"]] == ["addSheet", "updateCells"]


def test_formula_cells_are_refused():
    rows = [list(ROWS[0])]
    rows[0][5] = "=IMPORTXML(\"http://x\")"
    with pytest.raises(push.PushError, match="F2 starts with a formula"):
        _build(_csv(rows=rows))


@pytest.mark.parametrize("bad_header", [HEADER[:13], ["wk"] + HEADER[1:]])
def test_header_must_match_the_pool(bad_header):
    rows = [r[: len(bad_header)] for r in ROWS]
    with pytest.raises(push.PushError):
        _build(_csv(header=bad_header, rows=rows))


def test_mixed_weeks_refused():
    rows = [list(ROWS[0]), list(ROWS[1])]
    rows[1][0] = "9"
    with pytest.raises(push.PushError, match="not for week 8"):
        _build(_csv(rows=rows))


def test_cli_writes_json_and_never_prints_values(tmp_path, capsys):
    csv_path = tmp_path / "week.csv"
    meta_path = tmp_path / "meta.json"
    out_path = tmp_path / "batch.json"
    csv_path.write_text(_csv())
    meta_path.write_text(json.dumps(META))
    code = batch.main(["--csv", str(csv_path), "--meta", str(meta_path), "--season", "2026",
                       "--out", str(out_path)])
    assert code == 0
    plan = json.loads(out_path.read_text())
    assert plan["tab"] == "nfl_week_8_2026_pickem"
    printed = capsys.readouterr()
    for secretish in ("ann", "MIA@BUF", "draftkings"):
        assert secretish not in printed.out + printed.err
    assert "nfl_week_8_2026_pickem" in printed.err


def test_cli_exit_codes(tmp_path, capsys):
    csv_path = tmp_path / "week.csv"
    meta_path = tmp_path / "meta.json"
    csv_path.write_text(_csv())
    meta_path.write_text(json.dumps(_meta(("nfl_week_8_2026_pickem", 5))))
    assert batch.main(["--csv", str(csv_path), "--meta", str(meta_path), "--season", "2026"]) == 3
    meta_path.write_text("not json")
    assert batch.main(["--csv", str(csv_path), "--meta", str(meta_path), "--season", "2026"]) == 2
    rows = [list(ROWS[0])]
    rows[0][2] = "@MIA"
    csv_path.write_text(_csv(rows=rows))
    meta_path.write_text(json.dumps(META))
    assert batch.main(["--csv", str(csv_path), "--meta", str(meta_path), "--season", "2026"]) == 1


def test_meta_accepts_get_spreadsheet_shape_without_index():
    # Some connector replies omit `index` for the first tab (proto default 0).
    meta = {"sheets": [{"properties": {"title": "nfl_week_6_2026_pickem", "sheetId": 9}}]}
    plan = _build(meta=meta)
    assert plan["requests"][0]["duplicateSheet"]["insertSheetIndex"] == 1


def test_helper_is_stdlib_only():
    import ast

    tree = ast.parse((SCRIPTS / "pickem_sheet_batch.py").read_text())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"google", "requests", "pandas", "app"}


def test_cli_refuses_to_write_the_batch_inside_the_repo(tmp_path, capsys):
    csv_path = tmp_path / "week.csv"
    meta_path = tmp_path / "meta.json"
    csv_path.write_text(_csv())
    meta_path.write_text(json.dumps(META))
    out = SCRIPTS.parent / "docs" / "leak-batch.json"  # tracked by git, unlike batch.json
    assert batch.main(["--csv", str(csv_path), "--meta", str(meta_path), "--season", "2026",
                       "--out", str(out)]) == 2
    assert "inside the repo" in capsys.readouterr().err and not out.exists()


def test_placeholder_header_is_refused():
    header = list(mps.FIXED_COLUMNS) + [f"player_{i}" for i in range(1, 7)]
    with pytest.raises(push.PushError, match="header_fallback"):
        _build(_csv(header=header))


def test_meta_without_a_sheet_id_is_an_input_error():
    meta = {"sheets": [{"properties": {"title": "nfl_week_6_2026_pickem", "index": 0}},
                       {"properties": {"title": "rules"}}]}
    with pytest.raises(ValueError, match="sheetId"):
        _build(meta=meta)
