---
name: storing-odds-api-key
description: Use when the pick'em script fails with "missing environment variable ODDS_API_KEY", when the user wants the Odds API key stored permanently, rotated, or added to the GitHub pickem cron, or asks whether the key is set up.
---

# Storing the Odds API key

The key lives in two places: the macOS Keychain item `sportsbook-odds-api`
(local runs; `make_pickem_sheet.py` falls back to it when `$ODDS_API_KEY` is
unset) and the GitHub `pickem` environment secret `ODDS_API_KEY` (the
Wednesday cron). `scripts/setup-odds-key.sh` writes both from one hidden prompt.

## Steps

1. Check what's stored. This is safe to run yourself, and it never prints the key:
   `bash scripts/setup-odds-key.sh --check`
2. If something is missing, give the user this command to run **in a separate
   terminal window** (Terminal or iTerm, not the `!` prefix: that has no
   terminal for the hidden prompt, so the script refuses):
   ```bash
   cd ~/sportsbook-assistant && bash scripts/setup-odds-key.sh
   ```
   Add `--keychain-only` if the GitHub `pickem` environment doesn't exist yet
   (`docs/pickem-setup.md`). The cron repo defaults to
   `loed3055-cmd/sportsbook-assistant`; override it with `--repo OWNER/REPO`.
3. When they say it's done, run `--check` again and report the result.

## The key never passes through you

The user types the key only into the script's hidden prompt. If they paste
it into chat anyway, don't use it. Tell them to rotate it at
the-odds-api.com, because the transcript now holds it, and to store the new
key with the script.

## Troubleshooting

| Output | Fix |
|---|---|
| `github: FAILED` … environment not found | Create the `pickem` environment first (`docs/pickem-setup.md` step 3), or use `--keychain-only` |
| `github: unknown` / auth errors | `gh auth login`; they need admin on the cron repo |
| `keychain: unavailable` | Not macOS: export `ODDS_API_KEY` instead |
| Still "missing ODDS_API_KEY" after setup | Run `--check`; on first use, macOS may ask to allow `security`, so click Always Allow |
