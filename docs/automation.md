# Automation

The project is designed to run unattended on a server. Runtime data is local and append-only where trends matter, while source plugins use the source ledger to avoid unnecessary API calls.

## Server Cadence

Run two recurring jobs:

```bash
# Hourly prediction/update workflow
worldcup-predictions scheduled-update

# Daily heavier maintenance workflow
worldcup-predictions simulate-tournament
```

With Docker:

```bash
docker compose run --rm predictions worldcup-predictions scheduled-update
docker compose run --rm predictions worldcup-predictions simulate-tournament
```

## Hourly Job

Command:

```bash
worldcup-predictions scheduled-update
```

Responsibilities:

- fetch fresh/stale source data through plugins
- respect API limits via the source ledger
- update tournament fixtures and consensus-confirmed results
- run prediction workflow for all open fixtures with defined opponents
- persist provider-neutral predictions
- persist `srf.ch` and `20min.ch` optimized tips
- write timestamped prediction snapshots
- refresh backtests, prediction audits, provider points, bonus trackers, and postmatch learning artifacts
- write `prediction_run_summaries`
- write standard Markdown reports
- build the published static site under `public/current/`

Important outputs:

- `data/structured/prediction_snapshots.parquet`
- `data/structured/prediction_run_summaries.parquet`
- `data/structured/published_prediction_ledger.parquet`
- `data/structured/source_ledger.parquet`
- `reports/*.md`
- `public/current/`

## Daily Job

Command:

```bash
worldcup-predictions simulate-tournament
```

Responsibilities:

- rebuild predictions on the latest available structured data
- run the 20,000-iteration tournament simulation
- refresh champion, stage, team-goal, group, and bonus-question distributions
- regenerate simulation-derived provider bonus views
- refresh entity alias candidates and entity validation where needed

The daily job is intentionally separate from the hourly job because tournament simulation and maintenance work can be slower and changes less frequently.

The daily simulation starts from the current confirmed tournament state. For analysis or retrospective comparison, run `worldcup-predictions simulate-tournament --from-day-one` to ignore stored final scores and simulate from the initial fixture plan.

## Static Site

The static site reads from `published_prediction_ledger`.

Future rows can move until the relevant match locks. Past rows stay frozen as public historical predictions, while final score fields and provider point totals can be added afterward.

Generated files include:

- `public/current/index.html`
- `public/current/spiele/<match-slug>/index.html`
- `public/current/api/predictions`
- `public/current/api/predictions.json`
- `public/current/assets/site.<hash>.css`
- `public/current/assets/theme.<hash>.js`
- `public/current/assets/favicon.svg`
- `public/current/sitemap.xml`
- `public/current/robots.txt`

Build manually:

```bash
worldcup-predictions site-build
```

Serve locally:

```bash
worldcup-predictions site-serve --host 127.0.0.1 --port 8000
```

With Docker:

```bash
docker compose run --rm --service-ports predictions worldcup-predictions site-serve --host 0.0.0.0 --port 8000
```

Production can serve `public/current/` directly with the included [`../Caddyfile`](../Caddyfile). The generated site is server-rendered HTML plus JSON and hashed static assets, so it does not need an application backend.

Recommended cache policy:

- HTML: 5 minutes
- JSON prediction data: 60 seconds
- Hashed assets: 1 hour immutable

Google Tag Manager is rendered only when `GTM_CONTAINER_ID` is set. Leave it empty for local/public builds without GTM.

## Suggested Cron Entries

Example host cron shape:

```cron
30 * * * * cd /opt/worldcup-predictions && flock -n /tmp/worldcup-predictions-scheduled.lock ./scripts/run-with-timing.sh scheduled-update docker compose run --rm predictions worldcup-predictions scheduled-update >> logs/scheduled-update.log 2>&1
15 7 * * * cd /opt/worldcup-predictions && flock -n /tmp/worldcup-predictions-simulate.lock ./scripts/run-with-timing.sh simulate-tournament docker compose run --rm predictions worldcup-predictions simulate-tournament >> logs/simulate-tournament.log 2>&1
```

Use whatever process supervisor fits the deployment. The important part is the cadence: hourly prediction updates, daily simulation maintenance.

`scripts/run-with-timing.sh` writes one `START` line and one `FINISH` line around the Docker command, including the exit status and wall-clock duration. The application still writes its own run manifest and prediction counts; the wrapper records the scheduler-level runtime, including Docker startup and teardown.

## GitHub Actions Deployment

The repository includes a production deploy workflow at `.github/workflows/deploy.yml`. It runs on every push to `main` and can also be started manually from GitHub Actions.

Required repository secrets:

- `DEPLOY_HOST`: server hostname or IP address.
- `DEPLOY_USER`: SSH user, for example `deploy`.
- `DEPLOY_SSH_KEY`: private SSH key with access to the server.
- `DEPLOY_KNOWN_HOSTS`: pinned SSH known-hosts entry for the server.
- `DEPLOY_PORT`: optional SSH port. Defaults to `22`.

Create `DEPLOY_KNOWN_HOSTS` from a trusted machine and verify the fingerprint before saving it as a GitHub secret:

```bash
ssh-keyscan -p 22 -H 49.13.7.69
```

The workflow connects to `/opt/worldcup-predictions`, fetches `origin/main`, resets the deployment checkout to that revision, rebuilds the `predictions` Docker image, and runs:

```bash
docker compose run --rm predictions worldcup-predictions scheduled-update
```

The deploy command waits up to 30 minutes for `/tmp/worldcup-predictions-scheduled.lock`, so it does not overlap with the hourly cron job when both use the same lock.

## Failure Behavior

Source failures should not block unrelated plugins. A failing source writes diagnostics and source-ledger rows; prediction plugins keep using the latest available structured rows for that source.

Rate limits are stored in the source ledger. When a source returns a known next-safe fetch time, later runs skip that request until it is safe again.
