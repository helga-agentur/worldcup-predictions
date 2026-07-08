# Architecture

The refactored project uses a plugin-oriented workflow inspired by Drupal's extension model, but with data-science guardrails: explicit contracts, deterministic ordering, reproducible artifacts, and a central prediction combiner.

## Documentation Map

- Architecture: this document.
- Data sources: [`data-sources.md`](data-sources.md)
- Automation: [`automation.md`](automation.md)
- Diagnostics and improvement: [`diagnostics-and-improvement.md`](diagnostics-and-improvement.md)
- Advanced commands: [`advanced-commands.md`](advanced-commands.md)
- Generated plugin catalog: [`plugins.md`](plugins.md)

## Design Principles

- The workflow emits stable events. Plugins subscribe to events and return typed artifacts, signals, predictions, or diagnostics.
- Event payloads are typed dataclasses with dict-style compatibility for plugin ergonomics.
- Plugins should not silently mutate another plugin's output. They return data; the workflow or prediction engine decides how to combine it.
- A failed source should produce a diagnostic and allow unrelated sources to continue, unless a workflow is explicitly running in fail-fast mode.
- Every prediction run should be explainable through event logs, plugin diagnostics, and comparable prediction artifacts.
- Do not add flags or options speculatively. If a new control might be useful but is not clearly part of the active workflow, ask before implementing it.
- Plugins declare their metadata: kind, datasets read/written, signals emitted, env vars, quota policy, confidence policy, and i18n support.
- Core registries own signal names, dataset contracts, and user-facing translation. Plugins should emit canonical ids, numbers, and structured facts, not localized prose.
- Registered datasets validate required fields at storage boundaries. Unknown plugin datasets should be registered before they are written so contracts stay auditable.

## Capabilities Summary

The package provides the workflow shell, plugin contracts, shared source runtime, quota-aware storage, country and generic entity identity, generated alias candidates, tournament state/result recording, automatic openfootball support, automatic historical-result/shootout fetching, SRF public fixture/bonus/result imports, football-data.org enrichment, robots-aware public score/page-analysis sources, dynamic public-source claim consensus and reputation tracking, Kaggle/Wikipedia/robots-aware Transfermarkt optional enrichment, a transparent baseline Elo/goal-profile prediction model with explicit signal policy, capped mismatch/blowout adjustment, public market-odds and outright facts, high-confidence public market observations, match-window weather, reliable public analysis, structured postmatch causes/team adjustments, lineup availability signals, automatic match notes, postmatch stats normalization from public notes, player-impact signals, sklearn ML outcome calibration with fallback, team/global live calibration, explicit model calibration reports, SRF expert consensus/performance, group-state motivation signals, SRF and 20min tip optimization, provider-neutral tournament simulation, provider-specific bonus-question adapters, provider-ready structured output persistence, published website-ledger freezing, static site generation, match-intel review rows, core plugin IO diagnostics, prediction signal-impact rows, source diagnostics, extraction diagnostics, one-file prediction exports, refactor baseline bundles, prediction debug reports, prediction snapshots/comparisons/audits, Markdown report generation, postmatch learning/review queues, virtual provider point tracking, entity validation, and SRF backtesting.

The modeling layer restores and extends the legacy forecasting features: historical World Cup backtesting with expected points and ranked probability score (RPS), an ML-weight calibration grid, Bayesian-shrinkage live calibration, an expected-goal-share mismatch trigger, an Elo-logistic penalty-shootout model, market weights raised toward the dominant public signal with a totals line shift, a market-movement signal from odds snapshot history, tournament-outright priors folded into match matrices, a feature-rich ML outcome model with goal-profile inputs, accuracy-weighted SRF expert consensus, continuous source-reliability weighting above a spam floor, and a two-command server cadence for hourly predictions plus daily simulation/entity maintenance.

## Core Events

- `workflow_started`
- `fixtures_requested`
- `raw_source_fetched`
- `pre_game_analysis_available`
- `fixture_context_requested`
- `feature_signals_requested`
- `score_matrix_created`
- `predictions_requested`
- `prediction_ready`
- `provider_optimization_requested`
- `provider_tip_ready`
- `simulation_requested`
- `simulation_completed`
- `bonus_evaluation_requested`
- `results_updated`
- `postmatch_analysis_available`
- `calibration_requested`
- `debug_report_requested`

