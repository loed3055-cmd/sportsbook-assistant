---
name: working-with-pool-sheet
description: Use when the pick'em pool Google Sheet needs downloading, checking, or a new week tab, especially when the Wednesday tab is missing, the cron failed, or there is no Google service account. Also for general download/upload/edit of Google Sheets and Drive files through the claude.ai connectors.
---

# Working with the pool sheet

The pool lives in one Google Sheet: a tab per week, 14 columns (`week … ats_winner` plus six
players). Two ways to add a week tab, and both write the same thing:

| Path | When | How |
|---|---|---|
| **Script** | Google credentials (ADC) exist: the GitHub cron, or a Mac with `gcloud auth application-default login` | `push_pickem_to_sheets.py` (`docs/pickem-runbook.md`) |
| **Connector** | No service account: watchdog, catch-up, this chat | The steps below |

**REQUIRED BACKGROUND:** `anthropic-skills:google-workspace` (connector rules; read
`references/sheets.md` before writing).

## Connectors

You need **Google Drive** (find, export) and **Google Sheets** (read tabs, write). Load them
before you start. If one is missing, the user connects it through `/mcp` (claude.ai Google Drive /
Google Sheets). A missing connector is a stop-and-ask, never a reason to create a new file.

The spreadsheet: use `$PICKEM_SPREADSHEET_ID` if set, otherwise Drive search
(`title contains 'Pickem'`, `mimeType = 'application/vnd.google-apps.spreadsheet'`), and confirm
the match with the user if more than one fits.

## Add the missing week tab (connector path)

Work in the scratchpad: the export holds players' names and picks.

1. **Is it missing?** Run Sheets `get_spreadsheet` with `fields: ["sheets.properties"]`, then
   `python3 scripts/push_pickem_to_sheets.py --check-tab --workbook <export>`, or compare the
   titles yourself. If the tab exists, stop: it is never overwritten.
2. **Download:** Drive `download_file_content` with `exportMimeType`
   `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, saved to the scratchpad as `pool.xlsx`.
3. **Build:** `python3 scripts/make_pickem_sheet.py --workbook pool.xlsx --out week.csv`. This uses
   the Odds API key from env or the Keychain (see `storing-odds-api-key`) and costs 1 credit.
   Stop on a `header_fallback` warning or 0 games.
4. **Fresh metadata, then batch:** `get_spreadsheet` again right now, because the cron may have just run.
   Save it to `meta.json`, then run
   `python3 scripts/pickem_sheet_batch.py --csv week.csv --meta meta.json --out batch.json`.
   Exit 3 means the tab exists, so stop.
5. **Write once:** send `batch.json`'s `requests` in a single `update_spreadsheet` call. Never
   `update_values`/`update_formulas`: they parse kickoff times into dates and skip the
   formula guard.
6. **Verify:** check the new tab's title and position with `get_spreadsheet`, then `get_values` the
   header and row count against `week.csv`. Confirm the previous week's tab is unchanged.
7. **Clean up:** delete `pool.xlsx`, `week.csv`, `meta.json`, and `batch.json`.

Report the tab name, the game count, and which path you used. Never put player names, picks,
or the spreadsheet ID in a public log or commit message.

## General Drive/Sheets work

- Download: Drive `download_file_content` (export).
- Read: `get_values` (displayed values) or the grid read in `references/sheets.md`.
- Upload: Drive `create_file`, which converts CSV/.xlsx. Keep uploads small.
- Edit in place: Sheets tools. The google-workspace rules apply: same file, same link, verify
  after every write.

## Common mistakes

| Mistake | Fix |
|---|---|
| Wrote rows with `update_values` | Delete the tab, and redo step 4 with the batch |
| Ran the batch on stale metadata, and the cron had already made the tab | Always re-read in step 4; exit 3 protects you only if the metadata is fresh |
| Left `pool.xlsx` in the repo or `examples/` | Use the scratchpad; delete it after |
| `--force` to "fix" an existing tab | Never on the connector path: players may have picked |
