#!/bin/bash
# One-time setup for the Wednesday pick'em cron (.github/workflows/pickem-weekly.yml).
# Scripts steps 1-5 of docs/pickem-setup.md; that doc stays the source of truth.
#
#   bash scripts/setup-pickem-cron.sh --project P --check     # read-only: what's done?
#   bash scripts/setup-pickem-cron.sh --project P --dry-run   # print every command, change nothing
#   bash scripts/setup-pickem-cron.sh --project P             # apply; asks y/N before each step
#
# Idempotent: every step checks first and skips what already exists. It never
# deletes, disables or removes anything. Secrets are read with hidden input and
# reach `gh` on stdin only (never argv); they are never printed.
#
# Exit: 0 all done (or all checks pass, or dry run printed); 1 a check failed or
# a step failed or was declined; 2 usage, missing tool, login or permission error.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="loed3055-cmd/sportsbook-assistant"
PROJECT=""
PROJECT_NUMBER=""
SA_NAME="pickem-writer"
POOL_ID="github"
PROVIDER_ID="sportsbook-pickem"
ENV_NAME="pickem"   # fixed: the workflow and the WIF condition both name it
APIS="sheets.googleapis.com iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com"
GCLOUD="${PICKEM_SETUP_GCLOUD_BIN:-gcloud}"
GH="${PICKEM_SETUP_GH_BIN:-gh}"
GCLOUD_INSTALL_DOC="https://cloud.google.com/sdk/docs/install"
TAB="$(printf '\t')"

usage() {
  cat <<EOF
usage: bash scripts/setup-pickem-cron.sh --project PROJECT_ID [--check | --dry-run] [options]

Modes (default: apply, confirming each step with y/N):
  --check              read-only; report each item; exit 0 only if everything is in place
  --dry-run            print every gcloud/gh command apply would run; change nothing
  --yes                apply without asking before each step

Inputs:
  --project ID         GCP project ID (required)
  --repo OWNER/REPO    repo that runs the cron (default: $REPO)
  --sa-name NAME       service account name (default: $SA_NAME)
  --pool-id ID         Workload Identity Pool ID (default: $POOL_ID)
  --provider-id ID     OIDC provider ID (default: $PROVIDER_ID)
  --project-number N   skip the gcloud lookup of the project number (useful with --only github)
  --only gcp|github    run only the Google Cloud half (steps 2-4) or the GitHub half (step 5)
  --from-stdin         read missing secrets from stdin, one line each, in this order:
                       ODDS_API_KEY, then PICKEM_SPREADSHEET_ID (requires --yes)

The GCP half needs gcloud logged in as a project owner. The GitHub half needs gh
logged in as a repo admin. Steps that stay manual (sharing the sheet, the
responsible-gambling note, merging the workflow, the first run) are listed at the end.
EOF
}

die2() { echo "error: $*" >&2; exit 2; }

MODE="apply"
YES=0
FROM_STDIN=0
ONLY="all"
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    --yes|-y) YES=1 ;;
    --from-stdin) FROM_STDIN=1 ;;
    --project|--repo|--sa-name|--pool-id|--provider-id|--project-number|--only)
      [ $# -ge 2 ] || { usage >&2; exit 2; }
      case "$1" in
        --project) PROJECT="$2" ;;
        --repo) REPO="$2" ;;
        --sa-name) SA_NAME="$2" ;;
        --pool-id) POOL_ID="$2" ;;
        --provider-id) PROVIDER_ID="$2" ;;
        --project-number) PROJECT_NUMBER="$2" ;;
        --only) ONLY="$2" ;;
      esac
      shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

matches() { printf '%s' "$2" | grep -Eq "$1"; }

[ -n "$PROJECT" ] || { echo "error: --project PROJECT_ID is required" >&2; usage >&2; exit 2; }
matches '^[a-z][a-z0-9-]{4,28}[a-z0-9]$' "$PROJECT" || die2 "--project must be a GCP project ID (6-30 lowercase letters, digits, hyphens), not a display name"
matches '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "$REPO" || die2 "--repo must look like OWNER/REPO"
matches '^[a-z][a-z0-9-]{4,28}[a-z0-9]$' "$SA_NAME" || die2 "--sa-name must be 6-30 lowercase letters, digits, hyphens"
matches '^[a-z0-9-]{4,32}$' "$POOL_ID" || die2 "--pool-id must be 4-32 lowercase letters, digits, hyphens"
matches '^[a-z0-9-]{4,32}$' "$PROVIDER_ID" || die2 "--provider-id must be 4-32 lowercase letters, digits, hyphens"
[ -z "$PROJECT_NUMBER" ] || matches '^[0-9]+$' "$PROJECT_NUMBER" || die2 "--project-number must be digits"
case "$ONLY" in all|gcp|github) ;; *) die2 "--only must be gcp or github" ;; esac
if [ "$FROM_STDIN" -eq 1 ] && [ "$MODE" = "apply" ] && [ "$YES" -eq 0 ]; then
  die2 "--from-stdin feeds secrets on stdin, so it can't also answer y/N prompts: add --yes"