## Current Modules

- `worldcup_predictions.core.contracts`: dataclasses exchanged between plugins.
- `worldcup_predictions.core.config`: project defaults loaded from optional `worldcup_predictions.toml`.
- `worldcup_predictions.core.constants`: shared env var names, source ids, endpoints, debug-source ids, and model policy defaults.
- `worldcup_predictions.core.football`: shared football-domain helpers such as position buckets, caps, and tournament age calculations.
- `worldcup_predictions.core.http`: configured HTTP client used by source plugins.
- `worldcup_predictions.core.metadata`: plugin, signal, dataset, quota, and env-var metadata contracts.
- `worldcup_predictions.core.payloads`: typed workflow event payloads.
- `worldcup_predictions.core.signals`: canonical model signal registry.
- `worldcup_predictions.core.datasets`: canonical structured dataset registry.
- `worldcup_predictions.core.i18n`: output translation catalog with English/German v1 support.
- `worldcup_predictions.core.extraction`: shared extraction/rejection diagnostic row helpers for source parsers.
- `worldcup_predictions.core.plugin`: deterministic plugin manager, base plugin, and core plugin input/output diagnostics.
- `worldcup_predictions.core.source_reliability`: shared public-source reliability profiles with tier, strength, and freshness reasoning.
- `worldcup_predictions.core.workflow`: orchestration for prediction runs.
- `worldcup_predictions.evaluation.baseline_bundle`: refactor-safety bundles with prediction exports, reports, plugin metadata, source ledger, and dataset fingerprints.
- `worldcup_predictions.evaluation.prediction_export`: one-file JSON exports for latest predictions, score matrices, provider tips, diagnostics, and signal impacts.
- `worldcup_predictions.evaluation.scheduled_update`: cron run manifests for timestamped prediction trends.
- `worldcup_predictions.entities`: deterministic country and generic entity registries with i18n aliases, generated alias candidates, and optional NLP span detection.
- `worldcup_predictions.model`: historical-result imports, baseline ratings, expected-goals model, exact-score matrices, `ModelSignalPolicy`, and the signal-application registry.
- `worldcup_predictions.storage`: DuckDB-backed source ledger and structured record store.
- `worldcup_predictions.tournament`: fixtures, results, openfootball imports, standings, and group-state motivation signals.
- `worldcup_predictions.providers`: public facade for provider scoring and optimization helpers.
- `worldcup_predictions.simulations`: provider-neutral tournament simulation contracts, 2026 bracket helpers, and Monte Carlo runner.
- `worldcup_predictions.plugins.providers.common`: shared score-matrix optimization helpers.
- `worldcup_predictions.plugins.providers.ch_srf`: `srf.ch` rules, plugin, and bonus-question evaluation.
- `worldcup_predictions.plugins.providers.ch_20min`: `20min.ch` rules, plugin, and bonus-question evaluation.
- `worldcup_predictions.plugins.models.baseline_model`: prediction plugin that emits provider-neutral baseline predictions.
- `worldcup_predictions.plugins.sources.fixtures.openfootball_source`: automatic openfootball/worldcup fixture/result source plugin.
- `worldcup_predictions.plugins.sources.history.historical_results_source`: automatic martj42 historical international result/shootout source plugin.
- `worldcup_predictions.plugins.sources.fixtures.football_data`: football-data.org fixture/result/team/squad source plugin.
- `worldcup_predictions.plugins.sources.markets.market_odds`: market-odds aggregation plugin using The Odds API, source-ledger checks, and structured extracted facts only.
- `worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources`: bounded robots-aware public-page discovery, claim extraction, dynamic domain reputation, and multi-domain result-claim consensus.
- `worldcup_predictions.plugins.signals.market_trend`: derives market movement (totals-line drift, cross-snapshot disagreement, favorite move) from append-only `market_odds` and high-confidence `public_market_observations` history and emits a small capped trend signal.
- `worldcup_predictions.plugins.signals.phase_context`: phase-gated knockout and final-group-match context signal plugin.
- `worldcup_predictions.plugins.sources.enrichment.weather`: Open-Meteo match-window weather aggregation and total-goal signals.
- `worldcup_predictions.plugins.sources.enrichment.public_analysis`: reliable-source pregame/postgame article extraction and tactical tempo signals.
- `worldcup_predictions.plugins.sources.enrichment.lineup_availability`: automatic injury, suspension, rotation, and availability signals from reliable public sources.
- `worldcup_predictions.plugins.sources.enrichment.postmatch_stats`: postmatch xG/stat normalization with shots/on-target/corners fallback and red-card down-weighting.
- `worldcup_predictions.plugins.signals.player_impact`: squad market-value and composition signal plugin.
- `worldcup_predictions.plugins.signals.ml_outcome`: deterministic historical outcome-calibration signal plugin.
- `worldcup_predictions.plugins.signals.live_calibration`: conservative team-level calibration from finished tournament matches.
- `worldcup_predictions.plugins.sources.enrichment.srf_experts`: SRF public expert pick extraction and consensus signals.
- `worldcup_predictions.plugins.diagnostics.match_intel`: prematch review-priority output plugin.
- `worldcup_predictions.plugins.diagnostics.debug_report`: per-fixture prediction debug reports.
- `worldcup_predictions.plugins.source_runtime`: shared source-plugin storage, tournament-state, ledger, HTTP, and artifact plumbing.
- `worldcup_predictions.plugins.workflow.tournament_state`: workflow plugin that loads tournament state, records results, and emits group-state signals.
- `worldcup_predictions.plugins.workflow.structured_output`: persists extracted prediction facts as structured records.
- `worldcup_predictions.evaluation`: backtests, model calibration, snapshots, comparisons, provider points, bonus tracking, match intel, and postmatch learning helpers.
- `worldcup_predictions.site`: builds the public static website from the frozen published prediction ledger.
- `worldcup_predictions.cli`: runnable command line interface.
- `worldcup_predictions.documentation`: generated Markdown documentation from plugin metadata.

