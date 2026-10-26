# Pick'em cron: one-time setup

`.github/workflows/pickem-weekly.yml` runs `scripts/push_pickem_to_sheets.py`
every Wednesday at 13:00 UTC (09:00 ET under EDT, 08:00 ET under EST). The run
creates that week's tab in the pool's Google Sheet. There are no Google keys
anywhere. GitHub's OIDC token is exchanged through Workload Identity Federation
(WIF) for short-lived credentials that impersonate a service account with no
roles. The only thing that account can touch is the one sheet you share with it.

**SLO:** the week's tab is in the pool sheet by **Wednesday 12:00 ET for at
least 17 of 18 regular-season weeks**. Health check: the workflow run status.
GitHub emails you when a scheduled run fails, and each run writes its outcome
to the job summary. To recover a miss, rerun it (see "First run" below).

New to this? Start with `docs/pickem-runbook.md`, which explains where to get each value and how to test locally first.

Do these steps once, in order. Nothing here runs by itself. The cron starts only
after the workflow file is on `main` of the repo you choose.

## Scripted setup

`scripts/setup-pickem-cron.sh` runs steps 1–5 for you. It's idempotent: each step
checks first and skips what already exists, and it never deletes anything. The
manual steps below stay the reference. Run it in your own terminal:

```bash
bash scripts/setup-pickem-cron.sh --project PROJECT_ID --check     # read-only: ✓/✗ per item
bash scripts/setup-pickem-cron.sh --project PROJECT_ID --dry-run   # print every command, change nothing
bash scripts/setup-pickem-cron.sh --project PROJECT_ID             # apply, asking y/N before each step
```

`--repo` defaults to `loed3055-cmd/sportsbook-assistant`. `--sa-name`, `--pool-id`
and `--provider-id` override the defaults used below. The GCP half needs `gcloud`
logged in as a project owner. The GitHub half (step 5) needs `gh` logged in as a
repo **admin**. If those are different accounts, run `--only gcp`, then
`--only github` (add `--project-number N` if that machine has no `gcloud`). Secrets
are read with hidden input and passed to `gh` on stdin. `ODDS_API_KEY` goes
through `scripts/setup-odds-key.sh`, which also stores it in the macOS Keychain.
Sharing the sheet, the responsible-gambling note, and the first run (step 6)
stay manual. The script lists them when it finishes.

## 0. Choose the repo that runs the cron

There are two public remotes:

| Remote | Repo | Repo ID | Notes |
|---|---|---|---|
| `origin` | `edwin-lobo/sportsbook-assistant` | 1070517071 | A **fork** of upstream. GitHub doesn't run Actions in a fork until you enable them in the fork's Actions tab. |
| `upstream` | `loed3055-cmd/sportsbook-assistant` | 1066763931 | Not a fork |

**Use `upstream` (`loed3055-cmd/sportsbook-assistant`).** It's the remote this
machine can push to (`loobo07` has write access), and local branches track it.
`loobo07` has only read access to the fork. Step 5 needs **admin** on the
repo, so run it as the owner account, or have the owner grant `loobo07` admin.

Choose **one** of them. The WIF provider admits only that repo's numeric ID,
so the workflow in the other repo can never authenticate. Disable the workflow
there with `gh workflow disable pickem-weekly.yml --repo <other>` so it doesn't
send failure emails every week.

GitHub disables scheduled workflows in a public repo after **60 days without
repository activity**. In the off-season, check the Actions tab before week 1.

## 1. Variables

Run everything below in one shell. You need `gcloud` logged in as a project
owner and `gh` logged in as a repo admin.

```bash
export GITHUB_REPO="OWNER/REPO"          # the repo you chose in step 0
export PROJECT_ID="your-gcp-project-id"
export POOL_ID="github"                  # Workload Identity Pool
export PROVIDER_ID="sportsbook-pickem"   # OIDC provider in that pool
export SA_NAME="pickem-writer"

export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export REPO_ID="$(gh api "repos/${GITHUB_REPO}" --jq .id)"   # numeric ID; survives renames
echo "repo ${GITHUB_REPO} has id ${REPO_ID}"
```

