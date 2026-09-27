# Session Handoff

Current state (2026-09-26):
- A five-agent launch team is defined in `.claude/agents/`. For roles, the
  delegation flow and the backlog, see `docs/agents/README.md`.
- **M0 is done.** `make test` runs the full suite in Podman against a throwaway
  Postgres. CI mirrors it and adds pip-audit. Dependencies are locked in
  `uv.lock`.
- **M1 is built and reviewed, but not live yet.**
  `.github/workflows/pickem-weekly.yml` creates the new week's tab in the pool
  sheet every Wednesday.
- Backend: predictions and grading, and ingest/ETL with a mock Odds API fetch.

Waiting on Edwin before M1 runs unattended (`docs/pickem-setup.md`):
1. Rotate the credentials that were in the formerly tracked `src/app/.env`.
2. Choose which remote runs the cron: `edwin-lobo/...` or `loed3055-cmd/...`.
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
  - Run the Alembic migration against a live Postgres after the SQLAlchemy 2.1
    upgrade.