Plugin packages are grouped by workflow role:

- `plugins.sources`: external/API/public-page imports and structured enrichment.
- `plugins.signals`: derivations from stored facts into model signals.
- `plugins.models`: provider-neutral prediction models.
- `plugins.providers`: provider-specific tip optimizers and rules.
- `plugins.workflow`: orchestration, state, persistence, and source diagnostics.
- `plugins.diagnostics`: debug and review outputs.

## Plugin Metadata

Each plugin declares a `PluginMetadata` object. The plugin manager exposes this metadata through `worldcup-predictions plugins` and validates plugin outputs against the signal and dataset registries.

Regenerate the Markdown plugin catalog with:

```bash
worldcup-predictions docs-plugins
```

Server cron jobs should use:

```bash
worldcup-predictions scheduled-update
worldcup-predictions site-build
worldcup-predictions export-predictions
worldcup-predictions baseline-bundle before-refactor
```

`scheduled-update` runs the prediction workflow for every open fixture with defined opponents, stores a timestamped prediction snapshot, refreshes backtest/audit/postmatch/provider-point/report artifacts, writes the public published-prediction ledger, applies pending one-shot data and automation hooks, builds the static website, exports the source ledger, and writes a `prediction_run_summaries` manifest so hourly probability and score-matrix movement can be inspected later. Provider points in the scheduled path are virtual points from optimized recommendations, not personal submitted tips.

`site-build` regenerates `public/current/` from `published_prediction_ledger`. Future rows update hourly; locked/final rows preserve the prediction values that were published before kickoff while final score fields are added after the match. The generated website is server-rendered HTML plus JSON and hashed CSS assets, so it can be served without an application backend.

`export-predictions` writes a single JSON file under `data/exports/` by default. It is meant for external comparison and refactor review: every match row includes neutral predictions, score matrices, provider-optimized tips, debug rows, signal impacts, source diagnostics, extraction diagnostics, and recent run summaries.

