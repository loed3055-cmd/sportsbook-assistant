# Session Handoff

Current state (2026-09-28):
- A five-agent launch team is defined in `.claude/agents/`. For roles, the
  delegation flow and the backlog, see `docs/agents/README.md`.
- **M0 is done.** `make test` runs the full suite in Podman against a throwaway
  Postgres. CI mirrors it and adds pip-audit. Dependencies are locked in
  `uv.lock`.
- **M1 is built and reviewed, but not live yet.**
  `.github/workflows/pickem-weekly.yml` creates the new week's tab in the pool
  sheet every Wednesday.
- Backend: predictions and grading, and ingest/ETL with a mock Odds API fetch.
- **Weather** (`/weather`): kickoff-hour forecast at each venue; NWS for US
  stadiums, Open-Meteo for international games, domes marked indoor. On-demand
  refresh; verified live against both APIs on 2026-09-28.
- README rewritten to match the code; `tests/test_readme_features.py` keeps it
  honest. `make dev` works again (`Dockerfile.dev` was missing `PYTHONPATH`).

Waiting on Edwin before M1 runs unattended. Start with `docs/pickem-runbook.md`, then `docs/pickem-setup.md`:
1. Rotate the credentials that were in the formerly tracked `src/app/.env`.
2. The cron runs from `loed3055-cmd/sportsbook-assistant`, the pushable remote. Setup step 5 needs admin on it.
3. GCP service account and WIF setup; GitHub `pickem` environment, variables
   and secrets; an Odds API key.
4. Add the responsible-gambling note to the template tab.
5. Merge to `main`, run a `dry_run` dispatch, then do a real run.

Next:
- **M2:** replace the mock `fetch_odds_from_api` with a real Odds API connector.
- **Known follow-ups:**
  - Move `main.py` from `on_event` to a lifespan handler.
  - Make the stdlib-only check run on Python 3.9, to back the `/usr/bin/python3`
    claim.
  - Schedule the weather refresh (e.g., daily in game weeks) and feed it the
    week's slate with kickoff times; today the caller supplies them.
  - Weather hardening deferred from the 2026-09-28 security review: cache NWS
    `/points` lookups per venue, skip refetching an `ok` row newer than ~30 min,
    and put mutating routes behind auth before the API leaves localhost.
  - `.env.example` is gitignored and untracked, but `docker-compose.dev.yml`
    loads it; a fresh clone can't run `make dev` until one is created.
