#!/bin/bash
# Store The Odds API key once, everywhere the pick'em tooling looks for it:
#   - macOS Keychain item "sportsbook-odds-api" (local runs of make_pickem_sheet.py)
#   - GitHub secret ODDS_API_KEY in the "pickem" environment (the Wednesday cron)
#
# The key is read with hidden input and never passed as a command-line argument
# (argv is visible to other processes): it is fed to `security -i` and `gh` on stdin.
#
#   bash scripts/setup-odds-key.sh                 # prompt, store in both places
#   bash scripts/setup-odds-key.sh --keychain-only # local runs only
#   bash scripts/setup-odds-key.sh --check         # what's stored? (never prints the key)
#
# Exit: 0 all stored, 1 something failed or is missing, 2 usage/input error.
set -uo pipefail

SERVICE="sportsbook-odds-api"
SECRET_NAME="ODDS_API_KEY"
GH_ENV="pickem"
REPO="loed3055-cmd/sportsbook-assistant"
SECURITY="${ODDS_KEY_SECURITY_BIN:-/usr/bin/security}"
GH="${ODDS_KEY_GH_BIN:-gh}"

usage() {
  cat <<EOF
usage: bash scripts/setup-odds-key.sh [--check] [--keychain-only] [--repo OWNER/REPO] [--from-stdin]

  (no flags)       prompt for the key (hidden) and store it in the macOS Keychain
                   ("$SERVICE") and the GitHub "$GH_ENV" environment secret $SECRET_NAME
  --check          report where the key is stored; never prompts or prints it
  --keychain-only  skip GitHub
  --repo R         GitHub repo for the secret (default: $REPO)
  --from-stdin     read the key from stdin instead of requiring a terminal
EOF
}

check=0
keychain_only=0
from_stdin=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) check=1 ;;
    --keychain-only) keychain_only=1 ;;
    --from-stdin) from_stdin=1 ;;
    --repo)
      [ $# -ge 2 ] || { usage >&2; exit 2; }
      REPO="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

have_keychain() { [ -x "$SECURITY" ]; }
have_gh() { command -v "$GH" >/dev/null 2>&1; }

keychain_status() {
  if ! have_keychain; then echo "unavailable (no $SECURITY; macOS only)"; return 1; fi
  if "$SECURITY" find-generic-password -s "$SERVICE" >/dev/null 2>&1; then echo stored; return 0; fi
  echo missing; return 1
}

github_status() {
  if ! have_gh; then echo "unavailable (gh not installed)"; return 1; fi
  local listing
  if ! listing=$("$GH" secret list --repo "$REPO" --env "$GH_ENV" 2>/dev/null); then
    echo "unknown (gh couldn't list $REPO env $GH_ENV; run: gh auth status)"; return 1
  fi
  if printf '%s\n' "$listing" | cut -f1 | grep -qx "$SECRET_NAME"; then echo stored; return 0; fi
  echo missing; return 1
}

if [ "$check" -eq 1 ]; then
  rc=0
  status=$(keychain_status) || rc=1
  echo "keychain: $status"
  if [ "$keychain_only" -eq 0 ]; then
    status=$(github_status) || rc=1
    echo "github: $status ($REPO, env $GH_ENV)"
  fi
  exit "$rc"
fi

if [ "$from_stdin" -eq 0 ] && [ ! -t 0 ]; then
  echo "error: run this in your own terminal so the key can be typed or pasted hidden" >&2
  echo "       (or pass --from-stdin to pipe it in, e.g. from a password manager CLI)" >&2
  exit 2
fi

if [ "$from_stdin" -eq 1 ]; then
  IFS= read -r key || true
else
  printf 'Odds API key (input hidden): ' >&2
  IFS= read -rs key || true
  echo >&2
fi
# Trim surrounding whitespace (pasted keys often carry a newline or spaces).
key="$(printf '%s' "${key:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

if [ -z "$key" ]; then
  echo "error: no key entered" >&2; exit 2
fi
# Odds API keys are 32 alphanumerics. Accepting only [A-Za-z0-9] also makes the
# `security -i` command line below safe without quoting.
if ! printf '%s' "$key" | grep -Eq '^[A-Za-z0-9]{20,64}$'; then
  echo "error: that doesn't look like an Odds API key (expected 20-64 letters/digits)" >&2; exit 2
fi

rc=0

if have_keychain; then
  # -U updates an existing item. Fed on stdin so the key never appears in argv.
  if printf 'add-generic-password -U -a %s -s %s -w %s\n' "${USER:-$(id -un)}" "$SERVICE" "$key" \
      | "$SECURITY" -i >/dev/null 2>&1; then
    echo "keychain: stored ($SERVICE)"
  else
    echo "keychain: FAILED"; rc=1
  fi
else
  echo "keychain: unavailable (no $SECURITY; macOS only)"; rc=1
fi

if [ "$keychain_only" -eq 0 ]; then
  if ! have_gh; then
    echo "github: FAILED"
    echo "error: gh (GitHub CLI) is not installed; install it, run gh auth login, then re-run" >&2
    rc=1
  elif err=$(printf '%s' "$key" | "$GH" secret set "$SECRET_NAME" --repo "$REPO" --env "$GH_ENV" 2>&1 >/dev/null); then
    echo "github: stored ($REPO, env $GH_ENV)"
  else
    echo "github: FAILED"
    printf '%s\n' "$err" | sed "s/$key/<redacted>/g" >&2
    echo "hint: the \"$GH_ENV\" environment must exist and you need admin on $REPO (docs/pickem-setup.md)" >&2
    rc=1
  fi
fi

unset key
exit "$rc"