`baseline-bundle` creates a reproducibility bundle under `data/baselines/`. A bundle contains a fresh workflow run, frozen prediction snapshot, one-file prediction export, standard Markdown reports, generated plugin catalog, dataset content fingerprints, source ledger export, and metadata. Use it before large refactors so later code can be compared at the prediction, diagnostic, plugin, and dataset levels even when internal workflow steps have been consolidated.

Plugin kinds:

- `source`: talks to APIs or public pages and writes extracted facts.
- `signal`: derives model inputs from stored facts.
- `model`: emits provider-neutral predictions.
- `provider_optimizer`: converts neutral predictions into provider-specific tips.
- `simulator`: produces tournament distributions.
- `evaluator`: scores predictions against known outcomes.
- `output`: persists or renders workflow output.
- `workflow`: owns orchestration/state transitions.
## Tournament State

Tournament state is the first model input layer. It owns canonical fixture identity, consensus-confirmed final scores, open/closed fixture state, group standings, and source-level result checks. The core contracts are:

- `FixtureRecord`: one scheduled or final fixture with display names and optional FIFA country codes.
- `ResultRecord`: one source-derived full-time score observation.
- `TournamentState`: reconciled fixtures, consensus-confirmed results, group standings, and result checks.

Fixture identity uses FIFA country codes for known teams and canonical winner/loser slot codes such as `W75` for unresolved knockout participants. Source-specific labels like SRF's German placeholder text are normalized before tournament state reaches the model or signal plugins; website rendering translates canonical slot codes back into human-readable round labels such as `Sieger Halbfinal 2`.

The workflow fetches public fixture/result data through source plugins such as `srf_public`, `openfootball_source`, `football_data`, and `public_score_sources`. Public CLI workflows are automated-only; personal/manual score entry and ad-hoc local imports are not part of the runtime workflow.

Raw result observations stay in `tournament_results`. A result enters `TournamentState.results` only when the exact score is confirmed by at least three independent sources, or by at least two high-authority sources (`srf_public`, `fifa_match_centre`, `football_data_org`, or `espn_scoreboard`). Website publishing, standings, live calibration, provider points, and model learning consume that confirmed state; lower-consensus rows remain internal diagnostics in `tournament_result_checks`.

The `results_updated` event follows the same boundary: it is emitted only when the consensus-confirmed result set changes. New raw source observations that have not yet reached the confirmation threshold are written for diagnostics but do not trigger live-calibration decision logs.

Group-state motivation is derived from tournament state and emitted as conservative `Signal` rows. These signals describe must-win pressure, draw pressure, rotation risk, elimination risk, and current points/rank context. They are model inputs, not provider-specific rules.

## Prediction And Provider Optimization

The core model predicts football, not a single competition's scoring system. A `Prediction` is provider-neutral and contains:

- fixture identity
- continuous expected goals
- most likely exact score
- home/draw/away probabilities
- exact-score probability matrix
- knockout advancement probabilities when the fixture is a knockout match
- confidence and model diagnostics

The first implemented model is `baseline_model`. It reads open fixtures from `TournamentState`, imports historical international results from structured storage, folds in already-recorded tournament results, and computes:

- Elo-style team ratings with tournament and goal-difference weighting.
- Recency-weighted attack and defense goal profiles.
- Expected goals for each team.
- A Dixon-Coles adjusted Poisson exact-score matrix with light overdispersion.
- Home/draw/away probabilities and most-likely exact score.

This model is deliberately transparent and conservative. Market, weather, lineup, news, expert, and calibration layers should be separate plugins that produce typed signals rather than being folded directly into source-specific model code. Signal effects are applied by `SignalApplierRegistry` using `ModelSignalPolicy`, which keeps expected-goal and score-matrix adjustments auditable and replaceable.

The first migrated input plugin is `market_odds`. It fetches The Odds API `h2h`, `totals`, and `spreads` markets when `ODDS_API_KEY` is configured and the source ledger says the request is safe to spend. For near-term fixtures it also uses event-level odds endpoints for `draw_no_bet`, `btts`, `team_totals`, `alternate_totals`, and `alternate_spreads`, capped to the next few fixtures to preserve quota. It stores extracted market facts only and does not persist raw responses. Its aggregate signals are applied through capped blends:

- `market_hda_probabilities`: reweights the exact-score matrix toward no-vig home/draw/away probabilities.
- `market_total_goals`: nudges expected total goals toward the public totals line, shifted by the over/under lean.
- `market_goal_diff`: nudges expected goal difference toward the public spread line.

Market signals are weighted as the dominant public input but capped below a full overwrite; these weights have no historical odds to backtest and are validated forward on the live tournament. The `market_trend` plugin adds a small capped total-goals factor from market movement over the stored snapshot history.

The `phase_context` plugin is deliberately phase-gated. It only affects knockout fixtures and final group-stage matches: knockout rows nudge 90-minute tempo down, draw risk up, and team expected goals for rest/fatigue asymmetry; final group rows add a small score-shape adjustment for draw-path, must-win, or rotation-risk dynamics. Knockout prediction metadata also exposes advancement probabilities with 90-minute win, extra-time proxy, and penalty proxy components so provider optimizers can separate exact-score and advancement decisions. Earlier group-stage fixtures are deliberately untouched.

Additional migrated source layers follow the same pattern:

- `openfootball_source` fetches public openfootball/worldcup fixture/result files when the source ledger says they are stale. This makes frequent server cron runs safe without raw caching.
- `historical_results_source` fetches martj42 `results.csv` and `shootouts.csv` through the source ledger and stores normalized `historical_results` / `shootouts` facts. This keeps a fresh clone useful without committing raw source caches.
- `fifa_match_centre` fetches FIFA's public World Cup 2026 calendar API and stores official fixture/result observations plus `fifa_match_details` rows for stage, group, match number, venue, officials, attendance, possession when present, and formations/tactics. It is high-authority result evidence but does not currently provide player-level starting XIs.
- `football_data` fetches competition fixtures/results and team/squad metadata through football-data.org when `FOOTBALL_DATA_API_KEY` is configured. It writes structured tournament and squad rows; as a high-authority result source it can confirm scores only through the central source-consensus policy.
- `public_score_sources` checks allowed public result and page-analysis pages from FIFA, ESPN, FotMob, SofaScore, and 20min through robots.txt-aware requests. ESPN's public scoreboard currently provides parseable finished-score rows; other public pages contribute diagnostics unless their allowed paths expose canonical final-score or supported analysis/stat text. Private or robots-disallowed APIs are not used.
- `dynamic_public_sources` starts from known public seed pages plus a 100+ source trusted registry, follows a bounded set of same-domain tournament links, and stores page metadata/fingerprints plus extracted fixture, result, and market claims. The trusted registry rotates in per-run batches so broad discovery does not turn one cron run into a 100-page crawl. It uses Scrapy selectors when available for HTML parsing, while all HTTP requests still go through `SourceRuntime` for source-ledger freshness, cache validators, and backoff. Dynamic result claims are not high-authority; they become `tournament_results` observations only after multi-domain weighted support, then still need the central result-consensus policy before tournament state changes.
- `weather` fetches Open-Meteo hourly forecasts for the full match window, from 15 minutes before kickoff through 135 minutes after kickoff, when fixture venue coordinates are available.
- `public_analysis` uses bounded NewsAPI queries when `NEWS_API_KEY` is configured, stores reliable article-derived pregame/postgame notes, scores source reliability through the shared core reliability profiles, extracts parseable xG/shot/card/weather snippets, writes structured postmatch cause/team-adjustment rows, and emits small tempo signals. Knockout fixtures add stage, suspension, extra-time, and penalty terms to the query so KO-specific articles are more likely to surface.
- `lineup_availability` uses reliable public source discovery for injuries, suspensions, fitness, and rotation, then writes a `lineup_consensus` aggregate by fixture and side. FIFA official formations and football-data lineups are folded in as structured lineup context where available. It replaces manual lineup files; unavailable or weak evidence simply produces diagnostics.
- `postmatch_stats` normalizes xG when available and otherwise creates a conservative chance-quality proxy from shots, shots on target, and corners. It consumes stat snippets extracted from public postgame analysis. Red-card matches are down-weighted.
- `player_impact` consumes normalized squad-player rows from automated public sources to emit capped team-specific xG and total-goal signals.
- `ml_outcome` trains a dependency-light historical Elo-delta outcome calibration and emits capped H/D/A probabilities. It is deliberately replaceable behind the same `ml_hda_probabilities` signal.
- `live_calibration` turns finished matches, postmatch performance rows, postmatch team-adjustment rows, and frozen prediction audits into capped team and global calibration signals. Global signals can adjust tournament scoring pace, draw tendency, score-tail behavior, and favorite/underdog bias without mutating model defaults.
- `srf_experts` refetches SRF public expert pages for open fixtures and emits a small expert-consensus H/D/A signal.