fi
if [ "$MODE" = "apply" ] && [ "$YES" -eq 0 ] && [ ! -t 0 ]; then
  die2 "apply asks y/N before each step: run it in your own terminal, or pass --yes"
fi

want_gcp() { [ "$ONLY" != "github" ]; }
want_github() { [ "$ONLY" != "gcp" ]; }

SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
REPO_OWNER="${REPO%%/*}"

# Read-only queries. stdin is /dev/null so a query can never eat piped secrets.
# Output is passed on only on success: `gh api` prints the JSON error body of a
# 404 on stdout, which must never be mistaken for data.
gq() { local out; out="$("$GCLOUD" "$@" </dev/null 2>/dev/null)" || return $?; printf '%s\n' "$out"; }
ghq() { local out; out="$("$GH" "$@" </dev/null 2>/dev/null)" || return $?; printf '%s\n' "$out"; }

# ---------------------------------------------------------------- preflight: gh
command -v "$GH" >/dev/null 2>&1 || die2 "gh (GitHub CLI) is not installed: https://cli.github.com, then run: gh auth login"
"$GH" auth status </dev/null >/dev/null 2>&1 || die2 "gh is not logged in: run gh auth login (as an admin of $REPO for the GitHub half)"
GH_LOGIN="$(ghq api user --jq .login)" || GH_LOGIN="unknown"
REPO_ID="$(ghq api "repos/$REPO" --jq .id)" || REPO_ID=""
matches '^[0-9]+$' "$REPO_ID" || die2 "can't read repo $REPO as gh account '$GH_LOGIN' (does it exist, and can that account see it?)"
GH_ADMIN="$(ghq api "repos/$REPO" --jq .permissions.admin)" || GH_ADMIN="false"

if want_github && [ "$GH_ADMIN" != "true" ]; then
  msg="gh is logged in as '$GH_LOGIN', which doesn't have admin on $REPO.
  Step 5 (the '$ENV_NAME' environment, its variables and secrets) needs a repo admin. Either:
    - run this as the owner: gh auth login as '$REPO_OWNER', then gh auth switch --user $REPO_OWNER; or
    - have '$REPO_OWNER' grant '$GH_LOGIN' admin (repo Settings > Collaborators and teams); or
    - do the Google Cloud half now with --only gcp, and the GitHub half later as an admin with --only github."
  if [ "$MODE" = "apply" ]; then
    echo "error: $msg" >&2; exit 2
  fi
  echo "warning: $msg" >&2
fi

# ------------------------------------------------------------ preflight: gcloud
GCP_READY=1
GCP_WHY=""
GCLOUD_ACCOUNT=""
need_gcloud=0
if want_gcp || [ -z "$PROJECT_NUMBER" ]; then need_gcloud=1; fi
if [ "$need_gcloud" -eq 1 ]; then
  if ! command -v "$GCLOUD" >/dev/null 2>&1; then
    GCP_READY=0
    GCP_WHY="gcloud is not installed. Install the Google Cloud SDK ($GCLOUD_INSTALL_DOC), then run: gcloud auth login (as an Owner of project $PROJECT)"
  else
    GCLOUD_ACCOUNT="$(gq auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
    if [ -z "$GCLOUD_ACCOUNT" ]; then
      GCP_READY=0
      GCP_WHY="gcloud is not logged in: run gcloud auth login (as an Owner of project $PROJECT)"
    fi
  fi
  if [ "$GCP_READY" -eq 1 ] && [ -z "$PROJECT_NUMBER" ]; then
    PROJECT_NUMBER="$(gq projects describe "$PROJECT" --format='value(projectNumber)')" || PROJECT_NUMBER=""
    if ! matches '^[0-9]+$' "$PROJECT_NUMBER"; then
      PROJECT_NUMBER=""
      GCP_READY=0
      GCP_WHY="can't read project $PROJECT as gcloud account '$GCLOUD_ACCOUNT' (wrong project ID, or that account has no access)"
    fi
  fi
  if [ "$GCP_READY" -eq 0 ]; then
    if [ "$MODE" = "apply" ]; then
      echo "error: $GCP_WHY" >&2
      [ "$ONLY" = "github" ] && echo "       (or pass --project-number N to skip the lookup)" >&2
      exit 2
    fi
    echo "warning: $GCP_WHY" >&2
    echo "         Google Cloud items below are reported as unknown." >&2
  fi
