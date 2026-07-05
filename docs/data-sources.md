# Data Sources

The workflow fetches public/API data through source plugins and stores extracted structured facts in DuckDB/Parquet. It does not keep raw response caches. Every quota-limited source must consult the source ledger before making a request and record the outcome afterward.

The main bootstrap command is:

```bash
docker compose run --rm predictions worldcup-predictions scheduled-update
```

This command runs the source plugins that are relevant to currently open fixtures and writes structured rows under `data/structured/`.

## Source Ledger

The source ledger protects API limits and makes skipped calls auditable. A source request records:

- source, endpoint, purpose, fixture key, params, quota cost, and optional quota scope
- whether the call was made, skipped, failed, or rate-limited
- quota remaining and next safe fetch time when known
- source-specific metadata such as extracted row counts

Skipped calls are recorded with status `skipped`, so daily reports can show calls made, calls avoided, quota cost spent, and quota cost avoided.

Exact request keys still control ordinary freshness and HTTP cache validators. When a plugin declares a shared quota scope, provider-level blocks are also reused across sibling requests: one NewsAPI 429, Odds API quota exhaustion, or football-data.org rate-limit response prevents different fixture queries in the same scope from immediately spending more calls. Freshness skips for one exact request do not block sibling requests.

## Core Sources

### Historical Results

Plugin: `historical_results_source`

Fetches `martj42/international_results` CSVs:

- `results.csv`
- `shootouts.csv`

Writes:

- `historical_results`
- `shootouts`

Used for Elo ratings, historical goal profiles, backtesting, model calibration, and penalty-shootout priors.

### World Cup Fixtures And Results

Plugins:

- `srf_public`
- `openfootball_source`
- `fifa_match_centre`
- `football_data`
- `public_score_sources`
- `dynamic_public_sources`

These plugins write fixture and result observations. Raw result observations enter `tournament_results` first. A final score is used by tournament state, calibration, provider points, and website publishing only after consensus confirmation: at least three independent sources agree, or at least two high-authority sources agree.

`fifa_match_centre` fetches FIFA's public calendar API for World Cup 2026 (`idCompetition=17`, `idSeason=285023`). It writes official fixture/result evidence plus `fifa_match_details` rows with match number, group/stage, venue, officials, attendance, possession when available, and formations/tactics. The API does not currently expose player-level starting XIs, so formations are used as neutral official lineup context rather than as player availability data.

`dynamic_public_sources` extends the fixed public adapters with bounded public-page discovery. It starts from known public seed pages, fetches only robots-allowed same-domain pages through the source ledger, stores page metadata and content fingerprints, then extracts atomic fixture/result/market claims. Result claims are promoted to `tournament_results` only after the dynamic layer has multi-domain weighted consensus; those promoted rows still pass through the central tournament-state result consensus before they can affect published results, calibration, or provider points.

Writes:

- `public_source_pages`
- `public_source_claims`
- `public_claim_consensus`
- `public_source_reputation`
- `public_market_observations`
- `public_match_analysis`
- `extraction_diagnostics`

### Market Odds

Plugin: `market_odds`

Requires `ODDS_API_KEY`.

Fetches The Odds API markets:

- `h2h`
- `totals`
- `spreads`
- near-kickoff event markets such as `draw_no_bet`, `btts`, `team_totals`, `alternate_totals`, and `alternate_spreads`
- outright tournament winner odds

Writes:

- `market_odds`
- `market_outrights`

Market signals are strong prediction inputs, but capped below a full overwrite so the transparent score model and other signals still matter.

### Market Trends

Plugin: `market_trend`

Reads append-only `market_odds` rows plus high-confidence `public_market_observations` rows and derives small movement signals such as totals-line drift, favorite movement, and cross-snapshot disagreement.

Writes:

- `market_trends`

### football-data.org

Plugin: `football_data`

Requires `FOOTBALL_DATA_API_KEY`.

Fetches competition metadata, fixtures, results, standings, teams, squads, and selected match details.

Writes:

- `football_data_competition`
- `football_data_standings`
- `football_data_teams`
- `football_data_match_details`
- `tournament_fixtures`
- `tournament_results`
- `squad_players`

### Kaggle

Plugin: `kaggle_source`

Requires `KAGGLE_API_TOKEN`.

Kaggle is optional and supplemental. It searches football/player-value/squad datasets and currently selects `davidcariboo/player-scores` for structured squad/player-value enrichment.

The selected archive is downloaded temporarily, parsed into structured facts, and then removed. Raw Kaggle archives are not persisted.

Writes:

- `kaggle_datasets`
- `squad_players`

### Wikipedia Squads

Plugin: `wikipedia_squads`

Fetches current squad rows from Wikipedia pages with attribution and diagnostics.

Writes:

- `wikipedia_squads`
- `squad_players`

### Transfermarkt Discovery

Plugin: `transfermarkt_source`

Performs robots-aware search-result discovery only. It is used for optional enrichment diagnostics, not private scraping.

Writes:

- `transfermarkt_search_results`

### Weather

Plugin: `weather`

Fetches Open-Meteo weather for the match window, covering the game rather than just kickoff.

Writes:

- `weather_observations`

### Public Analysis, Lineups, And Notes

Plugins:

- `public_analysis`
- `lineup_availability`
- `automatic_match_notes`

`public_analysis` and `lineup_availability` can use `NEWS_API_KEY` for bounded article discovery. They also rely on source reliability scoring and extraction diagnostics.

Writes include:

- `public_match_analysis`
- `lineup_availability`
- `lineup_consensus`
- `fifa_match_details`
- `automatic_match_notes`
- `match_analysis_causes`
- `match_analysis_team_adjustments`
- `extraction_diagnostics`

### Postmatch Stats

Plugin: `postmatch_stats`

Normalizes postmatch xG/stat snippets when available. If xG is missing, it derives conservative chance-quality proxies from shots, shots on target, and corners. Red-card matches are down-weighted.

Writes:

- `postmatch_stats`
- `postmatch_team_performance`

## Optional Keys

For local development, copy `.env.example` to `.env` and fill whichever keys you have:

```bash
cp .env.example .env
```

The project loads `.env` with `python-dotenv` for local development and keeps values in `os.environ`. Existing process environment variables take precedence over `.env` values.

Production does not read `/opt/worldcup-predictions/.env`. Live secrets belong in `/etc/worldcup-predictions/env`; `scripts/run-prod-compose.sh` sources that file before running `docker compose -f compose.prod.yaml`, and the production Compose file passes the supported host environment variables into the container explicitly. Cron jobs should call the wrapper so those variables are available.

Supported variables:

- `ODDS_API_KEY`
- `FOOTBALL_DATA_API_KEY`
- `KAGGLE_API_TOKEN`
- `NEWS_API_KEY`
- `BASE_URL`
- `GTM_CONTAINER_ID`

All source plugins continue to run without optional keys, but missing keys reduce available enrichment.

## Licensing

Only structured facts extracted by the workflow are stored. Raw archives and raw API responses are not committed. Optional external datasets should be used only when their license allows the intended use. If redistribution is unclear, keep the raw data out of the repository and document the requirement instead of committing the data.

## Generated Structured Outputs

Prediction runs write extracted output rows to ignored local storage. The exact set depends on configured keys, current fixtures, and source availability, but the public workflow is designed around these structured datasets:

- `data/worldcup_predictions.duckdb`
- `data/structured/predictions.parquet`
- `data/structured/optimized_tips.parquet`
- `data/structured/tournament_fixtures.parquet`
- `data/structured/tournament_results.parquet`
- `data/structured/tournament_standings.parquet`
- `data/structured/tournament_result_checks.parquet`
- `data/structured/group_state_signals.parquet`
- `data/structured/phase_context_signals.parquet`
- `data/structured/historical_results.parquet`
- `data/structured/shootouts.parquet`
- `data/structured/srf_fixtures.parquet`
- `data/structured/srf_bonus_questions.parquet`
- `data/structured/football_data_competition.parquet`
- `data/structured/football_data_standings.parquet`
- `data/structured/football_data_match_details.parquet`
- `data/structured/football_data_teams.parquet`
- `data/structured/kaggle_datasets.parquet`
- `data/structured/wikipedia_squads.parquet`
- `data/structured/transfermarkt_search_results.parquet`
- `data/structured/squad_players.parquet`
- `data/structured/squad_values.parquet`
- `data/structured/player_impact.parquet`
- `data/structured/ml_outcome_models.parquet`
- `data/structured/market_odds.parquet`
- `data/structured/market_outrights.parquet`
- `data/structured/market_trends.parquet`
- `data/structured/weather_observations.parquet`
- `data/structured/public_match_analysis.parquet`
- `data/structured/public_source_pages.parquet`
- `data/structured/public_source_claims.parquet`
- `data/structured/public_claim_consensus.parquet`
- `data/structured/public_source_reputation.parquet`
- `data/structured/public_market_observations.parquet`
- `data/structured/lineup_availability.parquet`
- `data/structured/lineup_consensus.parquet`
- `data/structured/automatic_match_notes.parquet`
- `data/structured/postmatch_stats.parquet`
- `data/structured/postmatch_team_performance.parquet`
- `data/structured/team_calibration.parquet`
- `data/structured/live_global_calibration.parquet`
- `data/structured/calibration_decisions.parquet`
- `data/structured/match_analysis_causes.parquet`
- `data/structured/match_analysis_team_adjustments.parquet`
- `data/structured/srf_expert_predictions.parquet`
- `data/structured/srf_expert_performance.parquet`
- `data/structured/prediction_debug_report.parquet`
- `data/structured/prediction_backtest.parquet`
- `data/structured/prediction_ledger.parquet`
- `data/structured/published_prediction_ledger.parquet`
- `data/structured/published_prediction_seed.parquet`
- `data/structured/model_calibration.parquet`
- `data/structured/prediction_audit.parquet`
- `data/structured/prediction_reports.parquet`
- `data/structured/prediction_snapshots.parquet`
- `data/structured/prediction_comparisons.parquet`
- `data/structured/prediction_run_summaries.parquet`
- `data/structured/prediction_exports.parquet`
- `data/structured/baseline_bundles.parquet`
- `data/structured/match_intel.parquet`
- `data/structured/postmatch_learning.parquet`
- `data/structured/postmatch_review_queue.parquet`
- `data/structured/provider_points.parquet`
- `data/structured/provider_knockout_audit.parquet`
- `data/structured/provider_bonus_tracker.parquet`
- `data/structured/plugin_run_diagnostics.parquet`
- `data/structured/prediction_signal_impacts.parquet`
- `data/structured/diagnostics_completeness_audit.parquet`
- `data/structured/source_diagnostics.parquet`
- `data/structured/extraction_diagnostics.parquet`
- `data/structured/entity_validation.parquet`
- `data/structured/entity_aliases_generated.parquet`
- `data/structured/simulation_runs.parquet`
- `data/structured/simulation_summary.parquet`
- `data/structured/source_ledger.parquet`