All source failures become diagnostics. They should not block unrelated plugins.

Source plugins should use shared plumbing from `worldcup_predictions.plugins.source_runtime` and shared source constants from `worldcup_predictions.core.constants`. Plugin folders should contain extraction/classification logic and source-specific rules, not repeated storage, HTTP, env-var, or source-ledger boilerplate.

The source ledger is also the HTTP cache-validator ledger. Every request is keyed by source, endpoint, purpose, normalized params, and optional fixture key. Successful source-runtime fetches store response headers in ledger metadata, with cookie headers redacted, and keep `ETag` / `Last-Modified` values as `cache_validators`. Later eligible fetches send `If-None-Match` and `If-Modified-Since` for the same request key. A `304 Not Modified` response is recorded as `not_modified` and means no new source facts should be written for that request. Raw response bodies are still not stored.

Quota-limited plugins can also declare a shared `quota_scope` on individual `SourceRequest` objects. Exact request keys still handle per-resource freshness; the shared scope handles provider-wide limits, quota floors, and source blocks. For example, after one NewsAPI query returns a 429, sibling fixture queries in the same NewsAPI scope are skipped until the recorded next-safe time instead of spending more calls. Ordinary `fresh_enough` skips for one exact request do not block sibling requests.

Provider-specific tips are emitted separately as `OptimizedTip` records. Optimizer plugins subscribe to `provider_optimization_requested`, read the neutral prediction, apply one ruleset, and return a new typed artifact. They must not change the prediction probabilities or score matrix.

An optimized tip is not always an exact score. Providers may ask for different selections:

- `exact_score`: for competitions such as SRF where players enter a concrete scoreline.
- `outcome`: for group-stage competitions that only ask home/draw/away.
- `advancement`: for knockout competitions that ask which team advances.

This separation lets the same model feed multiple competitions:

- `srf.ch`: implemented from the 2026 group-stage and knockout-stage scoring rules.
- `20min.ch`: implemented from `https://tippspiel.20min.ch/details`; group-stage matches award 5 points for the correct outcome, while knockout matches award 5/10/20/25/30/40 points depending on the round for the correct advancing team.
- Future providers: add one provider folder with its optimizer plugin and rules, then subscribe to the same optimization event.

Structured output mirrors this split:

- `predictions.parquet`: provider-neutral model output.
- `optimized_tips.parquet`: provider/ruleset-specific exact-score choices and expected points.
- `prediction_debug_report.parquet`: per-fixture source coverage, model adjustments, and provider tips.
- `plugin_run_diagnostics.parquet`: core-recorded payload summaries, plugin output counts, timings, diagnostics, and affected fixtures for every plugin event.
- `prediction_signal_impacts.parquet`: per-fixture signal rows joined to the resulting prediction and model adjustment metadata.
- `result_update_audit.parquet`: source-level audit rows for newly discovered or changed final scores.
- `calibration_decisions.parquet`: report-only calibration value/recommendation changes with previous value, new value, and reason.
- `extraction_diagnostics.parquet`: accepted/rejected source extraction rows with source, extractor, fixture, phase, reason, severity, URL/title, and metadata.
- `prediction_exports.parquet`: manifests for one-file JSON prediction exports.
- `baseline_bundles.parquet`: manifests for refactor-safety baseline bundles.

If a score matrix is missing, optimizers may fall back to the neutral most-likely score, but they must emit a diagnostic because expected-points optimization is not meaningful without exact-score probabilities.

