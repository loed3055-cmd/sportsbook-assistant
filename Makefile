dev:
	podman-compose -f infra/compose/docker-compose.dev.yml up --build

TEST_COMPOSE = podman-compose -p sportsbook-test -f infra/compose/docker-compose.test.yml

# Full suite in Podman against a throwaway Postgres; always tears the stack down.
# Extra pytest args: make test PYTEST_ARGS="--ignore=tests/test_foo.py"
test:
	$(TEST_COMPOSE) build app
	$(TEST_COMPOSE) run --rm app pytest -v $(PYTEST_ARGS); rc=$$?; $(TEST_COMPOSE) down -v; exit $$rc

# Re-resolve uv.lock from pyproject.toml, then regenerate the pip-compatible
# requirements/*.txt exports (used by Dockerfile.dev/.prod and the devcontainer).
# requirements/pickem.txt is hand-maintained (standalone pick'em venv).
EXPORT = uv export --locked --no-hashes --no-emit-project --quiet
lock:
	uv lock
	$(EXPORT) -o requirements/base.txt
	$(EXPORT) --extra test -o requirements/test.txt
	$(EXPORT) --extra dev -o requirements/dev.txt
	$(EXPORT) --extra prod -o requirements/prod.txt

# Known-vulnerability scan of the locked dependency set (same as CI).
audit:
	uv export --locked --all-extras --no-emit-project --quiet -o /tmp/sportsbook-audit-reqs.txt
	uvx --from pip-audit==2.10.1 pip-audit --strict --require-hashes --disable-pip -r /tmp/sportsbook-audit-reqs.txt

prod:
	podman-compose -f infra/compose/docker-compose.prod.yml up -d

.PHONY: dev test lock audit prod down

down:
	podman-compose -f infra/compose/docker-compose.dev.yml down