The attribute condition pins the **numeric** repository ID, not the name. If
the repo is deleted, someone else can create a new repo with the same name, but
the new repo gets a different ID.

## 2. GCP: APIs and a service account with no roles

```bash
gcloud services enable \
  sheets.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}"

gcloud iam service-accounts create "${SA_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="Pick'em sheet writer" \
  --description="GitHub Actions pickem-weekly via WIF. No project roles, no keys."
```

`iam.googleapis.com` is required to create the pool and the service account.
`sts` and `iamcredentials` handle the token exchange and impersonation at run
time.

Don't grant the service account any project role, and never create a key for
it. Both lists below should come back empty:

```bash
gcloud projects get-iam-policy "${PROJECT_ID}" \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:${SA_EMAIL}" \
  --format="value(bindings.role)"
gcloud iam service-accounts keys list --iam-account="${SA_EMAIL}" --managed-by=user
```

**Share the pool sheet (and only that sheet)** with `${SA_EMAIL}` as **Editor**.
In the sheet, open Share, paste the email, pick Editor, and untick "Notify
people". Don't share a folder or a Drive with it. Then open Share → settings
(gear icon) and untick **"Editors can change permissions and share"**, so the
service account can't share the sheet further. If a run ever writes something
wrong, restore from File → Version history.

**Add a responsible-gambling note** to the template tab, outside columns A–N
(for example in cell P1). The Odds API's terms require appropriate
responsible-gambling messaging. Each new tab is copied from the previous one,
and the script clears only `A2:N`, so the note carries forward. Example wording:
"For entertainment only. If gambling stops being fun, call 1-800-GAMBLER." 

## 3. WIF pool and provider

```bash
gcloud iam workload-identity-pools create "${POOL_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --display-name="GitHub Actions Pool"

gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --display-name="sportsbook-assistant pickem" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository_id == '${REPO_ID}' && assertion.ref == 'refs/heads/main' && assertion.environment == 'pickem'"
```

The condition admits only tokens from jobs that run in the `pickem` environment
on this repo's `main` branch. Scheduled runs always use the default branch. A
`workflow_dispatch` from any other branch is refused. Any other workflow added
to `main` later can't impersonate the service account, even if it's granted
`id-token: write`.

Save the two full resource names:

```bash
export POOL_NAME="$(gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --project="${PROJECT_ID}" --location="global" --format="value(name)")"
export PROVIDER_NAME="$(gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" --location="global" --workload-identity-pool="${POOL_ID}" \
  --format="value(name)")"
echo "${POOL_NAME}"       # projects/<number>/locations/global/workloadIdentityPools/github
echo "${PROVIDER_NAME}"   # .../workloadIdentityPools/github/providers/sportsbook-pickem
```

## 4. Let the repo impersonate the service account

```bash
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository_id/${REPO_ID}"
```

This is the service account's only IAM binding. It isn't a project role.

## 5. GitHub: the `pickem` environment, variables and secrets

Create the environment, limited to deployments from `main`:

```bash
gh api -X PUT "repos/${GITHUB_REPO}/environments/pickem" --input - <<'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST "repos/${GITHUB_REPO}/environments/pickem/deployment-branch-policies" \
  -f name=main -f type=branch
```

Add the variables. They aren't secret, but keeping them in the environment keeps
everything in one place:

