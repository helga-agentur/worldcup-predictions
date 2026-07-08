# Automation

The project is designed to run unattended on a server. Runtime data is local and append-only where trends matter, while source plugins use the source ledger to avoid unnecessary API calls.

## Server Cadence

Run two recurring jobs:

```bash
# Half-hourly prediction/update workflow
worldcup-predictions scheduled-update

# Daily heavier maintenance workflow
worldcup-predictions simulate-tournament
```

With Docker:

```bash
docker compose run --rm predictions worldcup-predictions scheduled-update
docker compose run --rm predictions worldcup-predictions simulate-tournament
```

## Scheduled Update Job

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

Each scheduled run stores a source-ledger summary in `prediction_run_summaries`. The summary includes per-source status counts, total request decisions, quota cost, item counts reported by source metadata, cache skips from fresh-enough decisions, and HTTP `304 Not Modified` cache hits. The Markdown source report also includes a source-volume table so it is visible when a source suddenly stops returning rows or starts being queried too often.

For HTTP sources using the shared source runtime, response headers are stored in source-ledger metadata with `Set-Cookie` redacted. `ETag` and `Last-Modified` headers are reused as conditional request validators on the next eligible request for the same source request key. A `not_modified` ledger row means the upstream source explicitly said the content has not changed, so the run intentionally writes no new structured source facts for that request.

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

The daily job is intentionally separate from the half-hourly job because tournament simulation and maintenance work can be slower and changes less frequently.

The half-hourly `scheduled-update` command also checks whether the unresolved fixture state or simulation-logic version has changed since the latest current-state simulation. If a result removes an open fixture, resolves a future knockout participant, or the simulation logic was upgraded, it immediately runs the same current-state 20,000-iteration simulation before rebuilding the static site. If the fixture fingerprint and simulation version are unchanged, it skips this simulation refresh.

The daily simulation starts from the current confirmed tournament state. For analysis or retrospective comparison, run `worldcup-predictions simulate-tournament --from-day-one` to ignore stored final scores and simulate from the initial fixture plan.

For future knockout rounds, the simulator advances winners round by round. Existing score matrices are used for known/current fixtures; if a simulated pairing did not already have a published matrix, the simulator generates a matchup-specific baseline matrix on demand, applies the same bounded tournament-outright prior used by published predictions, and caches it for the run before falling back to the neutral matrix. Each summary stores `metadata.matrix_source_counts`, `metadata.market_adjustment_counts`, and a coherent `metadata.forecast_results` path for the modal champion.

## Static Site

The static site reads from `published_prediction_ledger`. The tournament page additionally reads the latest `simulation_summary` champion distribution, which already includes market-outright strength through the simulated match matrices, falling back to `market_outrights` no-vig probabilities (with a source note) when no usable simulation data exists.

When `simulation_summary.metadata.forecast_results` is present, the tournament bracket animation uses published ledger predictions for already-defined fixtures and uses the simulated path only for unresolved future pairings whose teams match the projected bracket slots. This keeps match pages, homepage cards, and bracketry aligned on the same per-fixture forecast while still letting the simulation fill future knockout rounds.

Future rows can move until the relevant match locks. Past rows stay frozen as public historical predictions, while final score fields and provider point totals can be added afterward.

Upcoming matches render as link cards with a 1-X-2 probability bar, the most likely score, and both provider tips with expected points. Past matches render as link cards with the final score, per-match provider points, and a hit-quality chip (exact score / partially correct / wrong outcome). Match detail pages lead with the SRF tip and expected points, a one-sentence explanation of how the tip relates to the most likely result, knockout advancement probabilities where available, and a shaded score-matrix heatmap. Every page carries OpenGraph/Twitter metadata plus a compact JSON-LD graph with `Organization`, `WebSite`, `Dataset`/`DataDownload`, and `BreadcrumbList`; match listings add `ItemList`, tournament probability pages add a team `ItemList`, and match detail pages add an enhanced `SportsEvent`.

Generated files include:

- `public/current/index.html`
- `public/current/de/index.html`
- `public/current/en/index.html`
- `public/current/de/spiele/kommende/index.html` and `.../vergangene/index.html`
- `public/current/en/matches/future/index.html` and `.../past/index.html`
- `public/current/de/turnierprognose/index.html`
- `public/current/en/tournament-forecast/index.html`
- `public/current/de/spiele/<match-slug>/index.html`
- `public/current/en/matches/<match-slug>/index.html`
- `public/current/api/predictions`
- `public/current/assets/site.<hash>.css`
- `public/current/assets/theme.<hash>.js`
- `public/current/assets/favicon.svg`
- `public/current/assets/world-cup-2026-predictions-og.png`
- `public/current/sitemap.xml`
- `public/current/robots.txt`
- `public/current/llms.txt`

