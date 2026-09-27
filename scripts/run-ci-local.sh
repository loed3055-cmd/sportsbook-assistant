#!/usr/bin/env bash
# Replays .github/workflows/ci.yaml in Podman: a pod with a postgres:16 service
# (reached on localhost, like the Actions service container) plus a Python 3.13
# + uv container (same uv version as ci.yaml) that runs the same
# `uv sync --locked` / pytest steps, then the audit job's steps. Uses a copy of
# the repo so the host's .venv is never touched. Cleans up after itself.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
POD="sportsbook-ci-local"
PG_ENV=(-e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=sportsbook_test)

cleanup() { podman pod rm -f "$POD" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

podman pod create --name "$POD" >/dev/null
podman run -d --pod "$POD" --name "$POD-postgres" "${PG_ENV[@]}" \
  --health-cmd "pg_isready -U postgres -d sportsbook_test" \
  docker.io/library/postgres:16 >/dev/null

for _ in $(seq 1 30); do
  podman healthcheck run "$POD-postgres" >/dev/null 2>&1 && break
  sleep 1
done

podman run --rm --pod "$POD" "${PG_ENV[@]}" -e POSTGRES_HOST=localhost \
  -e PYTEST_ARGS="${PYTEST_ARGS:-}" \
  -v "$REPO:/src:ro" ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim \
  bash -euo pipefail -c '
    mkdir /work && cd /src && tar --exclude=./.venv --exclude=./.git --exclude=./examples \
      --exclude="*.egg-info" --exclude="__pycache__" -cf - . | tar -C /work -xf -
    cd /work
    # --- test job steps from ci.yaml ---
    uv sync --locked --extra test
    uv run --locked --no-sync pytest -v ${PYTEST_ARGS:-}
    # --- audit job steps from ci.yaml (export check needs make; diff vs /src) ---
    for x in "" "--extra test" "--extra dev" "--extra prod"; do
      f=base; [ -n "$x" ] && f=${x#--extra }
      uv export --locked --no-hashes --no-emit-project --quiet $x -o requirements/$f.txt
    done
    diff -r /src/requirements requirements
    uv export --locked --all-extras --no-emit-project --quiet -o /tmp/audit-reqs.txt
    uvx --from pip-audit==2.10.1 pip-audit --strict --require-hashes --disable-pip -r /tmp/audit-reqs.txt
  '