fi

PN="${PROJECT_NUMBER:-<PROJECT_NUMBER>}"
POOL_NAME="projects/${PN}/locations/global/workloadIdentityPools/${POOL_ID}"
PROVIDER_NAME="${POOL_NAME}/providers/${PROVIDER_ID}"
MEMBER="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository_id/${REPO_ID}"
CONDITION="assertion.repository_id == '${REPO_ID}' && assertion.ref == 'refs/heads/main' && assertion.environment == '${ENV_NAME}'"

# ----------------------------------------------------------------- reporting
RC=0
SIDE="gcp"
SA_FOUND=0

ok() { echo "✓ $1${2:+ ($2)}"; }
# Collapse all whitespace runs to one space and trim, for exact comparisons.
squash() { printf '%s' "$1" | tr '\t\n' '  ' | tr -s ' ' | sed 's/^ //; s/ $//'; }
# A missing item that apply can create. In check mode it fails the run.
missing() {
  local detail="${2:-missing}"
  if [ "$SIDE" = "gcp" ] && [ "$GCP_READY" -eq 0 ]; then detail="unknown: gcloud unavailable"; fi
  echo "✗ $1: $detail"
  if [ "$MODE" = "check" ]; then RC=1; fi
}
# Something wrong that this script won't change (it never deletes). Always fails the run.
problem() {
  echo "✗ $1: $2"
  [ -n "${3:-}" ] && echo "    fix by hand: $3"
  if [ "$MODE" != "dry-run" ]; then RC=1; fi
}

shq() {
  case "$1" in
    ''|*[!A-Za-z0-9_./:=@,+%-]*) printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")" ;;
    *) printf '%s' "$1" ;;
  esac
}
show_cmd() {
  local first="$1" out a
  shift
  if [ "$first" = "$GCLOUD" ]; then out="gcloud"
  elif [ "$first" = "$GH" ]; then out="gh"
  else out="$(shq "$first")"; fi
  for a in "$@"; do out="$out $(shq "$a")"; done
  printf '%s' "$out"
}

confirm() {
  [ "$YES" -eq 1 ] && return 0
  local ans=""
  printf '    %s? [y/N] ' "$1" >&2
  IFS= read -r ans || true
  case "$ans" in y|Y|yes|YES) return 0 ;; esac
  return 1
}

# act SUMMARY CMD ARGS...: dry-run prints; apply prints, confirms, runs.
act() {
  local summary="$1"
  shift
  if [ "$MODE" = "dry-run" ]; then echo "    would run: $(show_cmd "$@")"; return 0; fi
  echo "    \$ $(show_cmd "$@")"
  if ! confirm "$summary"; then echo "    skipped (declined)"; RC=1; return 1; fi
  if "$@" </dev/null; then echo "    ✓ done"; return 0; fi
  echo "    ✗ FAILED: $summary"; RC=1; return 1
}

# act_input SUMMARY INPUT CMD ARGS...: same, with INPUT (not secret) on stdin.
act_input() {
  local summary="$1" input="$2"
  shift 2
  if [ "$MODE" = "dry-run" ]; then
    printf "    would run: %s --input - <<'EOF'\n    %s\n    EOF\n" "$(show_cmd "$@")" "$input"; return 0
  fi
  printf "    \$ %s --input - <<'EOF'\n    %s\n    EOF\n" "$(show_cmd "$@")" "$input"
  if ! confirm "$summary"; then echo "    skipped (declined)"; RC=1; return 1; fi
  if printf '%s\n' "$input" | "$@" --input - >/dev/null; then echo "    ✓ done"; return 0; fi
  echo "    ✗ FAILED: $summary"; RC=1; return 1
}