Source extraction should explain both usable data and rejected candidates. Public analysis, lineup availability, automatic notes, and postmatch-stat extraction write `extraction_diagnostics` rows for reasons such as article after kickoff, fixture not mentioned, no supported signal/stat, missing team side, low source reliability, missing fixture key, or insufficient consensus. These rows make failed or zero-row layers inspectable without requiring raw response caches.

## Tournament Simulation And Bonus Questions

Tournament simulation is provider-neutral. It consumes fixtures, already-known scores, tournament-outright team strengths, and exact-score matrices from the prediction model. Known scores are fixed; only unresolved fixtures are sampled. The same outright prior that adjusts published match matrices also adjusts generated hypothetical knockout matrices, so champion probabilities and the bracket forecast come from one sampled path instead of a separate champion/market blend. This lets the same simulator run before the first match or midway through the tournament without changing provider code.

The half-hourly scheduled update refreshes the current-state simulation when the unresolved fixture fingerprint changes, when the simulation logic version changes, or when a committed one-shot automation hook explicitly requests a simulation refresh. Applied automation-hook state is stored in the `automation_hooks` structured dataset under the normal runtime `data/` root.

The neutral simulation output includes:

- champion distribution
- stage-reached distribution for every team
- team-goals distribution for every team
- group-rank and group-qualification distributions
- 0:0 match-count distribution
- top-scorer goal-count distribution
- a sample run for diagnostics

Provider folders translate that output into competition-specific bonus views:

- `plugins.providers.ch_srf.bonus`: SRF bonus answers such as champion, Switzerland stage, Switzerland goals, top scorer goals, and number of 0:0 matches.
- `plugins.providers.ch_20min.bonus`: 20min-ready distributions such as champion, team progress, group winners, qualification, top scorer goals, and 0:0 matches.

The simulator must not apply SRF or 20min scoring rules directly. Provider rules remain on top of the generic tournament distributions.

## Entity Resolution

Participating countries use FIFA three-letter country codes as canonical identifiers. The bundled country registry lives in `src/worldcup_predictions/resources/countries.json` and stores:

- canonical FIFA code
- localized names for `en` and `de`
- localized aliases and nicknames
- source-specific aliases where needed
- ambiguous aliases that must not be auto-assigned

Resolver policy:

- Resolve exact FIFA codes first.
- Resolve deterministic aliases across all supported locales.
- Prefer caller-provided locale/source when available.
- Return an explicit ambiguous result instead of guessing.
- Use spaCy or other NLP tools only to detect candidate text spans in messy prose; pass those spans into the registry for canonical identity.

Generic football concepts such as player positions, injuries, suspensions, red cards, xG, shot stats, weather context, and provider labels live in `src/worldcup_predictions/resources/entity_aliases.json`. The same policy applies: deterministic aliases are the source of truth; spaCy or any other NLP tool may suggest text spans but must pass them back through the registry before data is attached to an entity.

User-facing text is handled through `worldcup_predictions.core.i18n`. Plugins should not branch on German/English. They should emit canonical ids and values; output adapters translate labels and messages at the boundary. Adding another language should mean adding a catalog/config entry, not editing plugin code.

## Storage Model

The project stores extracted information, not raw response caches. The workflow should decide whether a source should be called before making the request.

Default storage:

- `data/worldcup_predictions.duckdb`: rebuildable local analytical database.
- `data/structured/*.parquet`: canonical structured exports for model inputs, predictions, diagnostics, and source ledgers.
- `public/current/`: generated static website output. This is ignored runtime output, not source.

Storage contracts:

- Raw API and scraped response bodies are not persisted by default.
- Structured records are append-only. Latest-read paths collapse records by stable `record_key`, so an hourly run that receives no new facts from a source naturally falls back to the last available structured facts.
- Fetcher plugins must create a `SourceRequest` and call `storage.should_fetch(...)` before spending quota.
- Fetcher plugins must call `storage.record_fetch(...)` after a source attempt, including rate-limit headers or next-safe-fetch times when available.
- Plugins write normalized facts/signals through `storage.write_records(...)`.
- Core automatically writes detailed plugin outputs to `plugin_event_outputs` for trend-ready history: signals, artifacts, predictions, optimized tips, and diagnostics are timestamped per run without plugin-specific code.
- `storage.write_records(...)` validates required fields for registered datasets before persistence.
- Repository modules should type against `StructuredStorage` instead of a concrete backend.
- CSV remains acceptable only for tiny human-editable files.
- The DuckDB file is a local query/cache database and should be rebuildable from structured records and source plugins.

