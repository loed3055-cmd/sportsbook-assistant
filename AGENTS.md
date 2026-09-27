# Sportsbook Assistant

- Read `docs/session-handoff.md` for current context and follow the phase order in `docs/ROADMAP.md`.
- Prioritize backend and ETL work: ingestion, normalization, API shape, and schema discipline come before intelligence features.
- Keep FastAPI business logic under `src/app/` and tests under `tests/`.
- Run the tests after backend, API, or schema changes. Use `make test` (Podman + Postgres); see `CLAUDE.md`.
- Keep secrets and populated `.env` files out of Git.
- Team: five subagents in `.claude/agents/`. Roles, delegation flow, and launch backlog are in `docs/agents/README.md`.