secret_input_ok() {
  [ "$FROM_STDIN" -eq 1 ] && return 0
  [ -t 0 ] && return 0
  echo "error: run this in your own terminal so secrets can be typed or pasted hidden" >&2
  echo "       (or pass --from-stdin --yes to pipe them in, e.g. from a password manager CLI)" >&2
  exit 2
}

# ----------------------------------------------------------- GCP: step 2
step_apis() {
  local enabled api missing_apis=""
  enabled="$(gq services list --enabled --project="$PROJECT" --format='value(config.name)')"
  for api in $APIS; do
    printf '%s\n' "$enabled" | grep -qx "$api" || missing_apis="$missing_apis $api"
  done
  if [ -z "$missing_apis" ]; then ok "APIs enabled" "$APIS"; return; fi
  missing "APIs enabled" "not enabled:$missing_apis"
  [ "$MODE" = "check" ] && return
  # shellcheck disable=SC2086  # one word per API
  act "Enable$missing_apis" "$GCLOUD" services enable $missing_apis --project="$PROJECT"
}

step_sa() {
  local found
  found="$(gq iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" --format='value(email)')"
  if [ "$found" = "$SA_EMAIL" ]; then ok "service account exists" "$SA_EMAIL"; SA_FOUND=1; return; fi
  missing "service account $SA_EMAIL"
  [ "$MODE" = "check" ] && return
  act "Create service account $SA_NAME" "$GCLOUD" iam service-accounts create "$SA_NAME" \
    --project="$PROJECT" \
    --display-name="Pick'em sheet writer" \
    --description="GitHub Actions pickem-weekly via WIF. No project roles, no keys." && SA_FOUND=1
}

step_sa_no_roles() {
  local roles keys
  if [ "$GCP_READY" -eq 0 ]; then missing "service account has no project roles and no keys"; return; fi
  [ "$SA_FOUND" -eq 1 ] || return 0   # nothing to inspect yet
  if ! roles="$(gq projects get-iam-policy "$PROJECT" --flatten='bindings[].members' \
      --filter="bindings.members:serviceAccount:$SA_EMAIL" --format='value(bindings.role)')"; then
    problem "service account has no project roles" "unknown: couldn't read the project IAM policy"
  elif [ -n "$roles" ]; then
    problem "service account has no project roles" "it has: $(printf '%s' "$roles" | tr '\n' ' ')" \
      "gcloud projects remove-iam-policy-binding $PROJECT --member=serviceAccount:$SA_EMAIL --role=<role>"
  else
    ok "service account has no project roles"
  fi
  if ! keys="$(gq iam service-accounts keys list --iam-account="$SA_EMAIL" --managed-by=user --format='value(name)')"; then
    problem "service account has no user-managed keys" "unknown: couldn't list its keys"
  elif [ -n "$keys" ]; then
    problem "service account has no user-managed keys" "found $(printf '%s\n' "$keys" | wc -l | tr -d ' ') key(s)" \
      "gcloud iam service-accounts keys delete <KEY_ID> --iam-account=$SA_EMAIL"
  else
    ok "service account has no user-managed keys"
  fi
}

# ----------------------------------------------------------- GCP: step 3
step_pool() {
  local state
  state="$(gq iam workload-identity-pools describe "$POOL_ID" --project="$PROJECT" --location=global --format='value(state)')"
  case "$state" in
    ACTIVE) ok "WIF pool exists" "$POOL_ID"; return ;;
    DELETED)
      problem "WIF pool $POOL_ID" "soft-deleted" \
        "gcloud iam workload-identity-pools undelete $POOL_ID --project=$PROJECT --location=global"
      return ;;
  esac
  missing "WIF pool $POOL_ID"
  [ "$MODE" = "check" ] && return
  act "Create WIF pool $POOL_ID" "$GCLOUD" iam workload-identity-pools create "$POOL_ID" \
    --project="$PROJECT" \
    --location=global \
    --display-name="GitHub Actions Pool"
}

