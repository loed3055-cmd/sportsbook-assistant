"""scripts/setup-pickem-cron.sh with fake `gh`, `gcloud` and `security` binaries.

No network, no real GitHub or Google Cloud, no real Keychain. The fakes keep
their "cloud" in files under FAKE_STATE and log every call (argv, and stdin for
calls that read it) to FAKE_LOG.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup-pickem-cron.sh"
REPO = "loed3055-cmd/sportsbook-assistant"
REPO_ID = "1066763931"
PROJECT = "sportsbook-pickem"
PROJECT_NUMBER = "123456789012"
SA_EMAIL = f"pickem-writer@{PROJECT}.iam.gserviceaccount.com"
POOL_NAME = f"projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/github"
PROVIDER_NAME = f"{POOL_NAME}/providers/sportsbook-pickem"
MEMBER = f"principalSet://iam.googleapis.com/{POOL_NAME}/attribute.repository_id/{REPO_ID}"
CONDITION = (
    f"assertion.repository_id == '{REPO_ID}' && assertion.ref == 'refs/heads/main'"
    " && assertion.environment == 'pickem'"
)
APIS = ["sheets.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com", "iam.googleapis.com"]
ODDS_KEY = "0123456789abcdef0123456789abcdef"
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_abcd"

FAKE_GCLOUD = r"""#!/bin/bash
echo "argv: gcloud $*" >> "$FAKE_LOG"
S="$FAKE_STATE"
arg() { local a; for a in "$@"; do case "$a" in "$KEY"=*) printf '%s' "${a#*=}";; esac; done; }
case "$1 $2" in
  "auth list") [ -n "${FAKE_GCLOUD_NOAUTH:-}" ] || echo owner@example.com; exit 0 ;;
  "projects describe") echo 123456789012; exit 0 ;;
  "services list") cat "$S/apis" 2>/dev/null; exit 0 ;;
  "services enable")
    shift 2; for a in "$@"; do case "$a" in --*) ;; *) echo "$a" >> "$S/apis" ;; esac; done; exit 0 ;;
  "projects get-iam-policy") cat "$S/project_roles" 2>/dev/null; exit 0 ;;
  "iam service-accounts")
    case "$3" in
      describe) [ -e "$S/sa" ] || { echo "NOT_FOUND" >&2; exit 1; }; echo "$4"; exit 0 ;;
      create) touch "$S/sa"; exit 0 ;;
      keys) cat "$S/sa_keys" 2>/dev/null; exit 0 ;;
      get-iam-policy) cat "$S/sa_members" 2>/dev/null; exit 0 ;;
      add-iam-policy-binding) KEY=--member; arg "$@" >> "$S/sa_members"; echo >> "$S/sa_members"; exit 0 ;;
    esac ;;
  "iam workload-identity-pools")
    case "$3" in
      describe) [ -e "$S/pool" ] || exit 1; cat "$S/pool"; exit 0 ;;
      create) echo ACTIVE > "$S/pool"; exit 0 ;;
      providers)
        case "$4" in
          describe)
            [ -e "$S/provider_state" ] || exit 1
            case "$*" in *attributeCondition*) cat "$S/provider_condition" ;; *) cat "$S/provider_state" ;; esac
            exit 0 ;;
          create-oidc)
            echo ACTIVE > "$S/provider_state"; KEY=--attribute-condition; arg "$@" > "$S/provider_condition"; exit 0 ;;
        esac ;;
    esac ;;
