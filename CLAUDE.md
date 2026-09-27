# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `AGENTS.md` and `docs/session-handoff.md` first. Follow the phase order in
`docs/ROADMAP.md`: ingestion, normalization, API shape, and schema discipline
come before intelligence/RAG features.

## Launch team

Subagents live in `.claude/agents/`. The main session is the lead. For the delegation flow
and launch backlog, see `docs/agents/README.md`.

## Two independent halves

1. **FastAPI backend** (`src/app/`) on Postgres, run in Podman.
2. **Weekly pick'em tooling** (`scripts/`, `examples/`) — see `scripts/CLAUDE.md`.

## Commands

The README's `make build` / `make run` / `make serve` targets do not exist.

`make test` runs the full suite in Podman (`infra/docker/Dockerfile.test`,
Python 3.13 + uv) against a throwaway `postgres:16` from
`infra/compose/docker-compose.test.yml`, then tears it down.
`scripts/run-ci-local.sh` replays `.github/workflows/ci.yaml` the same way,
including the export check and `pip-audit`. Both take extra pytest args
(`make test PYTEST_ARGS=...`, `PYTEST_ARGS=... bash scripts/run-ci-local.sh`).
Inside that image, or any env from `uv sync --locked --extra test` with a
reachable Postgres:

```bash
PYTHONPATH=src pytest -q                                   # full suite
PYTHONPATH=src pytest tests/test_grading.py::test_name -q  # single test
```

The full suite passes, with no known failures.

Dependencies: `pyproject.toml` is the only place to declare them (extras:
`test`, `dev`, `prod`, `pickem`); `uv.lock` pins exact versions. After editing
pyproject, run `make lock` (re-locks and regenerates `requirements/{base,test,
dev,prod}.txt`, which are pip-compatible exports; never hand-edit them) and
`make audit` (pip-audit on the lock). `requirements/pickem.txt` is
hand-maintained floors for the standalone pick'em venv; keep it in step with the
`pickem` extra.

`.venv/` is a Linux container venv (aarch64); nothing in it runs on macOS.

## Backend architecture

- `app/main.py` calls `Base.metadata.create_all(bind=engine)` **at import
  time**, so importing the app — including from `tests/conftest.py` — connects
  to the real Postgres before conftest swaps `app.db.engine`/`SessionLocal`
  for in-memory SQLite. Tests therefore need a reachable Postgres even though
  they run on SQLite.
- `app/config.py` builds `DATABASE_URL` from `POSTGRES_USER/PASSWORD/HOST/DB`
  (default host `sportsbook-db`, driver `psycopg` v3). The `DATABASE_URL`
  env var set in the compose files is **ignored**. `pyproject.toml` declares
  `psycopg[binary]` (v3).
- Ingest: `routers/ingest.py` runs an APScheduler background scheduler
  (started/stopped in `main.py` startup/shutdown hooks) that calls
  `services/etl.py`: `fetch_odds_from_api` (currently a mock of The Odds API
  shape) → `normalize_odds` → `insert_odds_to_db`.
- Predictions (`/predictions`): stores picks per `(season, week, matchup,
  source)` where `source` is e.g. `edwin`, `claude-chat`, `rag-v1`, plus
  `GameResult` final scores keyed by `(season, week, matchup)` (not team FKs).
  `services/grading.py` is pure (no DB/framework imports): ATS grading against
  each pick's own spread, push on exact whole-number lines, team-code
  normalization (`WAS→WSH`, `JAC→JAX`, `LA→LAR`, case-insensitive). Standings
  endpoint aggregates W-L-P per source.

## Migrations

Alembic lives in `src/alembic/` (`alembic.ini` sets `prepend_sys_path = src`).
There is **no baseline migration**: `teams`, `odds`, `jobs` exist only via
`create_all`, and the first revision (`7c3e1a9b2d4f`) adds `predictions` and
`game_results`. On a DB where the app has already booted, `create_all` will
have created those tables, so use `alembic stamp head` rather than `upgrade`.