step_provider() {
  local state cond
  state="$(gq iam workload-identity-pools providers describe "$PROVIDER_ID" --project="$PROJECT" \
    --location=global --workload-identity-pool="$POOL_ID" --format='value(state)')"
  case "$state" in
    ACTIVE)
      cond="$(gq iam workload-identity-pools providers describe "$PROVIDER_ID" --project="$PROJECT" \
        --location=global --workload-identity-pool="$POOL_ID" --format='value(attributeCondition)')"
      # The whole condition must match: a substring match would pass "<condition> || true".
      if [ "$(squash "$cond")" = "$(squash "$CONDITION")" ]; then
        ok "WIF provider exists, admits only $REPO (id $REPO_ID) main + $ENV_NAME" "$PROVIDER_ID"; return
      fi
      problem "WIF provider $PROVIDER_ID" "its attribute condition doesn't pin repo id $REPO_ID, refs/heads/main and environment $ENV_NAME" \
        "gcloud iam workload-identity-pools providers update-oidc $PROVIDER_ID --project=$PROJECT --location=global --workload-identity-pool=$POOL_ID --attribute-condition=\"$CONDITION\""
      return ;;
    DISABLED)
      problem "WIF provider $PROVIDER_ID" "disabled" \
        "gcloud iam workload-identity-pools providers update-oidc $PROVIDER_ID --project=$PROJECT --location=global --workload-identity-pool=$POOL_ID --no-disabled"
      return ;;
    DELETED)
      problem "WIF provider $PROVIDER_ID" "soft-deleted" \
        "gcloud iam workload-identity-pools providers undelete $PROVIDER_ID --project=$PROJECT --location=global --workload-identity-pool=$POOL_ID"
      return ;;
  esac
  missing "WIF provider $PROVIDER_ID"
  [ "$MODE" = "check" ] && return
  act "Create OIDC provider $PROVIDER_ID" "$GCLOUD" iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project="$PROJECT" \
    --location=global \
    --workload-identity-pool="$POOL_ID" \
    --display-name="sportsbook-assistant pickem" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref" \
    --attribute-condition="$CONDITION"
}

# ----------------------------------------------------------- GCP: step 4
step_binding() {
  local members
  members="$(gq iam service-accounts get-iam-policy "$SA_EMAIL" --project="$PROJECT" \
    --flatten='bindings[].members' --filter='bindings.role:roles/iam.workloadIdentityUser' \
    --format='value(bindings.members)')"
  if printf '%s\n' "$members" | grep -qxF "$MEMBER"; then
    ok "repo id $REPO_ID may impersonate the service account" "roles/iam.workloadIdentityUser"; return
  fi
  missing "repo id $REPO_ID may impersonate $SA_EMAIL"
  [ "$MODE" = "check" ] && return
  act "Grant the repo workloadIdentityUser on the service account" "$GCLOUD" iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --project="$PROJECT" \
    --role=roles/iam.workloadIdentityUser \
    --member="$MEMBER"
}

# -------------------------------------------------------- GitHub: step 5
step_env() {
  local custom names others=""
  custom="$(ghq api "repos/$REPO/environments/$ENV_NAME" --jq .deployment_branch_policy.custom_branch_policies)"
  if [ $? -ne 0 ]; then
    missing "GitHub environment '$ENV_NAME'"
  elif [ "$custom" != "true" ]; then
    missing "GitHub environment '$ENV_NAME'" "exists, but deployments aren't limited to selected branches"
  else
    custom="ok"
    ok "GitHub environment '$ENV_NAME' exists with selected-branch deployments"
  fi
  if [ "$custom" != "ok" ] && [ "$MODE" != "check" ]; then
    act_input "Create/limit the '$ENV_NAME' environment to selected branches" \
      '{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}' \
      "$GH" api -X PUT "repos/$REPO/environments/$ENV_NAME"
  fi

  names="$(ghq api "repos/$REPO/environments/$ENV_NAME/deployment-branch-policies" --jq '.branch_policies[].name')"
  others="$(printf '%s\n' "$names" | grep -vx 'main' | grep -v '^$' | tr '\n' ' ')"
  if [ -n "$others" ]; then
    problem "'$ENV_NAME' deploys from main only" "other branch policies exist: $others" \
      "remove them in $REPO Settings > Environments > $ENV_NAME"
  fi
  if printf '%s\n' "$names" | grep -qx 'main'; then
    [ -z "$others" ] && ok "'$ENV_NAME' deploys from main only"
    return
  fi
  missing "'$ENV_NAME' deployment branch policy for main"
  [ "$MODE" = "check" ] && return
  act "Allow deployments to '$ENV_NAME' from main" "$GH" api -X POST \
    "repos/$REPO/environments/$ENV_NAME/deployment-branch-policies" -f name=main -f type=branch
}

