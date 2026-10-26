"""scripts/setup-odds-key.sh with fake `security` and `gh` binaries. No network, no real Keychain."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup-odds-key.sh"
KEY = "0123456789abcdef0123456789abcdef"

FAKE_SECURITY = """#!/bin/bash
# Logs each call; stdin (for `-i`) is appended so tests can see what was fed.
echo "argv: $*" >> "$FAKE_LOG"
if [ "$1" = "-i" ]; then cat >> "$FAKE_LOG"; [ -n "$FAKE_SECURITY_ADD_FAIL" ] && exit 1; touch "$FAKE_STATE/keychain"; exit 0; fi
if [ "$1" = "find-generic-password" ]; then [ -e "$FAKE_STATE/keychain" ] && exit 0; exit 44; fi
exit 2
"""

FAKE_GH = """#!/bin/bash
echo "argv: $*" >> "$FAKE_LOG"
if [ "$1 $2" = "secret set" ]; then
  [ -n "$FAKE_GH_FAIL" ] && { echo "HTTP 404: environment not found" >&2; exit 1; }
  echo "stdin: $(cat)" >> "$FAKE_LOG"; touch "$FAKE_STATE/gh"; exit 0
fi
if [ "$1 $2" = "secret list" ]; then
  [ -e "$FAKE_STATE/gh" ] && printf 'ODDS_API_KEY\\t2026-10-15T12:00:00Z\\n'
  printf 'GCP_SA\\t2026-10-01T12:00:00Z\\n'; exit 0
fi
exit 2
"""


@pytest.fixture
def env(tmp_path):
    bins = tmp_path / "bin"
    state = tmp_path / "state"
    bins.mkdir()
    state.mkdir()
    for name, body in (("security", FAKE_SECURITY), ("gh", FAKE_GH)):
        path = bins / name
        path.write_text(body)
        path.chmod(0o755)
    return {
        "PATH": f"{bins}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "USER": "tester",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_STATE": str(state),
        "ODDS_KEY_SECURITY_BIN": str(bins / "security"),
        "ODDS_KEY_GH_BIN": str(bins / "gh"),
    }


def run(env, *args, stdin=None, **extra):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**env, **extra},
        timeout=30,
    )


def log(env) -> str:
    path = Path(env["FAKE_LOG"])
    return path.read_text() if path.exists() else ""


def test_stores_key_in_keychain_and_github_without_argv_exposure(env):
    result = run(env, "--from-stdin", stdin=KEY + "\n")
    assert result.returncode == 0, result.stderr
    calls = log(env)
    # Keychain: fed through `security -i` on stdin, never as an argument.
    assert "argv: -i" in calls
    assert f"add-generic-password -U -a tester -s sportsbook-odds-api -w {KEY}" in calls
    assert not any(KEY in line for line in calls.splitlines() if line.startswith("argv:"))
    # GitHub: piped to `gh secret set` for the pickem environment.
    assert "argv: secret set ODDS_API_KEY --repo loed3055-cmd/sportsbook-assistant --env pickem" in calls
    assert f"stdin: {KEY}" in calls
    assert KEY not in result.stdout + result.stderr
    assert "keychain: stored" in result.stdout and "github: stored" in result.stdout


def test_refuses_without_a_terminal(env):
    result = run(env, stdin=KEY + "\n")
    assert result.returncode == 2
    assert "own terminal" in result.stderr
    assert log(env) == ""


@pytest.mark.parametrize("bad", ["", "   ", "has space inside1234567890", "short", "x" * 80, "abc;rm -rf ~ 123456789012"])
def test_rejects_malformed_keys(env, bad):
    result = run(env, "--from-stdin", stdin=bad + "\n")
    assert result.returncode == 2
    assert "doesn't look like" in result.stderr or "no key" in result.stderr
    assert log(env) == ""


def test_keychain_only_skips_github(env):
    result = run(env, "--from-stdin", "--keychain-only", stdin=KEY + "\n")
    assert result.returncode == 0, result.stderr
    assert "secret" not in log(env)


def test_custom_repo(env):
    run(env, "--from-stdin", "--repo", "someone/fork", stdin=KEY + "\n")
    assert "--repo someone/fork --env pickem" in log(env)


def test_github_failure_reports_and_exits_nonzero_but_keeps_keychain(env):
    result = run(env, "--from-stdin", stdin=KEY + "\n", FAKE_GH_FAIL="1")
    assert result.returncode == 1
    assert "keychain: stored" in result.stdout
    assert "github: FAILED" in result.stdout
    assert "environment not found" in result.stderr
    assert "docs/pickem-setup.md" in result.stderr


def test_missing_gh_is_reported(env):
    result = run(env, "--from-stdin", stdin=KEY + "\n", ODDS_KEY_GH_BIN="/nonexistent/gh")
    assert result.returncode == 1
    assert "github: FAILED" in result.stdout and "gh" in result.stderr


def test_no_keychain_off_macos(env):
    result = run(env, "--from-stdin", "--keychain-only", stdin=KEY + "\n",
                 ODDS_KEY_SECURITY_BIN="/nonexistent/security")
    assert result.returncode == 1
    assert "keychain: unavailable" in result.stdout


def test_check_reports_without_prompting_or_revealing(env):
    before = run(env, "--check")
    assert before.returncode == 1
    assert "keychain: missing" in before.stdout and "github: missing" in before.stdout

    run(env, "--from-stdin", stdin=KEY + "\n")
    after = run(env, "--check")
    assert after.returncode == 0, after.stdout + after.stderr
    assert "keychain: stored" in after.stdout and "github: stored" in after.stdout
    assert KEY not in after.stdout + after.stderr
    # --check never reads the secret value back.
    assert "-w" not in [line for line in log(env).splitlines() if "find-generic-password" in line][-1].split()


def test_help(env):
    result = run(env, "--help")
    assert result.returncode == 0
    assert "--check" in result.stdout and "--keychain-only" in result.stdout


def test_unknown_flag(env):
    assert run(env, "--bogus").returncode == 2


def test_script_is_executable_by_bash_on_macos_bash_3():
    # macOS ships bash 3.2: no associative arrays, no ${var,,}, no mapfile.
    text = SCRIPT.read_text()
    for construct in ("declare -A", ",,}", "^^}", "mapfile", "readarray"):
        assert construct not in text, construct
