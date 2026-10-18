# Pick'em runbook

This guide takes you from nothing to a new tab appearing in the pool sheet every
Wednesday. Each stage works on its own, so you can stop at any point and still
have something useful:

| Stage | You get | Time | Google setup? |
|---|---|---|---|
| 1. Try it locally | A preview of next week's tab from your Mac | ~15 min | No |
| 2. Connect Google locally | Create the real tab from your Mac with one command | ~30 min | Yes |
| 3. Turn on the cron | GitHub creates the tab every Wednesday, 9am ET | ~20 min | Yes |

The full command reference for stage 3 is in `docs/pickem-setup.md`. The script
reference (flags, warnings, exit codes) is in `docs/pickem-sheet-app-design.md`.

## Values to collect

Gather these as you go. **Secret** values go in your shell or in GitHub secrets
only. Never put them in a file in this repo, which is public.

| Value | Where to get it | Stage | Secret? |
|---|---|---|---|
| **Odds API key** | Go to the-odds-api.com, choose the free plan, and sign up. The key arrives by email. The free plan gives 500 credits a month, and each run uses 1. | 1+ | Yes |
| **Downloaded pool copy** | In the pool sheet: **File → Download → Microsoft Excel (.xlsx)**. Save it outside the repo, for example in `~/Downloads`, because it holds names and picks. | 1 only | Private |
| **Spreadsheet ID** | Open the pool sheet. The address bar shows `docs.google.com/spreadsheets/d/`**`<ID>`**`/edit`. Copy the part between `/d/` and `/edit`. A *Publish to web* link (it contains `2PACX-`) will not work. | 2+ | Yes |
| **Your Google account** | The Google account that owns or edits the pool sheet. | 2+ | No |
| **GCP project ID** | console.cloud.google.com. Use the project picker at the top to create a project (e.g. `sportsbook-pickem`) or pick one, then copy its **ID**, not its display name. | 2+ | No |
| **Service account email** | Created in stage 2: `pickem-writer@<project-id>.iam.gserviceaccount.com`. You can also find it under Console → IAM & Admin → Service Accounts. | 2+ | No |
| **WIF provider name** | Printed in stage 3 (`echo "${PROVIDER_NAME}"`). You can also find it under Console → IAM & Admin → Workload Identity Federation → `github` → `sportsbook-pickem`. It looks like `projects/<number>/locations/global/workloadIdentityPools/github/providers/sportsbook-pickem`. | 3 | No |
| **Repo** | `loed3055-cmd/sportsbook-assistant` (ID `1066763931`). This is the remote you can push to. The `edwin-lobo` fork is read-only for the `loobo07` account. | 3 | No |

Tools:
- `uv` and `gh` are already installed (`gh` is logged in as `loobo07`).
- Install `gcloud` for stage 2: https://cloud.google.com/sdk/docs/install, then
  run `gcloud auth login`.

## Stage 1: try it locally (no Google setup)

This builds next week's tab from your downloaded copy and prints it. It doesn't
write anything. It spends 1 Odds API credit.

Store your Odds API key once. In your own terminal (it prompts with hidden input):

```bash
bash scripts/setup-odds-key.sh                  # macOS Keychain + GitHub "pickem" secret
bash scripts/setup-odds-key.sh --keychain-only  # until the pickem environment exists
bash scripts/setup-odds-key.sh --check          # where is it stored? (never prints the key)
```

Local runs then find the key in the Keychain with no export needed. `$ODDS_API_KEY`
still takes priority when set. Re-run to replace the key; remove the local copy
with `security delete-generic-password -s sportsbook-odds-api`.

```bash
cd ~/sportsbook-assistant

uv run --no-project --with-requirements requirements/pickem.txt \
  python scripts/push_pickem_to_sheets.py \
  --week <next week> --dry-run --show-values --workbook ~/Downloads/<pool>.xlsx
```

Check the output:
- A line like `week N 2026: 16 games, 16 with a line`.
- `DRY RUN: would create tab nfl_week_N_2026_pickem`.
- The rows. Compare the player records in the header with what you'd expect.
  They're computed from the `ats_winner` column of earlier tabs.
