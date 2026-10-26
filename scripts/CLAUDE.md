# Pick'em tooling

`make_pickem_sheet.py` is stdlib-only and runs with the macOS system
`python3` (3.9+). It does not import `app` and needs no DB. It holds all the
shared logic: the slate and spreads from The Odds API v4 (behind
`fetch_week_slate(season, week) -> list[Game]`, urllib only), week resolution
from the season opener, pick resolution, record computation, and tab-name
pattern detection. ESPN is not used: its terms prohibit scripted access.

Env vars: `ODDS_API_KEY` (required; on macOS, if unset, `odds_api_key()` falls back to
the Keychain item `sportsbook-odds-api` via `/usr/bin/security`. Tests disable that with an
autouse fixture so they never read a real Keychain). `setup-odds-key.sh` stores the key in both the Keychain and the
GitHub `pickem` secret; the user must run it in their own terminal (hidden prompt). Never ask
for the key in chat, `PICKEM_SPREADSHEET_ID` (push only),
optional `PICKEM_BOOKMAKERS` (default `draftkings,fanduel,betmgm`) and
`PICKEM_SEASON_START` (YYYY-MM-DD; default: Thursday after Labor Day). Cost:
**1 credit per run** (`/odds`, markets=spreads × regions=us; `/events` is
free), against 500 a month on the free tier. The key is in the query string:
never log a request URL, and pass every error or log line through `redact()`.
With no `--week`, the push exits 0 with "off-season: nothing to create" after
week 18, or more than 6 days before week 1.

Output CSVs must keep the pool sheet's 14 columns: `week, kickoff_et, matchup,
favorite, spread, spread_source, spread_as_of_et, ats_winner`, then six player
columns. Names come from the pool sheet's latest week tab, and records are computed
from this season's earlier tabs. Nothing pool-specific is hard-coded: no sheet
URLs or IDs, no player names, and no default paths into `examples/` (gitignored,
local-only).

`docs/pickem-sheet-app-design.md` records hard-won parsing findings (Excel
omits empty cells, `/pub` vs `/export` URLs, messy pick cells, missed picks ≠
losses) — read it before touching xlsx/CSV parsing.

`push_pickem_to_sheets.py` reads the pool through the Sheets API (ADC auth) and
writes a week as a new tab (see "Automated import" in the design doc). It is the
only module that imports a Google client; its deps live in
`requirements/pickem.txt`. Its logs must never contain player names, cell
values, or the spreadsheet ID.

The Wednesday watchdog (a Claude routine using the Drive connector, without a
service account) relies on two guarantees. Keep both, and the tests that pin them:

- `push_pickem_to_sheets.py --check-tab` picks the week exactly as a real run
  does and applies the same clash rule as exit 3 (`conflicting_tab`). It reads
  tab names only, from Sheets metadata or from `--workbook pool.xlsx` (which
  needs no Google creds), and makes no Odds API call. It prints one line and
  exits 0 if the tab exists or in the off-season, 4 if it's missing, and 2 on
  a config error.
- `make_pickem_sheet.py --workbook export.xlsx --out week.csv` (no `--week`)
  writes exactly the cells the push would write: same header with `(W-L)`,
  same rows and values. It refuses anything the push refuses (exit 1). It also
  prints `tab_name: <name>` on stderr. Any logic that shapes these cells
  belongs in the generator's shared code (`pool_slice`, `sheet_values`,
  `intended_tab_name`), never in only one of the two paths.

Tests: `tests/test_make_pickem_sheet.py` and `tests/test_push_pickem_to_sheets.py`.
They need no network (The Odds API is a fake `urlopen` with inline fixtures in
the documented v4 shape), `app`, or google-auth. They run under `make test`, or
standalone with `--noconftest`.