Legacy redirect pages are also emitted for `public/current/de/turnier/index.html` and `public/current/en/tournament/index.html`.

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

The front page is a lightweight language redirect. It sends visitors to `/de/` or `/en/` by checking the `helga_language` cookie first, then the browser language list, and falling back to English. Localized pages set that cookie when visited directly. The JSON feed remains shared at `/api/predictions`.

Set `BASE_URL` to the public origin used for absolute URLs in the JSON feed, without a trailing slash. Local development can use `BASE_URL=http://127.0.0.1:8000`; live should use `BASE_URL=https://tippspiel.helga.ch`. The builder tolerates a trailing slash and normalizes it away.

Recommended cache policy:

- HTML: 5 minutes
- JSON prediction data: 60 seconds
- Hashed assets: 1 hour immutable

Google Tag Manager is rendered only when `GTM_CONTAINER_ID` is set. Leave it empty for local/public builds without GTM.

When GTM is enabled, the static theme script pushes these custom events to `dataLayer`: `helga_api_click` for the JSON API link, `helga_blog_click` for the Helga blog post link, `helga_github_click` for the public GitHub repository link, `helga_language_switch` for language-switch clicks, and `helga_scroll_depth` once per page at 25, 50, 75, 90, and 100 percent scroll depth.

## Suggested Cron Entries

Example host cron shape:

```cron
0,30 * * * * cd /opt/worldcup-predictions && flock -n /tmp/worldcup-predictions-scheduled.lock ./scripts/run-with-timing.sh scheduled-update ./scripts/run-live-scheduled-update.sh >> logs/scheduled-update.log 2>&1
15 7 * * * cd /opt/worldcup-predictions && flock -n /tmp/worldcup-predictions-simulate.lock ./scripts/run-with-timing.sh simulate-tournament ./scripts/run-prod-compose.sh run --rm predictions worldcup-predictions simulate-tournament >> logs/simulate-tournament.log 2>&1
```

Use whatever process supervisor fits the deployment. The important part is the cadence: prediction updates twice per hour, daily simulation maintenance. The `0,30` schedule is intentional because a 10-15 minute update run should finish roughly 15 minutes before common `:00` and `:30` kickoff times.

## Helper Scripts

- `scripts/run-with-timing.sh`: cron wrapper that writes one `START` line and one `FINISH` line around a command, including exit status and wall-clock duration. The application still writes its own run manifest and prediction counts; the wrapper records scheduler-level runtime, including Docker startup and teardown.
- `scripts/run-prod-compose.sh`: production compose wrapper that loads `/etc/worldcup-predictions/env` into the host process environment before calling `docker compose -f compose.prod.yaml`.
- `scripts/run-live-scheduled-update.sh`: production scheduled-update wrapper. It pulls the latest `ghcr.io/helga-agentur/worldcup-predictions:main` image, reads the image's `org.opencontainers.image.revision` label, resets the checkout to that exact commit if it is reachable from `origin/main`, then runs `scheduled-update` through `run-prod-compose.sh`.
- `scripts/sync-live-data.sh`: local development helper that pulls ignored live runtime data from the production server into local `./data/`.

## Runtime Data Update Hooks

Runtime data migrations are handled by versioned one-shot hooks:

```bash
./scripts/run-prod-compose.sh run --rm predictions worldcup-predictions data-update-hooks
```

Each hook records a successful run in the `data_update_hooks` structured dataset. Later deploys skip hook ids that are already marked successful. These hooks are for cleanup of persisted runtime data only; they should not run predictions or publish tips.

The scheduled `scheduled-update` command runs pending data update hooks before it reads tournament state or writes new prediction outputs. In normal runs where all hooks are already applied, this is just a cheap no-op check.

## Production Compose

Local development uses `compose.yaml`, which bind-mounts the full repository into the container and can use a repository-local `.env`. Production uses `compose.prod.yaml`, which runs the immutable GHCR image, receives supported variables from the host environment, and mounts only runtime state:

- `data/` for structured DuckDB/Parquet state
- `public/` for generated static site output
- `reports/` for generated diagnostics reports
- `logs/` for cron logs

Live production does not read or depend on `/opt/worldcup-predictions/.env`. Do not create a production `.env` in the repository checkout.

Set production secrets outside the repository in `/etc/worldcup-predictions/env`, owned by root and readable by the deploy user. `scripts/run-prod-compose.sh` loads this file into the host process environment before calling `docker compose -f compose.prod.yaml`, and the production Compose file then passes only the supported variables into the container.