```bash
gh variable set GCP_WIF_PROVIDER --repo "${GITHUB_REPO}" --env pickem --body "${PROVIDER_NAME}"
gh variable set GCP_PICKEM_SA    --repo "${GITHUB_REPO}" --env pickem --body "${SA_EMAIL}"
# Optional: bookmaker preference, tried in order (default draftkings,fanduel,betmgm).
# gh variable set PICKEM_BOOKMAKERS   --repo "${GITHUB_REPO}" --env pickem --body "draftkings,fanduel"
# Optional: set this only if the season opens on some day other than the Thursday after Labor Day.
# gh variable set PICKEM_SEASON_START --repo "${GITHUB_REPO}" --env pickem --body "2027-09-09"
```

Add the secrets. With no `--body`, `gh` reads the value from standard input,
so it stays out of your shell history:

```bash
gh secret set ODDS_API_KEY          --repo "${GITHUB_REPO}" --env pickem
gh secret set PICKEM_SPREADSHEET_ID --repo "${GITHUB_REPO}" --env pickem   # ID from the sheet's /edit URL
```

`ODDS_API_KEY` is your key for The Odds API (the-odds-api.com); the free tier is
enough. Each run uses 1 credit: the `/events` call is free and `/odds` costs 1. A
missing or rejected key exits 2. From February to August, and before the season
opens, the script prints `off-season: nothing to create` and exits 0. For the
bookmaker preference and season-start overrides, see
`docs/pickem-sheet-app-design.md`.

### 5b. Protect `main` (admin)

The WIF condition and the main-only environment trust whatever is on `main`. Without
protection, anyone with push access could change the workflow and use the `pickem`
secrets. In the repo: **Settings → Rules → Rulesets → New branch ruleset**, target
`main`, then turn on **Require a pull request before merging** (1 approval) and
**Block force pushes**. `scripts/setup-pickem-cron.sh --check` reports
`✗ main is protected` until this is done.

## 6. First run

The workflow file must be on `main` of `${GITHUB_REPO}` first. That means a
merged PR, which you approve.

1. Dry run. It reads the sheet and describes the write, but doesn't write:

   ```bash
   gh workflow run pickem-weekly.yml --repo "${GITHUB_REPO}" --ref main -f dry_run=true
   gh run watch --repo "${GITHUB_REPO}" "$(gh run list --repo "${GITHUB_REPO}" \
     --workflow pickem-weekly.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   ```

   Expect a `DRY RUN: would create tab ...` line. The log never contains player
   names, cell values or the sheet ID.

2. Real run, for a week whose tab **doesn't exist yet**:

   ```bash
   gh workflow run pickem-weekly.yml --repo "${GITHUB_REPO}" --ref main -f week=<N>
   ```

   Check the new tab in the sheet. After that, the Wednesday cron takes over.

## Exit codes and reruns

| Exit | Meaning | Run result |
|---|---|---|
| 0 | Tab created (or dry run OK) | pass |
| 1 | Generation, validation or API failure | **fail** |
| 2 | Configuration error: a secret or variable is missing or wrong, or the week input is invalid | **fail** |
| 3 | The week's tab already exists | pass, with a notice |

Exit 3 passes on purpose. The tab is already in the pool, so the SLO is met.
The script never overwrites the tab, because players may have entered picks.
So a rerun of a week that already succeeded is a safe no-op. The workflow never
passes `--force`. To rewrite a tab, run the script locally (see
`docs/pickem-sheet-app-design.md`, step 5).

## Turning it off (reversible)

- Pause the cron: `gh workflow disable pickem-weekly.yml --repo "${GITHUB_REPO}"`
  (`enable` resumes it).
- Cut Google access without deleting anything. New token exchanges are refused;
  a token already issued lives for about an hour at most:
  `gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" --project="${PROJECT_ID}" --location=global --workload-identity-pool="${POOL_ID}" --disabled`
  (use `--no-disabled` to turn it back on).
- Remove the grant:
  `gcloud iam service-accounts remove-iam-policy-binding "${SA_EMAIL}" --project="${PROJECT_ID}" --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository_id/${REPO_ID}"`
- Or simply un-share the sheet from `${SA_EMAIL}`.