VARS=""
VARS_OK=1
load_vars() {
  VARS="$(ghq variable list --repo "$REPO" --env "$ENV_NAME" --json name,value --jq '.[] | "\(.name)\t\(.value)"')" || VARS_OK=0
}

step_var() {
  local name="$1" want="$2" line cur
  line="$(printf '%s\n' "$VARS" | grep "^${name}${TAB}" | head -n 1)"
  if [ -n "$line" ]; then
    cur="${line#*"$TAB"}"
    if [ "$cur" = "$want" ]; then ok "variable $name" "$cur"; return; fi
    if [ -z "$PROJECT_NUMBER" ] && [ "$name" = "GCP_WIF_PROVIDER" ]; then
      ok "variable $name" "$cur; not verified: project number unknown"; return
    fi
    missing "variable $name" "is '$cur', expected '$want'"
  elif [ "$VARS_OK" -eq 0 ] && [ "$MODE" = "check" ]; then
    missing "variable $name" "unknown: couldn't list $ENV_NAME variables (does the environment exist? admin needed?)"
    return
  else
    missing "variable $name"
  fi
  [ "$MODE" = "check" ] && return
  act "Set variable $name" "$GH" variable set "$name" --repo "$REPO" --env "$ENV_NAME" --body "$want"
}

SECRETS=""
SECRETS_OK=1
load_secrets() {
  SECRETS_OK=1
  SECRETS="$(ghq secret list --repo "$REPO" --env "$ENV_NAME" --json name --jq '.[].name')" || SECRETS_OK=0
}
has_secret() { printf '%s\n' "$SECRETS" | grep -qx "$1"; }

secret_missing() {
  if [ "$SECRETS_OK" -eq 0 ] && [ "$MODE" = "check" ]; then
    missing "secret $1" "unknown: couldn't list $ENV_NAME secrets (does the environment exist? admin needed?)"
  else
    missing "secret $1"
  fi
}

# The WIF condition and the main-only environment are only as strong as main itself:
# without protection, anyone with push access could change the workflow and use the
# pickem secrets. Never changed here (rulesets are a judgment call); check only.
step_main_protection() {
  local rules prot
  rules="$(ghq api "repos/$REPO/rules/branches/main" --jq '.[].type')" || rules=""
  case "
$rules
" in
    *"
pull_request
"*) ok "main is protected" "a ruleset requires a pull request"; return ;;
  esac
  prot="$(ghq api "repos/$REPO/branches/main/protection" --jq '.required_pull_request_reviews != null')" || prot=""
  if [ "$prot" = "true" ]; then ok "main is protected" "branch protection requires reviews"; return; fi
  missing "main is protected" "nothing requires a pull request, so anyone with push access could change the workflow and use the pickem secrets"
  echo "    fix by hand (admin): Settings > Rules > Rulesets > New branch ruleset: target main,"
  echo "    require a pull request (1 approval), block force pushes"
}

step_secret_odds() {
  local flag="" orc
  if has_secret ODDS_API_KEY; then ok "secret ODDS_API_KEY" "set; value never shown"; return; fi
  secret_missing ODDS_API_KEY
  [ "$MODE" = "check" ] && return
  [ "$FROM_STDIN" -eq 1 ] && flag="--from-stdin"
  if [ "$MODE" = "dry-run" ]; then
    echo "    would run: bash scripts/setup-odds-key.sh --repo $REPO${flag:+ $flag}"
    echo "               (hidden prompt; also stores the key in the macOS Keychain)"
    return
  fi
  echo "    \$ bash scripts/setup-odds-key.sh --repo $REPO${flag:+ $flag}"
  if ! confirm "Store the Odds API key (hidden prompt)"; then echo "    skipped (declined)"; RC=1; return; fi
  secret_input_ok
  # shellcheck disable=SC2086  # empty flag expands to nothing
  ODDS_KEY_GH_BIN="$GH" "${BASH:-bash}" "$SCRIPT_DIR/setup-odds-key.sh" --repo "$REPO" $flag
  orc=$?
  [ "$orc" -eq 2 ] && exit 2
  load_secrets
  if has_secret ODDS_API_KEY; then
    echo "    ✓ done"
  else
    echo "    ✗ FAILED: ODDS_API_KEY is still not in the $ENV_NAME environment"; RC=1
  fi
}

