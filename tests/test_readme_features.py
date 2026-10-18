"""README.md is a contract: every command, endpoint, and env var it documents must exist.

The doc-consistency tests parse the README; the behavior tests cover README
features not already exercised elsewhere (see tests/test_api.py for the happy
path of /teams).
"""

import re
from http import HTTPStatus
from pathlib import Path

import pytest

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")

# Endpoint table rows look like: | `GET` | `/teams` | ... |
ENDPOINT_ROW = re.compile(r"^\|\s*`(GET|POST|PUT|PATCH|DELETE)`\s*\|\s*`(/[^`]*)`", re.MULTILINE)


def _make_targets() -> set[str]:
    return set(re.findall(r"^([A-Za-z][\w-]*):", MAKEFILE, re.MULTILINE))


def _app_routes() -> set[tuple[str, str]]:
    # The OpenAPI schema, not app.routes: included routers are nested there.
    return {
        (method.upper(), path.rstrip("/") or "/")
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def _readme_endpoints() -> list[tuple[str, str]]:
    return [(m, p.rstrip("/") or "/") for m, p in ENDPOINT_ROW.findall(README)]


def test_readme_make_targets_exist():
    mentioned = set(re.findall(r"\bmake ([a-z][\w-]*)", README))
    assert mentioned, "README should document at least one make target"
    missing = mentioned - _make_targets()
    assert not missing, f"README mentions make targets missing from Makefile: {sorted(missing)}"


def test_readme_endpoints_are_real_routes():
    documented = _readme_endpoints()
    assert documented, "README should have an endpoint table"
    missing = [e for e in documented if e not in _app_routes()]
    assert not missing, f"README documents endpoints the app does not serve: {missing}"


@pytest.mark.parametrize(
    "prefix", ["/teams", "/ingest", "/predictions", "/weather"]
)
def test_every_mounted_router_is_documented(prefix):
    assert any(path.startswith(prefix) for _, path in _readme_endpoints()), (
        f"{prefix} is mounted but missing from the README endpoint table"
    )


def test_readme_env_vars_match_config():
    for var in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_DB"):
        assert var in README, f"README should document {var} (read by app/config.py)"
    assert "psycopg2" not in README, "the app uses psycopg v3, not psycopg2"
    assert not re.search(r"^DATABASE_URL=", README, re.MULTILINE), (
        "DATABASE_URL is ignored by app/config.py; don't present it as required"
    )


def test_readme_env_defaults_match_config():
    config = (ROOT / "src/app/config.py").read_text(encoding="utf-8")
    defaults = re.findall(r"getenv\('(POSTGRES_\w+)', '([^']*)'\)", config)
    assert len(defaults) == 4, "config.py changed shape; update this test"
    for var, default in defaults:
        assert re.search(rf"\|\s*`{var}`\s*\|\s*`{re.escape(default)}`", README), (
            f"README should list {var} with default {default!r} (src/app/config.py)"
        )


def test_readme_documented_app_env_vars_are_read():
    weather = (ROOT / "src/app/services/weather.py").read_text(encoding="utf-8")
    assert "WEATHER_USER_AGENT" in README and '"WEATHER_USER_AGENT"' in weather


def test_readme_does_not_call_ci_planned():
    # CI runs from .github/workflows/ci.yaml.
    assert "GitHub Actions (planned)" not in README


TEAM = {"sport": "football", "league": "NFL", "name": "Buffalo Bills", "short_name": "BUF"}


def test_duplicate_team_name_is_a_conflict(client):
    assert client.post("/teams/", json=TEAM).status_code == HTTPStatus.OK
    response = client.post("/teams/", json=TEAM)
    assert response.status_code == HTTPStatus.CONFLICT
    # The session is still usable after the rollback.
    assert client.get("/teams/").status_code == HTTPStatus.OK


def test_team_missing_required_fields_rejected(client):
    response = client.post("/teams/", json={"name": "No League"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
