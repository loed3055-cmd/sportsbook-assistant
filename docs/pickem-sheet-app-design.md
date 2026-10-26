# Pick'em sheet app — parked design

Status: **nice-to-have, not built.** `scripts/make_pickem_sheet.py` covers the
weekly need. This records what a fuller version would do and, more importantly,
the things that were verified the hard way so they don't get rediscovered.

## What the script does today

Nothing in it is specific to one pool. It takes the week's full slate, kickoffs
and spreads from The Odds API v4, and the player columns from the pool
spreadsheet itself: names from the latest week tab's header, with each player's
"(W-L)" computed from this season's earlier week tabs (see "Records" below). It
writes a CSV matching the pool sheet's 14 columns. An xlsx schedule is an
optional `--schedule` override, not the source.

**CSV injection guard.** In the CSV (`--out`, stdout, and `--serve`'s `csv`
field), a text cell that starts with a tab, CR or LF, or whose `lstrip()` starts
with `= + - @` (or the full-width `＝＋－＠`), gets a leading `'` so a
spreadsheet opens it as text. A plain number in `week` or `spread` (such as
`-6.5`) is data and is left alone. `rows` and `header` in the JSON keep the raw
values for rendering.

Where the pool comes from, first match wins: tabs the push script already read
through the Sheets API; an uploaded/`--workbook` xlsx; `--headers URL|path`; or
the publish-to-web URL in `PICKEM_PUBLISHED_CSV_URL` (no default). Any
`/pubhtml` or `/pub?output=csv` form is accepted and fetched as the whole
workbook (`/pub?output=xlsx`), because the CSV form only returns the **first**
tab. That was the source of the old stale-header bug: week 3's header was copied
from week 1's. With no pool available, the player columns are the placeholders
`player_1`…`player_6` and a `header_fallback` warning is raised.

**Source: The Odds API.** ESPN was dropped on 2026-09-25: its terms (Disney
ToU) prohibit scripted access. Everything goes through one function,
`fetch_week_slate(season, week) -> list[Game]`, stdlib-only (`urllib`), so the
source can be swapped again. Per run, bounded to the week with
`commenceTimeFrom`/`commenceTimeTo` (UTC, `YYYY-MM-DDTHH:MM:SSZ`):

| Call | Credits |
|---|---|
| `GET /v4/sports/americanfootball_nfl/events?dateFormat=iso` | 0 (does not count against the quota) |
| `GET /v4/sports/americanfootball_nfl/odds?regions=us&markets=spreads&oddsFormat=american&dateFormat=iso` | 1 (markets × regions = 1 × 1; 0 if it returns no events) |

So **1 credit per run**, against 500 a month on the free tier; both responses
are cached for the rest of the process. `x-requests-remaining` and
`x-requests-last` are logged to stderr. `/events` goes first: if the week is
empty, the run fails before spending the credit.

- **Key handling.** `ODDS_API_KEY` travels in the query string, so full URLs
  are never logged. Error text is redacted (the key and any `apiKey=` value),
  URLs are dropped from API error messages, transport exceptions are reported
  by type only, and chained tracebacks are suppressed. A 401 is a config error.
  429 is never retried.