Whenever automated source refresh adds or changes final-score rows, the workflow emits `results_updated`. Observer plugins use that event for monitoring and report-only recalibration: `result_monitoring` writes a result audit row, while `live_calibration` records which live-calibration values or recommendations changed and why. These rows are append-only diagnostics; runtime defaults still change only through deliberate code/config updates.

Backtesting writes `prediction_backtest` rows. The first evaluator uses the current transparent baseline and SRF scoring rules against finished fixtures so refactor steps can be compared with concrete points, exact-score hits, and outcome hits.

Model calibration writes `model_calibration` rows. Calibration evaluates transparent baseline parameter candidates on historical World Cup samples and reports the selected candidate, but it does not silently rewrite runtime configuration. Promotion of a calibration result into defaults should be a deliberate code/config change.

Prediction snapshots write full provider-neutral predictions plus provider tips into `prediction_snapshots`, while snapshot comparisons write probability, score-matrix, and most-likely-score deltas into `prediction_comparisons`. These artifacts are the main safety net for future refactors.

Trend inputs are collected before they are necessarily used. Append-only source rows, `source_ledger`, `plugin_run_diagnostics`, `plugin_event_outputs`, prediction snapshots, and provider ledgers are enough to analyze movement over time. New trend consumers should be explicit signal plugins; they should not silently infer from overwritten state.

Provider point tracking is virtual at the storage level: `provider_points` scores optimized recommendations for supported providers. `provider_bonus_tracker` includes a virtual match-points summary for SRF and 20min, showing how many points the workflow would currently have if its recommendations had been entered one-to-one.

Postmatch learning joins backtest misses with chance-quality rows, structured analysis causes, team-adjustment rows, and available result metadata such as half-time scores or goal text. It writes both `postmatch_learning` and `postmatch_review_queue`. This keeps improvement analysis separate from the model so it can inform later feature work without silently changing current predictions.

Generated alias candidates live in `entity_aliases_generated`. They can be rebuilt from structured rows such as squads, fixtures, venues, markets, and publishers. Ambiguous aliases stay explicitly marked and must not be used to attach data to a single entity automatically. The canonical country registry remains the source of truth for country/FIFA-code identity.

The source ledger stores request intent and quota facts:

- source, endpoint, purpose, parameter hash, fixture key, optional quota scope
- workflow run id
- quota cost, remaining quota, status, message
- fetched time, rate-limit reset time, next safe fetch time

This is intentionally different from a response cache: it helps the workflow avoid unnecessary calls without treating raw responses as canonical data. Scheduled-update manifests include a compact source-ledger summary with per-source status counts, failures, and zero-row successes so hourly reliability trends can be inspected without parsing terminal output.

## Project Defaults

Stable workflow defaults live in `worldcup_predictions.core.config`. A repository can override them with `worldcup_predictions.toml`:

```toml
[project]
default_locale = "de"
supported_locales = ["en", "de"]
[source_defaults]
timeout_seconds = 20
default_refresh_minutes = 30
weather_refresh_minutes = 60
expert_refresh_minutes = 15
odds_quota_remaining_floor = 2
news_quota_remaining_floor = 5
```

Runtime state, remaining quota, and next-safe-fetch timestamps still belong in the source ledger.

## Porting Strategy

1. Implement one data source at a time as a dedicated plugin.
2. Each source plugin writes only structured extracted facts and source-ledger entries.
3. Implement the central score model after input plugins, storage, and debug reports are stable.
4. Compare each refactor step against explicit prediction and data-quality fixtures.

## Docker

The Docker image installs the root package and dependencies. Generated local data stays outside the image build context.

```bash
docker compose run --rm predictions worldcup-predictions plugins
docker compose run --rm predictions worldcup-predictions predict --limit 4
```