step_secret_sheet() {
  local sid="" err
  if has_secret PICKEM_SPREADSHEET_ID; then ok "secret PICKEM_SPREADSHEET_ID" "set; value never shown"; return; fi
  secret_missing PICKEM_SPREADSHEET_ID
  [ "$MODE" = "check" ] && return
  if [ "$MODE" = "dry-run" ]; then
    echo "    would run: <hidden prompt> | gh secret set PICKEM_SPREADSHEET_ID --repo $REPO --env $ENV_NAME"
    return
  fi
  echo "    \$ <hidden prompt> | gh secret set PICKEM_SPREADSHEET_ID --repo $REPO --env $ENV_NAME"
  if ! confirm "Store the pool sheet's spreadsheet ID (hidden prompt)"; then echo "    skipped (declined)"; RC=1; return; fi
  secret_input_ok
  if [ "$FROM_STDIN" -eq 1 ]; then
    IFS= read -r sid || true
  else
    printf 'Spreadsheet ID, from the sheet'"'"'s /edit URL (input hidden): ' >&2
    IFS= read -rs sid || true
    echo >&2
  fi
  sid="$(printf '%s' "$sid" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$sid" in
    *2PACX-*) unset sid; die2 "that's a Publish-to-web link; use the ID between /d/ and /edit in the sheet's address bar" ;;
    */spreadsheets/d/*) sid="$(printf '%s' "$sid" | sed -n 's#.*/spreadsheets/d/\([A-Za-z0-9_-]*\).*#\1#p')" ;;
  esac
  [ -n "$sid" ] || die2 "no spreadsheet ID entered"
  if ! matches '^[A-Za-z0-9_-]{25,100}$' "$sid"; then
    unset sid; die2 "that doesn't look like a spreadsheet ID (expected 25-100 letters, digits, - or _)"
  fi
  if err=$(printf '%s' "$sid" | "$GH" secret set PICKEM_SPREADSHEET_ID --repo "$REPO" --env "$ENV_NAME" 2>&1 >/dev/null); then
    echo "    ✓ done"
  else
    echo "    ✗ FAILED: gh secret set PICKEM_SPREADSHEET_ID"
    printf '%s\n' "$err" | sed "s/$sid/<redacted>/g" >&2
    RC=1
  fi
  unset sid
}

# --------------------------------------------------------------------- main
case "$MODE" in
  check) echo "Checking pick'em cron setup (read-only)" ;;
  dry-run) echo "Dry run: printing the commands apply would run; nothing is changed" ;;
  apply) echo "Applying pick'em cron setup (docs/pickem-setup.md steps 1-5)" ;;
esac
echo "  repo:            $REPO (id $REPO_ID; gh account $GH_LOGIN, admin=$GH_ADMIN)"
echo "  project:         $PROJECT (number $PN${GCLOUD_ACCOUNT:+; gcloud account $GCLOUD_ACCOUNT})"
echo "  service account: $SA_EMAIL"
echo "  WIF provider:    $PROVIDER_NAME"
echo

if want_gcp; then
  SIDE="gcp"
  echo "== Google Cloud (steps 2-4)"
  step_apis
  step_sa
  if [ "$MODE" = "check" ] || [ "$MODE" = "apply" ]; then step_sa_no_roles; fi
  step_pool
  step_provider
  step_binding
  echo
fi

if want_github; then
  SIDE="github"
  echo "== GitHub (step 5)"
  step_env
  step_main_protection
  load_vars
  step_var GCP_WIF_PROVIDER "$PROVIDER_NAME"
  step_var GCP_PICKEM_SA "$SA_EMAIL"
  load_secrets
  step_secret_odds
  step_secret_sheet
  echo
fi

if [ "$MODE" != "check" ]; then
  cat <<EOF
Still manual (docs/pickem-setup.md):
  - Share the pool sheet, and only that sheet, with $SA_EMAIL as Editor
    (untick "Notify people", then untick "Editors can change permissions and share").
  - Add the responsible-gambling note to the template tab, outside A-N (e.g. P1).
  - Merge .github/workflows/pickem-weekly.yml to main of $REPO, then do the
    first run (step 6): a dry run, then one real week.
  - Disable the workflow in the other remote: gh workflow disable pickem-weekly.yml --repo <other>
EOF
fi

if [ "$MODE" = "dry-run" ]; then exit 0; fi
if [ "$MODE" = "check" ]; then
  if [ "$RC" -eq 0 ]; then echo "All checks passed."; else echo "Some checks failed (✗ above)."; fi
fi
exit "$RC"