- **Teams.** Full names map to the sheet's codes through an explicit 32-team
  table (`TEAM_CODES`: `WSH`, `JAX`, `LAR`, `LV`, …, matching the latest tabs
  and `grading.py`'s canonical codes). An unknown name is written as-is with an
  `unknown_team` warning.
- **Spread.** From the first bookmaker in the preference list that has a
  usable spreads market (`--bookmakers` / `PICKEM_BOOKMAKERS`, default
  `draftkings,fanduel,betmgm`; must be `regions=us` books). `favorite` = the team
  with the negative point, `spread_source` = that bookmaker's title,
  `spread_as_of_et` = its `last_update` in ET. Malformed markets (both points
  the same sign) fall through to the next bookmaker. A 0/0 line is written as
  spread `0` with no favorite (`pickem_line`).
- **Games without odds.** `/odds` only lists events with odds. Games that are on
  `/events` but not `/odds` are written with no line (`no_line`), and a single
  `missing_games` warning carries the count. No row is ever invented.
- There is no TBD-kickoff signal in the API, so `kickoff_tbd` is gone.

**Week resolution.** With no `--week`, the week comes from the season opener:
`--season-start` / `PICKEM_SEASON_START` (YYYY-MM-DD, must be in the season),
else derived as the Thursday after Labor Day. Week 1 starts at 03:00 ET on the
Wednesday on or before the opener, and each week runs Wednesday 03:00 ET to the
next Wednesday 02:59:59 ET (wall clock, DST-safe). A Wednesday-morning cron after
MNF therefore gets the new week, and 2026's Wednesday opener (NE@SEA, Sep 9)
falls in week 1. Before the season, the generator returns week 1; after it,
week 18. If `/events` lists nothing for the resolved week, the error points at
`PICKEM_SEASON_START`. `--season` defaults to the current NFL season (Jan–Feb
count as the previous year).

### Records

Each player's record is computed from the week tabs of the same season before
the target week, by comparing the player's pick with that row's `ats_winner`:

- Picks are resolved against the two teams in their own row. Matching is
  case-insensitive, uses aliases that mirror `src/app/services/grading.py`
  (`WAS→WSH`, `JAC→JAX`, `LA→LAR`), and accepts a 2+ letter prefix only when it
  matches exactly one side. Anything else (`dale`, `la` in `LV@NO`) is reported
  as `unresolved_pick` and not counted.
- A blank pick is **missed**, not a loss. The label uses the sheet's own format:
  `(W-L)`, plus `; N missed` only when N > 0.
- `ats_winner` = `PUSH` counts as neither a W nor an L. Push counts are **not
  shown**, because no tab of the pool has ever displayed one.
- A blank `ats_winner` raises `ungraded_week` (listing the games), and the
  record is computed from the graded games. Prior tabs are never written to.
- Player columns in earlier tabs are matched by full name, or by the part
  before ` - ` if that is unique, so a nickname change doesn't lose history.
- Week 1 has no earlier tab this season, so the latest tab's header is carried
  through unchanged (last season's numbers).

**Which season a tab belongs to.** A tab whose title has a 4-digit year
(`nfl_week_3_2026_pickem`) belongs to that season. A yearless title (`Week 3`)
belongs to the current season **only if no week tab in the sheet has a year**.
Once any week tab carries a year, yearless tabs belong to no season. They are
left out of records, the template choice, the pool read, and the
"week N already exists" check. Only an exact-name clash still counts. So a
leftover yearless `Week 1` can't block next season's `nfl_week_1_2027_pickem`
(exit 3), and its picks can't count in next season's records.

A pool with no years anywhere can't hold two seasons in one spreadsheet,
because Sheets tab names are unique and next season's `Week 1` would clash with
last season's. Before week 1, rename last season's tabs to include the year
(`Week 1 2026`), or start a new spreadsheet. The tool can't tell the seasons
apart from yearless names alone. Don't `--force` your way past that exit 3:
it would overwrite last season's tab.

Checked against the live sheet on 2026-09-24: the computed week 2 headers equal
the hand-maintained ones for all six players (Edwin 5-11, etc.). The week 3 tab
disagrees (its header is week 1's, copied by the first-tab bug), and week 2 has
no `ats_winner` entered yet, so week 3 and 4 records currently reflect week 1 only.

## HTTP service contract

`python3 scripts/make_pickem_sheet.py --serve 8000` runs a stdlib `http.server`
on `127.0.0.1`. No Podman, no Postgres, no dependencies. It exists so a frontend
can be developed against a real service; it is single-threaded and dev-only.

| Route | Purpose |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `GET /sheet?week=3` | Generate from The Odds API's slate (`ODDS_API_KEY`) + the pool in `PICKEM_PUBLISHED_CSV_URL` |
| `POST /sheet` | `multipart/form-data`: `workbook` (file, optional), `week` (optional) |

`week` is optional everywhere; omitted, it resolves to the upcoming week.
There is no wildcard CORS. Set `PICKEM_SERVE_ORIGIN` (e.g.
`http://localhost:5173`, no default) to allow that one origin, with an
`OPTIONS` preflight, so a Vite dev server on another port works without proxy
config. Unset, the service is same-origin only.

Local-only hardening:

- The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`.
  Anything else gets 403 `forbidden`, which blocks DNS rebinding.
- A POST needs a non-negative integer `Content-Length` (400 otherwise).
- An uploaded xlsx is validated before any Odds API credit is spent. It is
  refused (400) if its parts total more than 50 MB uncompressed (zip bomb), or
  if any XML part declares a `<!DOCTYPE>`. macOS system expat 2.2.8 predates
  the billion-laughs mitigation, and real xlsx parts never have one. The same
  checks apply to `--workbook` and `--schedule` files.

### Success, 200

```json
{
  "week": 3,
  "season": 2026,
  "generated_at": "2026-09-18 17:25 ET",
  "header_source": "uploaded workbook: tab nfl_week_2_2026_pickem; records from 2 earlier week tab(s) of 2026",
  "header": ["week", "kickoff_et", "...", "Edwin - The Favorite (5-11)"],
  "rows": [["3", "2026-09-24 20:15 ET", "ATL@GB", "GB", "-6.5", "DraftKings", "...", ""]],
  "csv": "week,kickoff_et,...\n3,...",
  "warnings": [
    {"kind": "whole_number_line", "game": "NYJ@DET", "detail": "DET -7 can push"}
  ]
}
```

`rows` is for rendering a preview table; `csv` is the same data ready to be
turned into a download client-side. A frontend needs no second endpoint for it.

### Warning kinds

| `kind` | Means |
|---|---|
| `whole_number_line` | Spread is a whole number and can push |
| `no_line` | No odds listed for that game yet, or none of the preferred bookmakers has a spread |
| `missing_games` | Summary: `count` games are on `/events` but not `/odds`; written with no line |
| `pickem_line` | The line is 0 (pick'em); spread `0`, no favorite |
| `unknown_team` | A team name is not in the 32-team table; written as-is |
| `unmatched` | `--schedule` game not found in The Odds API's slate; row written with no line |
| `header_fallback` | No pool sheet configured or readable; placeholder player columns written |
| `ungraded_week` | An earlier week tab has blank `ats_winner` cells; those games aren't in records |
| `unresolved_pick` | A pick cell matches neither team; not counted |
| `unresolved_result` | An `ats_winner` cell matches neither team (nor `PUSH`); game not counted |
| `unknown_player` | An earlier tab has a player column not in the latest header; not counted |

Warnings that name a player or quote a cell also carry a `public_detail` without
them, which is what the push script logs.

Warnings never fail the request. A sheet always comes back.

### Errors

`{"error": "<human message>", "detail": "<stable code>"}`. Branch on `detail`,
not on the message text.

| Status | `detail` |
|---|---|
| 400 | `bad_request` — bad week, unreadable workbook, bad `Content-Length`, xlsx over 50 MB uncompressed or with a `<!DOCTYPE>` |
| 403 | `forbidden` — `Host` header is not `127.0.0.1:<port>` / `localhost:<port>` |
| 404 | `not_found` |
| 413 | `too_large` — upload over 10 MB |
| 415 | `unsupported_media_type` — POST body that is not multipart |
| 500 | `internal_error` |
| 500 | `internal_error` — also a missing `ODDS_API_KEY` or bad `PICKEM_BOOKMAKERS` / `PICKEM_SEASON_START` |
| 502 | `upstream_error` — The Odds API unreachable, errored, or rejected the key |

### What the upload does today

The uploaded workbook is the pool: player names come from its latest week tab
and records are computed from its earlier tabs, exactly as for the live sheet.
Grading (filling `ats_winner`) is still manual.

## Automated import

`scripts/push_pickem_to_sheets.py` replaces the manual CSV import and works for
any pool: point it at a spreadsheet and it reads everything it needs from that
sheet through the Sheets API v4. The steps:

1. List the tabs and batch-read `A1:N` of this season's week tabs (plus the
   latest earlier-season one), in one `values:batchGet`.
2. Call the generator's `build_sheet` with those tabs: The Odds API slate and spreads,
   names from the latest week tab, records computed from earlier ones.
3. Name the new tab from the pattern of the existing week tabs. The latest one
   is templated (`nfl_week_3_2026_pickem` becomes `nfl_week_{week}_{season}_pickem`,
   `Week 3` becomes `Week {week}`), with `nfl_week_{week}_{season}_pickem` as
   the default.
4. Write the week as a new tab.

It is a separate module so `make_pickem_sheet.py` stays stdlib-only. Its
dependencies (`google-auth`, `requests`) are in `requirements/pickem.txt`, not in
the app's requirements.

**How the tab is made.** Each pool tab is a Sheets table (`A1:N17`) with a
frozen header, custom widths, and number validation on `week` and `spread`. So
the script **duplicates the latest earlier week tab**, clears `A2:N`, and writes
the header and rows with `valueInputOption=RAW`. `week` is sent as a JSON integer
and `spread` as a JSON number, so the number validation passes. Every other
cell is stored literally, never evaluated. As a second guard, any string cell
whose `lstrip()` starts with `= + - @` (or the full-width `＝＋－＠`), or which
starts with a tab, CR, or LF, is rejected before any write. With no earlier
week tab, it falls back to a bare `addSheet` with a frozen header. If the write
fails after the tab is created, the new tab is deleted so it can't block the
next run.

**Never overwrites.** If a tab for that week already exists (the exact name, or
any `week N` tab for the season; see "Which season a tab belongs to"), the script exits 3 and changes nothing,
because players may have entered picks. `--force` clears and rewrites that tab
in place.

**Nothing sensitive in logs.** The repo and its Actions logs are public. Output
never contains player names, cell contents, or the spreadsheet ID:

- Warnings print their `public_detail`.
- Validation errors cite counts and cell positions (`C2`, `I1`).
- Sheets API errors print only the HTTP code and Google's `error.status`
  (`HTTP 403 PERMISSION_DENIED`). The free-text `error.message` can quote tab
  or cell contents, so it is printed only with `--show-values`, and even then
  with the ID scrubbed.
- Transport and auth exceptions are reported by type only, with no chained
  traceback.
- Any other exception prints only `error: unexpected <TypeName>; re-run locally
  with --show-values for details` and exits 1. There is no traceback.

`--show-values` lifts this for local dry runs. Never use it in CI.

**Credentials.** Google Application Default Credentials
(`google.auth.default(scopes=[".../auth/spreadsheets"])`). No key material is
passed through env vars:

| Where | How |
|---|---|
| GitHub Actions | Workload Identity Federation via `google-github-actions/auth`, which sets ADC. No JSON key in secrets. |
| Local, impersonation | `gcloud auth application-default login --impersonate-service-account=<sa-email>` |
| Local, key file | `GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` (keep it outside the repo) |

| Env var | Value |
|---|---|
| `ODDS_API_KEY` | Required. The Odds API v4 key. Sent only in the query string of the two calls above; never logged. 1 credit per run. Missing or rejected → exit 2. |
| `PICKEM_SPREADSHEET_ID` | The ID from the sheet's **edit** URL, `/spreadsheets/d/<ID>/edit` (the whole edit URL is accepted too). The `2PACX-…` publish-to-web ID does not work with the API and is rejected. Never logged. |
| `PICKEM_BOOKMAKERS` | Optional. Bookmaker keys in preference order, default `draftkings,fanduel,betmgm`. `--bookmakers` overrides. |
| `PICKEM_SEASON_START` | Optional. Opening day, `YYYY-MM-DD`, in the target season. Default: the Thursday after Labor Day. `--season-start` overrides. |
| `PICKEM_PUBLISHED_CSV_URL` | Optional. Used only by the stdlib generator / `--serve` for local runs without credentials. No default. |

| Flag | Effect |
|---|---|
| `--week N` | Week to push (works in the off-season too). Default: resolved from the season opener, so a **Wednesday-morning ET** cron after MNF gets the new week. Thursday through Tuesday, it resolves to the week in progress, whose tab usually exists already (exit 3). |
| `--season YYYY` | Default: the current NFL season. |
| `--dry-run` | Build and describe the plan. With credentials, it makes only read-only API calls (list tabs, batch read), and it reports whether the tab already exists. |
| `--check-tab` | Only report whether this week's tab exists: one line such as `week 8 2026: tab missing (would be named nfl_week_8_2026_pickem)`. The week and the clash rule are the same as a real run's (off-season included). Reads tab names only (no cell values), needs no `ODDS_API_KEY`, spends no credit. Exit 0 = exists (or off-season), 4 = missing, 2 = config error. |
| `--workbook pool.xlsx` | With `--dry-run` or `--check-tab`: read the pool from a downloaded copy instead of the API. Needs no credentials and no `google-auth`. |
| `--show-values` | Print names, warning details, and the rows. For local use only. |
| `--force` | Clear and rewrite an existing tab for the week. **Destroys entered picks.** |
| `--allow-header-fallback` | Write placeholder player columns if the sheet has no readable week tab (for a brand-new pool). By default this is fatal. |
| `--bookmakers a,b,c` | Bookmaker preference; overrides `PICKEM_BOOKMAKERS`. |
| `--season-start YYYY-MM-DD` | Opening day; overrides `PICKEM_SEASON_START`. |
| `--schedule path.xlsx` | Optional: take matchups from an xlsx instead of The Odds API's slate (joined to its spreads). |

**Off-season guard.** With no `--week`, a run after week 18 ends, or more than
6 days before week 1 starts, prints `off-season: nothing to create` and exits
`0` before any Sheets or Odds API call, so the Wednesday cron (Jan and Sep–Dec)
neither fails nor creates a tab early. Week 1's own Wednesday run creates
week 1; the Wednesday before it is still off-season. An explicit `--week N` bypasses the guard. The guard lives in
the push script only; the generator CLI and `--serve` still clamp to week 1/18.

Configuration is validated before any network call. Exit codes: `0` ok (and
the off-season no-op), `1` build/validation/API failure, `2` configuration
(missing or bad `PICKEM_SPREADSHEET_ID`, missing or rejected `ODDS_API_KEY`,
bad `PICKEM_BOOKMAKERS` / `PICKEM_SEASON_START`, no ADC, `google-auth` missing,
`--workbook` without `--dry-run` or `--check-tab`), `3` the tab already exists,
`4` `--check-tab` found no tab for the week. Warnings are printed to stdout.
Under GitHub Actions they are also emitted as `::warning` annotations.

**Watchdog fallback (no service account).** A Wednesday-noon routine with the
Drive connector runs `--check-tab --workbook <export.xlsx>`. On exit 4, it
runs `make_pickem_sheet.py --workbook <export.xlsx> --out week.csv` with no
`--week`, and writes the CSV into a new tab named after the `tab_name: …`
line on stderr. The CSV is guaranteed to hold exactly the cells this script
would write for the same pool and slate: the 14 columns, the `(W-L)` header,
row order, and the week and spread values. Both paths share the same code.
The pool is narrowed to the tabs the API read covers (`pool_slice`: this
season's week tabs plus the latest earlier one, columns A:N only). The cells
come from `sheet_values`, so whatever the push refuses (formula-like cells,
anything but 14 columns, `header_fallback` without `--allow-header-fallback`)
also fails the generator with exit 1. A test builds both from one fake pool
and one fake slate and asserts they are equal. Unlike the push, the generator
has no off-season guard (it clamps to week 1 or 18), so run `--check-tab`
first.

**One-time setup (Edwin):**
1. In Google Cloud Console, pick or create a project and enable the **Google
   Sheets API**.
2. Create a service account. It needs no project roles and no key.
3. Open the pool spreadsheet, click **Share**, and add the service account's
   email as **Editor**. Its access is limited to what is shared with it.
4. For Actions, set up Workload Identity Federation: create a pool and a GitHub
   provider restricted to this repo, and grant the repo's principal
   `roles/iam.workloadIdentityUser` on the service account. The workflow then
   runs `google-github-actions/auth` with `workload_identity_provider` and
   `service_account`. Add `PICKEM_SPREADSHEET_ID` as a repo secret so it stays
   out of logs.
5. Locally, give yourself `roles/iam.serviceAccountTokenCreator` on the service
   account and run
   `gcloud auth application-default login --impersonate-service-account=<sa-email>`.
6. Test with `--dry-run` first. The first real run should be for a week whose
   tab doesn't exist yet.

```bash
python3 -m venv /tmp/pickem && /tmp/pickem/bin/pip install -r requirements/pickem.txt
ODDS_API_KEY=<key> PICKEM_SPREADSHEET_ID=<id> /tmp/pickem/bin/python scripts/push_pickem_to_sheets.py --week 4 --dry-run
# or, with no Google access at all (downloaded copy of the pool; still 1 Odds API credit):
ODDS_API_KEY=<key> python3 scripts/push_pickem_to_sheets.py --week 4 --dry-run --workbook pool.xlsx
```

## Ideas not built

- **Consensus pricing.** The `/odds` response already carries every US book,
  so the sheet could carry a median across books instead of one bookmaker's
  line, with the favorite decided by majority vote, at no extra credit.
  `src/app/services/odds_api.py` (the backend connector, M2) is separate from
  the stdlib client in the script.
- **Auto-grading.** Fill `ats_winner` from final scores. The Odds API's scores
  endpoint only reaches 3 days back, so a Thursday run can't see Sunday. It
  needs a terms-compatible source of final scores by week (not ESPN: its terms
  prohibit scripted access).

## Verified findings worth keeping

**Two xlsx files, two shapes.** Any parser touching both must handle:

| | Local schedule file | Workbook downloaded from Sheets |
|---|---|---|
| Strings | Inline (`<is><t>`) | `sharedStrings.xml` indices |
| Tab names | `Week 1` … `Week 18` | `nfl_week_1_2026_pickem` |
| Rel targets | Absolute (`/xl/worksheets/…`) | Relative (`worksheets/…`) |

A regex anchored on `week[ _](\d+)` reads both namings without matching the
`2026` in `nfl_week_1_2026_pickem`.

**Excel omits empty cells.** Week 1's second row has 13 `<c>` elements, not 14,
because `ats_winner` is blank. Parsing positionally shifts every later column
left and silently corrupts picks. Cells must be placed by their `r` column
reference. This is the highest-risk bug in anything that reads these files.

**Published sheets use `/pub`, not `/export`.** For a publish-to-web URL
(`/spreadsheets/d/e/2PACX-.../pubhtml`), `.../pub?output=csv` and
`.../pub?output=xlsx` both work; `.../export?format=xlsx` returns 404.

**Pick cells are messy.** Week 1 contains `Ne`, `WSH`, and `NY` against a
`matchup` column using `NE`, `WAS`, `NYJ`. Picks should be resolved against the
two teams in their own row — case-insensitive, with aliases and prefix matching —
which disambiguates `NY` correctly in `NYJ@TEN`. Anything unresolvable should be
reported, never guessed into a win or a loss.

**Missed picks are not losses.** In the original export every player's
W + L + missed summed to exactly 241 (Edwin 127+114, Player4 90+89+62, Player5
110+118+13). The `; N missed` suffix appears only when N > 0.

**Sheet abbreviations.** Week 1's tab spells Washington `WAS`, and later tabs
use `WSH`. Joins go through the alias map, and new rows are written with the
canonical `WSH` (the `TEAM_CODES` table).

## Environment notes

- `.venv/` is a Linux container venv — `bin/python` symlinks to
  `/root/.local/share/uv/python/cpython-3.13.7-linux-aarch64-gnu/bin/python3.13`
  and nothing in it runs on macOS. The script is stdlib-only for this reason.
- `examples/` is local-only (gitignored). No code default or test depends on it.