esac
echo "fake gcloud: unhandled $*" >&2
exit 2
"""

FAKE_GH = r"""#!/bin/bash
echo "argv: gh $*" >> "$FAKE_LOG"
S="$FAKE_STATE"
R="loed3055-cmd/sportsbook-assistant"
E="repos/$R/environments/pickem"
# Like the real gh api, a 404 prints its JSON error body on stdout.
nf() { echo '{"message":"Not Found","status":"404"}'; echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
case "$*" in
  "auth status") [ -n "${FAKE_GH_NOAUTH:-}" ] && { echo "not logged in" >&2; exit 1; }; exit 0 ;;
  "api user --jq .login") echo "${FAKE_GH_LOGIN:-loobo07}"; exit 0 ;;
  "api repos/$R --jq .id") echo 1066763931; exit 0 ;;
  "api repos/$R --jq .permissions.admin") echo "${FAKE_GH_ADMIN:-true}"; exit 0 ;;
  "api $E --jq "*)
    [ -e "$S/env" ] || nf; cat "$S/env"; exit 0 ;;
  "api -X PUT $E --input -") echo "stdin: $(cat)" >> "$FAKE_LOG"; echo true > "$S/env"; exit 0 ;;
  "api $E/deployment-branch-policies --jq "*)
    [ -e "$S/env" ] || nf; cat "$S/policies" 2>/dev/null; exit 0 ;;
  "api -X POST $E/deployment-branch-policies -f name=main -f type=branch") echo main >> "$S/policies"; exit 0 ;;
  "api repos/$R/rules/branches/main --jq "*)
    cat "$S/main_rules" 2>/dev/null; exit 0 ;;
  "api repos/$R/branches/main/protection --jq "*)
    [ -e "$S/main_protection" ] || nf; cat "$S/main_protection"; exit 0 ;;
  "variable list "*)
    [ -e "$S/env" ] || nf; cat "$S/vars" 2>/dev/null; exit 0 ;;
  "variable set "*)
    printf '%s\t%s\n' "$3" "$9" >> "$S/vars"; exit 0 ;;
  "secret list "*)
    [ -e "$S/env" ] || nf; cat "$S/secrets" 2>/dev/null; exit 0 ;;
  "secret set "*)
    [ -e "$S/env" ] || { echo "HTTP 404: environment not found" >&2; exit 1; }
    echo "stdin: $(cat)" >> "$FAKE_LOG"; echo "$3" >> "$S/secrets"; exit 0 ;;