- Warnings. An `ungraded_week` warning means an earlier tab has blank
  `ats_winner` cells, so those games aren't in the records yet.

`--show-values` prints names and picks. Use it only on your own machine, never
in CI.

## Stage 2: connect Google locally

This lets the script read and write the real sheet from your Mac. After this
stage, you can create each week's tab yourself with one command, even without
the cron.

1. Run steps 1–2 of `docs/pickem-setup.md`: set the variables, create the
   service account with no roles, share the sheet with it as Editor, and add
   the responsible-gambling note. For `GITHUB_REPO`, use
   `loed3055-cmd/sportsbook-assistant`.
2. Allow your own Google account to act as the service account, then log in
   as it:

   ```bash
   gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
     --project="${PROJECT_ID}" \
     --member="user:<your-google-account>" \
     --role="roles/iam.serviceAccountTokenCreator"
   gcloud auth application-default login --impersonate-service-account="${SA_EMAIL}"
   ```

   This is a second binding on the service account, alongside the GitHub one
   from stage 3. It lets only your account act as the service account.
3. Dry run against the real sheet. It reads the sheet and writes nothing:

   ```bash
   read -rs PICKEM_SPREADSHEET_ID && export PICKEM_SPREADSHEET_ID
   uv run --no-project --with-requirements requirements/pickem.txt \
     python scripts/push_pickem_to_sheets.py --week <next week> --dry-run
   ```

4. Real run, for a week whose tab **doesn't exist yet**: run the same command
   without `--dry-run`. Then check the new tab in the sheet.

This path hasn't been run against the live Google or Odds APIs yet. If step 3
fails, see Troubleshooting below.

## Stage 3: turn on the Wednesday cron

Follow steps 3–6 of `docs/pickem-setup.md` with
`GITHUB_REPO=loed3055-cmd/sportsbook-assistant`.

- **Admin access needed for step 5.** Creating the `pickem` environment and its
  secrets needs **admin** on the repo. `loobo07` has write access only. Either
  switch `gh` to the owner account for that step
  (`gh auth login`, then `gh auth switch`), or have the owner grant `loobo07`
  admin.
- The workflow must be on `main` before the cron runs. Push the branch, open a
  PR, and merge it.
- In the `edwin-lobo` fork, Actions are off by default, so it won't run
  the job. Leave them off.

## Weekly routine (in season)

| When | What | Who |
|---|---|---|
| After Monday Night Football | Fill in `ats_winner` for every game in that week's tab: the team code that covered, or `PUSH`. Next week's records are computed from this column. | You |
| Wednesday ~9am ET | The new tab appears. GitHub emails you if the run fails. | Cron |
| Wednesday to kickoff | Players enter picks in their columns. | Players |

If the tab hasn't appeared by noon on Wednesday, open the repo's **Actions** tab,
then **pickem-weekly**, then the latest run, and match the error against the
table below. To create the tab by hand, run the stage 2 command from your Mac,
or use **Run workflow** with the week number.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `missing environment variable ...` (exit 2) | The key or ID isn't set in this shell, or isn't a GitHub secret | Set it again: stage 1/2 locally, setup step 5 in CI |
| `HTTP 403 PERMISSION_DENIED` | The sheet isn't shared with the service account, or the ID is for a different sheet | Share the sheet with the service account as Editor, and check the ID |
| Odds API key rejected (exit 2) | Wrong key, or the month's credits are used up | Check the key in your email, and your usage at the-odds-api.com |
| `tab ... already exists` (exit 3) | That week is already done | Nothing to do. To rebuild it locally, `--force` **erases entered picks**. |
| Many `no_line` warnings | Sportsbooks haven't posted lines yet | Fill them in by hand, or rebuild before anyone picks |
| `off-season: nothing to create` | February to August, or more than 6 days before week 1 | Expected. Use `--week N` to force a week. |
| No Wednesday run at all | GitHub pauses scheduled workflows after 60 days without repo activity | Actions tab → pickem-weekly → **Enable workflow** |
| `unexpected <Error>` | A bug or an unexpected API response | Re-run locally with `--show-values` to see details |