```bash
sudo install -m 0750 -o root -g deploy -d /etc/worldcup-predictions
sudoedit /etc/worldcup-predictions/env
sudo chown root:deploy /etc/worldcup-predictions/env
sudo chmod 0640 /etc/worldcup-predictions/env
```

Supported production variables:

```bash
ODDS_API_KEY=
FOOTBALL_DATA_API_KEY=
KAGGLE_API_TOKEN=
NEWS_API_KEY=
BASE_URL=https://tippspiel.helga.ch
GTM_CONTAINER_ID=
```

The live scheduled-update cron entry must call `./scripts/run-live-scheduled-update.sh`, and other live Docker commands should call `./scripts/run-prod-compose.sh`, not `docker compose` directly, otherwise `/etc/worldcup-predictions/env` will not be loaded.

## Sync Live Runtime Data

Local development can copy the live ignored runtime data into `./data/`:

```bash
./scripts/sync-live-data.sh
```

The script reads local `.env` values for the SSH target:

```bash
DEPLOY_HOST=49.13.7.69
DEPLOY_USER=deploy
DEPLOY_PORT=22
DEPLOY_PATH=/opt/worldcup-predictions
```

These `DEPLOY_*` values are local-only helper settings. They are not production runtime variables and should not be added to `/etc/worldcup-predictions/env`.

Only `data/` is synced. Secrets, Docker credentials, server environment files, generated reports, logs, and the published site are not copied.

The helper requires `rsync` locally and on the live server.

The remote `rsync` runs under non-blocking shared `flock` locks for the scheduled-update and simulation lock files. If live automation is currently writing data, the helper aborts instead of waiting or copying partial structured Parquet/DuckDB state.

## GitHub Actions Image Publishing

The repository includes a production image workflow at `.github/workflows/deploy.yml`. It runs on every push to `main` and can also be started manually from GitHub Actions. Manual runs only publish the production image when the selected ref is `main`.

Deployment follows a pull-on-cron model:

1. GitHub Actions builds the Docker image with Buildx.
2. The image is pushed to GitHub Container Registry as `ghcr.io/helga-agentur/worldcup-predictions:main` and tagged with the source commit SHA.
3. The next live `scheduled-update` cron run pulls the production image.
4. The server reads the image's `org.opencontainers.image.revision` label and resets `/opt/worldcup-predictions` to that exact commit.
5. The same cron run applies pending data update hooks, runs `scheduled-update`, and publishes the regenerated site.

The workflow uses the built-in `GITHUB_TOKEN` to push the image to GHCR. The production server pulls the image as the `deploy` user, so that user must be logged in to GHCR when the package is private or organization policy blocks public package visibility.

Create a GitHub token with `read:packages` only, authorize it for the organization if SSO is required, then log in once on the server as `deploy`:

```bash
read -rsp 'GitHub token: ' GHCR_PAT
echo
printf '%s' "$GHCR_PAT" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
unset GHCR_PAT
```

Docker stores this pull credential under the deploy user's Docker config. Do not put the GHCR token in the repository, GitHub Actions secrets, cron, or `/etc/worldcup-predictions/env`.

The server-side scheduled-update wrapper runs:

```bash
docker compose -f compose.prod.yaml pull predictions
docker image inspect ghcr.io/helga-agentur/worldcup-predictions:main
git fetch --prune origin main:refs/remotes/origin/main
git reset --hard <image-revision>
./scripts/run-prod-compose.sh run --rm predictions worldcup-predictions scheduled-update
```

This means a successful image publish is picked up on the next half-hourly cron run. If the image build fails, live keeps using the previous image. If cron runs before GitHub Actions has finished pushing the new image, live keeps using the previous image and tries again on the next cron run.

Scheduled-update logs include the pulled image digest, image revision, checkout reset target, and normal prediction/site-build output. The scheduled-update cron lock still serializes code/image promotion with runtime data writes.

One-time migration for an existing live server:

1. Wait for a successful image publish from this commit.
2. Under the scheduled-update lock, update `/opt/worldcup-predictions` to `origin/main` once so `scripts/run-live-scheduled-update.sh` exists.
3. Update the scheduled-update crontab line to call `./scripts/run-live-scheduled-update.sh` as shown above.
4. Leave the daily `simulate-tournament` cron line on `./scripts/run-prod-compose.sh`.

## Failure Behavior

Source failures should not block unrelated plugins. A failing source writes diagnostics and source-ledger rows; prediction plugins keep using the latest available structured rows for that source.

Rate limits are stored in the source ledger. When a source returns a known next-safe fetch time, later runs skip that request until it is safe again.