esac
echo "fake gh: unhandled $*" >&2
exit 2
"""

FAKE_SECURITY = r"""#!/bin/bash
echo "argv: security $*" >> "$FAKE_LOG"
[ "$1" = "-i" ] && { echo "stdin: $(cat)" >> "$FAKE_LOG"; exit 0; }
exit 44
"""

# argv fragments of calls that change something. Read-only runs must log none.
MUTATING = (
    " create", "services enable", "add-iam-policy-binding", "secret set", "variable set",
    "-X PUT", "-X POST", "delete", "remove", "undelete", "update", "disable",
)


@pytest.fixture
def env(tmp_path):
    bins = tmp_path / "bin"
    state = tmp_path / "state"
    bins.mkdir()
    state.mkdir()
    for name, body in (("gcloud", FAKE_GCLOUD), ("gh", FAKE_GH), ("security", FAKE_SECURITY)):
        path = bins / name
        path.write_text(body)
        path.chmod(0o755)
    return {
        "PATH": f"{bins}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "USER": "tester",
        "LANG": "C.UTF-8",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_STATE": str(state),
        "PICKEM_SETUP_GCLOUD_BIN": str(bins / "gcloud"),
        "PICKEM_SETUP_GH_BIN": str(bins / "gh"),
        "ODDS_KEY_SECURITY_BIN": str(bins / "security"),
    }


def populate(env, *, skip=()):
    """Write a fully set-up fake cloud, leaving out the items named in `skip`."""
    s = Path(env["FAKE_STATE"])
    files = {
        "apis": "\n".join(APIS) + "\n",
        "sa": "",
        "pool": "ACTIVE\n",
        "provider_state": "ACTIVE\n",
        "provider_condition": CONDITION + "\n",
        "sa_members": MEMBER + "\n",
        "env": "true\n",
        "policies": "main\n",
        "vars": f"GCP_WIF_PROVIDER\t{PROVIDER_NAME}\nGCP_PICKEM_SA\t{SA_EMAIL}\n",
        "secrets": "ODDS_API_KEY\nPICKEM_SPREADSHEET_ID\n",
        "main_rules": "pull_request\nnon_fast_forward\n",
    }
    for name, body in files.items():
        if name not in skip:
            (s / name).write_text(body)


def run(env, *args, stdin=None, project=True, **extra):
    argv = ["bash", str(SCRIPT)]
    if project:
        argv += ["--project", PROJECT]
    return subprocess.run(
        [*argv, *args],
        input=stdin if stdin is not None else "",  # never inherit pytest's stdin
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**env, **extra},
        timeout=60,
    )


def log(env) -> str:
    path = Path(env["FAKE_LOG"])
    return path.read_text() if path.exists() else ""


def argv_lines(env) -> list[str]:
    return [line for line in log(env).splitlines() if line.startswith("argv:")]


def mutating_calls(env) -> list[str]:
    return [line for line in argv_lines(env) if any(m in line for m in MUTATING)]


def state_files(env) -> list[str]:
    return sorted(p.name for p in Path(env["FAKE_STATE"]).iterdir())


# --------------------------------------------------------------------- dry run


def test_dry_run_lists_every_command_without_side_effects(env):
    result = run(env, "--dry-run")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for expected in (
        "would run: gcloud services enable sheets.googleapis.com iamcredentials.googleapis.com"
        " sts.googleapis.com iam.googleapis.com --project=sportsbook-pickem",
        "would run: gcloud iam service-accounts create pickem-writer --project=sportsbook-pickem",
        "would run: gcloud iam workload-identity-pools create github",
        "would run: gcloud iam workload-identity-pools providers create-oidc sportsbook-pickem",
        # shell-quoted for copy-paste: ' inside the condition becomes '\''
        f"'--attribute-condition=assertion.repository_id == '\\''{REPO_ID}'\\''",
        f"add-iam-policy-binding {SA_EMAIL}",
        f"--member={MEMBER}",
        f"would run: gh api -X PUT repos/{REPO}/environments/pickem --input -",
        f"would run: gh api -X POST repos/{REPO}/environments/pickem/deployment-branch-policies"
        " -f name=main -f type=branch",
        f"would run: gh variable set GCP_WIF_PROVIDER --repo {REPO} --env pickem --body {PROVIDER_NAME}",
        f"would run: gh variable set GCP_PICKEM_SA --repo {REPO} --env pickem --body {SA_EMAIL}",
        f"would run: bash scripts/setup-odds-key.sh --repo {REPO}",
        f"would run: <hidden prompt> | gh secret set PICKEM_SPREADSHEET_ID --repo {REPO} --env pickem",
        "Still manual",
    ):
        assert expected in out, expected
    assert REPO_ID in out
    # Nothing changed: no mutating call reached a fake, and the fake cloud is still empty.
    assert mutating_calls(env) == []
    assert state_files(env) == []
    assert "stdin:" not in log(env)


def test_dry_run_without_gcloud_still_prints_the_plan(env):
    result = run(env, "--dry-run", PICKEM_SETUP_GCLOUD_BIN="/nonexistent/gcloud")
    assert result.returncode == 0, result.stderr
    assert "gcloud is not installed" in result.stderr
    assert "https://cloud.google.com/sdk/docs/install" in result.stderr
    assert "would run: gcloud iam service-accounts create pickem-writer" in result.stdout
    assert "<PROJECT_NUMBER>" in result.stdout
    assert "unknown: gcloud unavailable" in result.stdout
    assert state_files(env) == []


def test_dry_run_skips_what_exists(env):
    populate(env, skip=("secrets",))
    result = run(env, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would run: gcloud" not in result.stdout
    assert "would run: gh api" not in result.stdout
    assert "would run: bash scripts/setup-odds-key.sh" in result.stdout
    assert mutating_calls(env) == []


# ------------------------------------------------------------------ check mode


def test_check_fails_on_an_empty_setup_and_changes_nothing(env):
    result = run(env, "--check")
    assert result.returncode == 1
    assert result.stdout.count("✗") >= 10
    assert "✓" not in result.stdout
    assert mutating_calls(env) == []
    assert state_files(env) == []


def test_a_missing_environment_is_not_misread_from_the_404_body(env):
    populate(env, skip=("env", "policies", "vars", "secrets"))
    result = run(env, "--check")
    assert result.returncode == 1
    assert "✗ GitHub environment 'pickem': missing" in result.stdout
    assert "✗ 'pickem' deployment branch policy for main: missing" in result.stdout
    assert "Not Found" not in result.stdout
    assert "other branch policies" not in result.stdout


def test_check_passes_when_everything_is_in_place(env):
    populate(env)
    result = run(env, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "✗" not in result.stdout
    for item in (
        "APIs enabled", "service account exists", "no project roles", "no user-managed keys",
        "WIF pool exists", "WIF provider exists", f"repo id {REPO_ID} may impersonate",
        "GitHub environment 'pickem'", "deploys from main only", "main is protected",
        "variable GCP_WIF_PROVIDER", "variable GCP_PICKEM_SA",
        "secret ODDS_API_KEY", "secret PICKEM_SPREADSHEET_ID",
    ):
        assert f"✓ {item}" in result.stdout or item in result.stdout, item
    assert "All checks passed." in result.stdout
    assert mutating_calls(env) == []


@pytest.mark.parametrize(
    ("skip", "expected"),
    [
        ("apis", "✗ APIs enabled"),
        ("sa", f"✗ service account {SA_EMAIL}"),
        ("pool", "✗ WIF pool github"),
        ("provider_state", "✗ WIF provider sportsbook-pickem"),
        ("sa_members", f"✗ repo id {REPO_ID} may impersonate"),
        ("policies", "✗ 'pickem' deployment branch policy for main"),
        ("vars", "✗ variable GCP_WIF_PROVIDER"),
        ("secrets", "✗ secret PICKEM_SPREADSHEET_ID"),
    ],
)
def test_check_reports_each_missing_item(env, skip, expected):
    populate(env, skip=(skip,))
    result = run(env, "--check")
    assert result.returncode == 1
    assert expected in result.stdout


def test_check_flags_project_roles_keys_and_a_loose_provider(env):
    populate(env)
    state = Path(env["FAKE_STATE"])
    (state / "project_roles").write_text("roles/editor\n")
    (state / "sa_keys").write_text("projects/p/serviceAccounts/x/keys/abc\n")
    (state / "provider_condition").write_text("assertion.repository_owner == 'someone'\n")
    result = run(env, "--check")
    assert result.returncode == 1
    assert "✗ service account has no project roles: it has: roles/editor" in result.stdout
    assert "✗ service account has no user-managed keys: found 1 key(s)" in result.stdout
    assert "✗ WIF provider sportsbook-pickem: its attribute condition doesn't pin" in result.stdout


def test_check_flags_a_wrong_variable_value(env):
    populate(env)
    (Path(env["FAKE_STATE"]) / "vars").write_text(
        f"GCP_WIF_PROVIDER\t{PROVIDER_NAME}\nGCP_PICKEM_SA\tother@x.iam.gserviceaccount.com\n"
    )
    result = run(env, "--check")
    assert result.returncode == 1
    assert "✗ variable GCP_PICKEM_SA: is 'other@x.iam.gserviceaccount.com'" in result.stdout


def test_check_without_gcloud_reports_unknown(env):
    populate(env)
    result = run(env, "--check", PICKEM_SETUP_GCLOUD_BIN="/nonexistent/gcloud")
    assert result.returncode == 1
    assert "https://cloud.google.com/sdk/docs/install" in result.stderr
    assert "✗ APIs enabled: unknown: gcloud unavailable" in result.stdout
    assert "✓ secret ODDS_API_KEY" in result.stdout


# ---------------------------------------------------------------------- apply


def test_apply_from_scratch_creates_everything_and_keeps_secrets_off_argv(env):
    result = run(env, "--yes", "--from-stdin", stdin=f"{ODDS_KEY}\n{SHEET_ID}\n")
    assert result.returncode == 0, result.stdout + result.stderr

    calls = argv_lines(env)
    joined = "\n".join(calls)
    assert "services enable" in joined
    assert "iam service-accounts create pickem-writer" in joined
    assert "workload-identity-pools create github" in joined
    assert "providers create-oidc sportsbook-pickem" in joined
    assert f"add-iam-policy-binding {SA_EMAIL}" in joined
    assert f"api -X PUT repos/{REPO}/environments/pickem --input -" in joined
    assert "deployment-branch-policies -f name=main -f type=branch" in joined
    assert f"secret set ODDS_API_KEY --repo {REPO} --env pickem" in joined
    assert f"secret set PICKEM_SPREADSHEET_ID --repo {REPO} --env pickem" in joined
    # The provider admits only this repo's numeric ID, on main, in the pickem environment.
    assert (Path(env["FAKE_STATE"]) / "provider_condition").read_text() == CONDITION

    # Secrets went over stdin only, never argv, never to the terminal.
    for secret in (ODDS_KEY, SHEET_ID):
        assert not any(secret in line for line in calls), secret
        assert secret not in result.stdout + result.stderr, secret
    assert f"stdin: {ODDS_KEY}" in log(env)
    assert f"stdin: {SHEET_ID}" in log(env)

    # Never deletes or removes anything.
    assert not any(word in joined for word in ("delete", "remove", "disable"))

    # Branch protection is a manual admin step: apply says how, and --check needs it.
    assert "✗ main is protected" in result.stdout and "Rulesets" in result.stdout
    (Path(env["FAKE_STATE"]) / "main_rules").write_text("pull_request\n")
    check = run(env, "--check")
    assert check.returncode == 0, check.stdout + check.stderr


def test_apply_skips_existing_resources(env):
    populate(env)
    result = run(env, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert mutating_calls(env) == []
    assert "would run" not in result.stdout


def test_apply_is_idempotent(env):
    first = run(env, "--yes", "--from-stdin", stdin=f"{ODDS_KEY}\n{SHEET_ID}\n")
    assert first.returncode == 0, first.stdout + first.stderr
    Path(env["FAKE_LOG"]).unlink()
    second = run(env, "--yes", "--from-stdin", stdin="")
    assert second.returncode == 0, second.stdout + second.stderr
    assert mutating_calls(env) == []


def test_apply_only_fills_the_gaps(env):
    populate(env, skip=("pool", "provider_state", "sa_members", "vars"))
    result = run(env, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = mutating_calls(env)
    assert len(calls) == 5, calls  # pool, provider, binding, two variables
    assert not any("services enable" in c or "service-accounts create" in c for c in calls)


def test_apply_updates_a_wrong_variable_value(env):
    populate(env)
    (Path(env["FAKE_STATE"]) / "vars").write_text(f"GCP_WIF_PROVIDER\twrong\nGCP_PICKEM_SA\t{SA_EMAIL}\n")
    result = run(env, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert mutating_calls(env) == [
        f"argv: gh variable set GCP_WIF_PROVIDER --repo {REPO} --env pickem --body {PROVIDER_NAME}"
    ]


def test_apply_reports_but_never_removes_project_roles(env):
    populate(env)
    (Path(env["FAKE_STATE"]) / "project_roles").write_text("roles/editor\n")
    result = run(env, "--yes")
    assert result.returncode == 1
    assert "fix by hand: gcloud projects remove-iam-policy-binding" in result.stdout
    assert mutating_calls(env) == []


def test_apply_without_a_terminal_needs_yes(env):
    result = run(env, stdin="")
    assert result.returncode == 2
    assert "--yes" in result.stderr
    assert argv_lines(env) == []


def test_from_stdin_requires_yes(env):
    result = run(env, "--from-stdin", stdin=f"{ODDS_KEY}\n")
    assert result.returncode == 2
    assert "--yes" in result.stderr


def test_secret_prompt_refuses_without_a_terminal(env):
    populate(env, skip=("secrets",))
    result = run(env, "--yes", stdin=f"{ODDS_KEY}\n{SHEET_ID}\n")
    assert result.returncode == 2
    assert "own terminal" in result.stderr
    assert not any("secret set" in line for line in argv_lines(env))
    assert ODDS_KEY not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2PACX-1vQabcdefghijklmnopqrstuvwxyz0123456789", "Publish-to-web"),
        ("short", "doesn't look like"),
        ("has spaces in it but is long enough to pass", "doesn't look like"),
        ("", "no spreadsheet ID"),
    ],
)
def test_rejects_bad_spreadsheet_ids_without_echoing_them(env, value, message):
    populate(env, skip=("secrets",))
    (Path(env["FAKE_STATE"]) / "secrets").write_text("ODDS_API_KEY\n")
    result = run(env, "--yes", "--from-stdin", stdin=value + "\n")
    assert result.returncode == 2
    assert message in result.stderr
    if value:
        assert value not in result.stdout + result.stderr
    assert not any("secret set" in line for line in argv_lines(env))


def test_accepts_a_full_edit_url(env):
    populate(env, skip=("secrets",))
    (Path(env["FAKE_STATE"]) / "secrets").write_text("ODDS_API_KEY\n")
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"
    result = run(env, "--yes", "--from-stdin", stdin=url + "\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"stdin: {SHEET_ID}" in log(env)
    assert SHEET_ID not in result.stdout + result.stderr


# ------------------------------------------------------------ early errors


def test_no_admin_stops_before_changing_anything(env):
    result = run(env, "--yes", "--from-stdin", stdin=f"{ODDS_KEY}\n{SHEET_ID}\n", FAKE_GH_ADMIN="false")
    assert result.returncode == 2
    err = result.stderr
    assert "'loobo07'" in err and "doesn't have admin on loed3055-cmd/sportsbook-assistant" in err
    assert "loed3055-cmd" in err and "--only gcp" in err
    assert mutating_calls(env) == []


def test_no_admin_can_still_do_the_gcp_half(env):
    result = run(env, "--yes", "--only", "gcp", FAKE_GH_ADMIN="false")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "== GitHub" not in result.stdout
    assert any("service-accounts create" in c for c in mutating_calls(env))
    assert not any("argv: gh" in c for c in mutating_calls(env))


def test_no_admin_check_warns_but_still_reports(env):
    populate(env)
    result = run(env, "--check", FAKE_GH_ADMIN="false")
    assert result.returncode == 0
    assert "doesn't have admin" in result.stderr


def test_github_half_without_gcloud_using_project_number(env):
    populate(env, skip=("vars",))
    result = run(env, "--yes", "--only", "github", "--project-number", PROJECT_NUMBER,
                 PICKEM_SETUP_GCLOUD_BIN="/nonexistent/gcloud")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"--body {PROVIDER_NAME}" in "\n".join(mutating_calls(env))


def test_gcloud_missing_is_an_early_error_for_apply(env):
    result = run(env, "--yes", PICKEM_SETUP_GCLOUD_BIN="/nonexistent/gcloud")
    assert result.returncode == 2
    assert "gcloud is not installed" in result.stderr
    assert "https://cloud.google.com/sdk/docs/install" in result.stderr
    assert mutating_calls(env) == []


def test_gcloud_not_logged_in(env):
    result = run(env, "--yes", FAKE_GCLOUD_NOAUTH="1")
    assert result.returncode == 2
    assert "gcloud auth login" in result.stderr


def test_gh_not_logged_in(env):
    result = run(env, "--check", FAKE_GH_NOAUTH="1")
    assert result.returncode == 2
    assert "gh auth login" in result.stderr


def test_gh_missing(env):
    result = run(env, "--check", PICKEM_SETUP_GH_BIN="/nonexistent/gh")
    assert result.returncode == 2
    assert "gh (GitHub CLI) is not installed" in result.stderr


def test_project_is_required(env):
    result = run(env, "--check", project=False)
    assert result.returncode == 2
    assert "--project" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("--project", "My Project"),
        ("--project", PROJECT, "--repo", "not-a-repo"),
        ("--project", PROJECT, "--only", "both"),
        ("--project", PROJECT, "--pool-id", "x"),
        ("--project", PROJECT, "--bogus"),
    ],
)
def test_bad_input(env, args):
    assert run(env, "--check", *args, project=False).returncode == 2


def test_custom_names_flow_through(env):
    result = run(env, "--dry-run", "--sa-name", "other-writer", "--pool-id", "gh-pool",
                 "--provider-id", "other-prov", "--repo", "someone/fork")
    # The fake gh only knows the default repo, so a custom repo can't be read.
    assert result.returncode == 2
    assert "can't read repo someone/fork" in result.stderr

    result = run(env, "--dry-run", "--sa-name", "other-writer", "--pool-id", "gh-pool",
                 "--provider-id", "other-prov")
    assert result.returncode == 0, result.stderr
    assert f"other-writer@{PROJECT}.iam.gserviceaccount.com" in result.stdout
    assert "workload-identity-pools create gh-pool" in result.stdout
    assert "providers create-oidc other-prov" in result.stdout


def test_help():
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    for flag in ("--check", "--dry-run", "--yes", "--project", "--repo", "--sa-name",
                 "--pool-id", "--provider-id", "--from-stdin", "--only"):
        assert flag in result.stdout, flag


def test_script_is_executable_by_bash_on_macos_bash_3():
    # macOS ships bash 3.2: no associative arrays, case-modifying expansions,
    # mapfile, `|&`, `&>>`, `;;&`, or negative array indices.
    text = SCRIPT.read_text()
    for construct in ("declare -A", "local -A", ",,}", "^^}", "mapfile", "readarray", "|&", "&>>", ";;&", "[-1]"):
        assert construct not in text, construct


def test_reuses_setup_odds_key_instead_of_duplicating_it():
    text = SCRIPT.read_text()
    assert "setup-odds-key.sh" in text
    assert "secret set ODDS_API_KEY" not in text


def test_check_flags_a_condition_loosened_with_extra_clauses(env):
    populate(env)
    (Path(env["FAKE_STATE"]) / "provider_condition").write_text(CONDITION + " || true\n")
    result = run(env, "--check")
    assert result.returncode == 1
    assert "✗ WIF provider sportsbook-pickem: its attribute condition doesn't pin" in result.stdout


def test_check_accepts_the_condition_with_different_whitespace(env):
    populate(env)
    (Path(env["FAKE_STATE"]) / "provider_condition").write_text("  " + CONDITION.replace(" && ", "  &&\n ") + "\n")
    assert run(env, "--check").returncode == 0


def test_check_fails_when_main_is_unprotected(env):
    populate(env, skip=("main_rules",))
    result = run(env, "--check")
    assert result.returncode == 1
    assert "✗ main is protected" in result.stdout
    assert "Rulesets" in result.stdout  # tells them how
    assert mutating_calls(env) == []


def test_classic_branch_protection_with_reviews_counts(env):
    populate(env, skip=("main_rules",))
    (Path(env["FAKE_STATE"]) / "main_protection").write_text("true\n")
    result = run(env, "--check")
    assert result.returncode == 0, result.stdout
    assert "✓ main is protected" in result.stdout


def test_rules_without_a_review_requirement_do_not_count(env):
    populate(env)
    (Path(env["FAKE_STATE"]) / "main_rules").write_text("deletion\n")
    result = run(env, "--check")
    assert result.returncode == 1 and "✗ main is protected" in result.stdout
