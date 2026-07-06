# Plugin Catalog

Generated from plugin metadata declared in the codebase.

| Plugin | Kind | Priority | Events | Writes | Emits | Quota-limited |
| --- | --- | ---: | --- | --- | --- | --- |
| `tournament_state` | workflow | 100 | `workflow_started`, `fixtures_requested`, `feature_signals_requested` | `tournament_standings`, `tournament_result_checks`, `group_state_signals` | `group_motivation`, `group_draw_pressure`, `group_rotation_risk`, `group_elimination_risk` | no |
| `openfootball_source` | source | 115 | `fixtures_requested` | `tournament_fixtures`, `tournament_results` | - | no |
| `srf_public` | source | 115 | `fixtures_requested` | `srf_fixtures`, `srf_bonus_questions`, `tournament_fixtures`, `tournament_results` | - | no |
| `fifa_match_centre` | source | 118 | `fixtures_requested` | `tournament_fixtures`, `tournament_results`, `fifa_match_details` | - | no |
| `football_data` | source | 120 | `fixtures_requested`, `feature_signals_requested` | `tournament_fixtures`, `tournament_results`, `football_data_competition`, `football_data_standings`, `football_data_teams`, `football_data_match_details`, `squad_players` | - | yes |
| `kaggle_source` | source | 125 | `feature_signals_requested` | `kaggle_datasets`, `squad_players` | - | yes |
| `wikipedia_squads` | source | 126 | `feature_signals_requested` | `wikipedia_squads`, `squad_players` | - | no |
| `transfermarkt_source` | source | 127 | `feature_signals_requested` | `transfermarkt_search_results` | - | no |
| `public_score_sources` | source | 130 | `fixtures_requested` | `tournament_results`, `public_match_analysis`, `extraction_diagnostics` | - | no |
| `dynamic_public_sources` | source | 132 | `fixtures_requested` | `tournament_results`, `public_source_pages`, `public_source_claims`, `public_claim_consensus`, `public_source_reputation`, `public_market_observations`, `public_match_analysis`, `extraction_diagnostics` | - | no |
| `historical_results_source` | source | 135 | `feature_signals_requested` | `historical_results`, `shootouts` | - | no |
| `result_monitoring` | output | 145 | `results_updated` | `result_update_audit` | - | no |
| `market_odds` | source | 250 | `feature_signals_requested` | `market_odds`, `market_outrights` | `market_hda_probabilities`, `market_total_goals`, `market_goal_diff` | yes |
| `market_trend` | signal | 255 | `feature_signals_requested` | `market_trends` | `total_goals_factor` | no |
| `phase_context` | signal | 258 | `feature_signals_requested` | `phase_context_signals` | `total_goals_factor`, `live_draw_adjustment`, `team_expected_goals_factor` | no |
| `weather` | source | 260 | `feature_signals_requested` | `weather_observations` | `total_goals_factor` | no |
| `lineup_availability` | source | 265 | `feature_signals_requested` | `lineup_availability`, `lineup_consensus`, `extraction_diagnostics` | `team_expected_goals_factor` | yes |
| `public_analysis` | source | 270 | `feature_signals_requested` | `public_match_analysis`, `match_analysis_causes`, `match_analysis_team_adjustments`, `extraction_diagnostics` | `total_goals_factor` | yes |
| `postmatch_stats` | signal | 300 | `feature_signals_requested` | `postmatch_stats`, `postmatch_team_performance`, `extraction_diagnostics` | - | no |
| `automatic_match_notes` | signal | 310 | `feature_signals_requested` | `automatic_match_notes`, `extraction_diagnostics` | `total_goals_factor`, `team_expected_goals_factor` | no |
| `player_impact` | signal | 310 | `feature_signals_requested` | `squad_values`, `player_impact` | `team_expected_goals_factor`, `total_goals_factor` | no |
| `ml_outcome` | signal | 320 | `feature_signals_requested` | `ml_outcome_models` | `ml_hda_probabilities` | no |
| `live_calibration` | signal | 330 | `results_updated`, `feature_signals_requested` | `team_calibration`, `live_global_calibration`, `calibration_decisions` | `team_expected_goals_factor`, `total_goals_factor`, `live_draw_adjustment`, `live_score_tail_factor`, `live_favorite_outcome_factor` | no |
| `srf_experts` | source | 340 | `feature_signals_requested` | `srf_expert_predictions`, `srf_expert_performance`, `extraction_diagnostics` | `expert_hda_probabilities` | no |
| `baseline_model` | model | 400 | `predictions_requested` | - | - | no |
| `provider_optimizer_srf_ch` | provider_optimizer | 700 | `provider_optimization_requested` | - | - | no |
| `provider_optimizer_20min_ch` | provider_optimizer | 710 | `provider_optimization_requested` | - | - | no |
| `structured_output` | output | 900 | `prediction_ready`, `provider_tip_ready` | `predictions`, `optimized_tips` | - | no |
| `match_intel` | output | 930 | `debug_report_requested` | `match_intel` | - | no |
| `source_diagnostics` | output | 940 | `debug_report_requested` | `source_diagnostics` | - | no |
| `debug_report` | output | 950 | `debug_report_requested` | `prediction_debug_report`, `prediction_signal_impacts` | - | no |

## Details

### `tournament_state`

Load tournament state, apply result consensus, derive standings, and emit group-state signals.

- Version: `0.1.0`
- Kind: `workflow`
- Priority: `100`
- Events: `workflow_started`, `fixtures_requested`, `feature_signals_requested`
- Reads: `tournament_results`
- Writes: `tournament_standings`, `tournament_result_checks`, `group_state_signals`
- Signals: `group_motivation`, `group_draw_pressure`, `group_rotation_risk`, `group_elimination_risk`
- Locales: `en`, `de`
- Confidence policy: Group-state signals are deterministic from fixtures and consensus-confirmed results.

### `openfootball_source`

Fetch openfootball/worldcup fixture and result data into tournament state.

- Version: `0.1.0`
- Kind: `source`
- Priority: `115`
- Events: `fixtures_requested`
- Reads: -
- Writes: `tournament_fixtures`, `tournament_results`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Public GitHub raw files are refreshed through the source ledger so hourly cron runs do not refetch unchanged inputs.
- Confidence policy: openfootball fixtures/results are useful public fallbacks; result rows enter tournament state only after the central source-consensus policy confirms them.

### `srf_public`

Fetch SRF public round pages and extract fixtures, final results, and bonus-question metadata.

- Version: `0.1.0`
- Kind: `source`
- Priority: `115`
- Events: `fixtures_requested`
- Reads: `srf_fixtures`, `srf_bonus_questions`
- Writes: `srf_fixtures`, `srf_bonus_questions`, `tournament_fixtures`, `tournament_results`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Public SRF round pages refresh with a short ledger interval during the tournament.
- Confidence policy: SRF fixture rows are canonicalized through the country registry before entering tournament state.

### `fifa_match_centre`

Fetch FIFA public match-centre calendar data for official fixtures, scores, formations, officials, venue, and attendance.

- Version: `0.1.0`
- Kind: `source`
- Priority: `118`
- Events: `fixtures_requested`
- Reads: -
- Writes: `tournament_fixtures`, `tournament_results`, `fifa_match_details`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - FIFA's public calendar endpoint is refreshed through the source ledger; raw API responses are not cached.
- Confidence policy: FIFA match-centre rows are high-authority fixture/result evidence. They enrich metadata and result consensus but do not provide player-level starting XI data.

### `football_data`

Fetch football-data.org World Cup competition, standings, fixtures, results, teams, squads, and match details.

- Version: `0.1.0`
- Kind: `source`
- Priority: `120`
- Events: `fixtures_requested`, `feature_signals_requested`
- Reads: `tournament_fixtures`, `tournament_results`, `football_data_competition`, `football_data_standings`, `football_data_teams`, `football_data_match_details`, `squad_players`
- Writes: `tournament_fixtures`, `tournament_results`, `football_data_competition`, `football_data_standings`, `football_data_teams`, `football_data_match_details`, `squad_players`
- Signals: -
- Locales: `en`, `de`
- Environment:
  - `FOOTBALL_DATA_API_KEY` (optional): football-data.org API token.
- Quota: limited and ledger-required - Competition endpoints are skipped while fresh or when the stored quota floor has been reached.
- Confidence policy: football-data.org rows enrich canonical tournament state; as a high-authority result source they can confirm scores only through the central source-consensus policy.

### `kaggle_source`

Optionally query Kaggle and extract selected squad-value facts without storing raw archives.

- Version: `0.1.0`
- Kind: `source`
- Priority: `125`
- Events: `feature_signals_requested`
- Reads: `kaggle_datasets`, `squad_players`
- Writes: `kaggle_datasets`, `squad_players`
- Signals: -
- Locales: `en`, `de`
- Environment:
  - `KAGGLE_API_TOKEN` (optional): Kaggle API token with dataset read access.
- Quota: limited and ledger-required - Dataset searches refresh weekly; selected downloads refresh daily and store extracted facts only.
- Confidence policy: Kaggle facts are supplemental and must be joined through canonical country/player rows before model use.

### `wikipedia_squads`

Extract squad facts from Wikipedia national-team pages with attribution and diagnostics.

- Version: `0.1.0`
- Kind: `source`
- Priority: `126`
- Events: `feature_signals_requested`
- Reads: `wikipedia_squads`
- Writes: `wikipedia_squads`, `squad_players`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Wikipedia API pages refresh daily and store only parsed squad facts.
- Confidence policy: Wikipedia squad rows are supplemental and merged by canonical country code.

### `transfermarkt_source`

Robots-aware Transfermarkt team search extraction for optional player/squad enrichment discovery.

- Version: `0.1.0`
- Kind: `source`
- Priority: `127`
- Events: `feature_signals_requested`
- Reads: `football_data_teams`, `transfermarkt_search_results`
- Writes: `transfermarkt_search_results`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Search pages refresh weekly and are skipped if robots.txt disallows the request.
- Confidence policy: Search results are discovery hints only; model features still require structured squad/player rows.

### `public_score_sources`

Fetch robots-aware FIFA, ESPN, FotMob, SofaScore, and 20min public pages for result verification and page-level analysis.

- Version: `0.1.0`
- Kind: `source`
- Priority: `130`
- Events: `fixtures_requested`
- Reads: -
- Writes: `tournament_results`, `public_match_analysis`, `extraction_diagnostics`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Public pages are robots-gated and source-ledgered; private/disallowed APIs are not fetched.
- Confidence policy: Only finished score rows with canonical team-code matches are stored; page analysis rows require recognized pre/postgame signals or stat snippets.

### `dynamic_public_sources`

Discover robots-aware public pages, extract fixture/result/market claims, and score domain reputation over time.

- Version: `0.1.0`
- Kind: `source`
- Priority: `132`
- Events: `fixtures_requested`
- Reads: `public_source_claims`, `public_source_reputation`, `tournament_results`
- Writes: `tournament_results`, `public_source_pages`, `public_source_claims`, `public_claim_consensus`, `public_source_reputation`, `public_market_observations`, `public_match_analysis`, `extraction_diagnostics`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Every page fetch is robots-gated, source-ledgered, cache-validator aware, and rate-limit backed off per domain.
- Confidence policy: Dynamic public results require multi-domain weighted consensus before becoming raw result observations; market rows need a confidence floor before trend use.

### `historical_results_source`

Fetch martj42 international_results CSVs and write normalized historical results and shootouts.

- Version: `0.1.0`
- Kind: `source`
- Priority: `135`
- Events: `feature_signals_requested`
- Reads: -
- Writes: `historical_results`, `shootouts`
- Signals: -
- Locales: `en`, `de`
- Quota: not limited and ledger-required - GitHub raw CSVs are not quota-priced but are still ledgered and refreshed at most daily.

### `result_monitoring`

Persist result-update audit rows whenever final-score rows are newly discovered or changed.

- Version: `0.1.0`
- Kind: `output`
- Priority: `145`
- Events: `results_updated`
- Reads: -
- Writes: `result_update_audit`
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Monitoring rows do not affect predictions; they explain when downstream audits and calibration should react.

### `market_odds`

Fetch public Odds API markets and emit provider-neutral market signals.

- Version: `0.1.0`
- Kind: `source`
- Priority: `250`
- Events: `feature_signals_requested`
- Reads: `market_odds`, `market_outrights`
- Writes: `market_odds`, `market_outrights`
- Signals: `market_hda_probabilities`, `market_total_goals`, `market_goal_diff`
- Locales: `en`, `de`
- Environment:
  - `ODDS_API_KEY` (optional): The Odds API key for public market prices.
- Quota: limited and ledger-required - Fetches are skipped while fresh enough or when quota floor is reached.
- Confidence policy: Confidence rises with bookmaker count and is capped before blending into the score matrix.

### `market_trend`

Derive market movement (totals-line drift, disagreement, favorite move) from the odds snapshot history.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `255`
- Events: `feature_signals_requested`
- Reads: `market_odds`, `public_market_observations`
- Writes: `market_trends`
- Signals: `total_goals_factor`
- Locales: `en`, `de`
- Confidence policy: Trends need multiple snapshots; confidence rises with snapshot count and falls with cross-snapshot disagreement.

### `phase_context`

Emit knockout-only and final-group-match context signals for tempo, draw risk, rest, and fatigue.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `258`
- Events: `feature_signals_requested`
- Reads: `tournament_fixtures`, `tournament_results`, `tournament_standings`
- Writes: `phase_context_signals`
- Signals: `total_goals_factor`, `live_draw_adjustment`, `team_expected_goals_factor`
- Locales: `en`, `de`
- Confidence policy: Signals are phase-gated: knockout effects only apply to non-group fixtures; final-group dynamics only apply when both teams have one group match remaining.

### `weather`

Fetch Open-Meteo match-window weather and emit total-goal factors.

- Version: `0.1.0`
- Kind: `source`
- Priority: `260`
- Events: `feature_signals_requested`
- Reads: `weather_observations`
- Writes: `weather_observations`
- Signals: `total_goals_factor`
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Ledger avoids unnecessary repeated weather requests even though Open-Meteo has no project API key.
- Confidence policy: Weather signals are capped and require material heat, wind, rain, or storm risk.

### `lineup_availability`

Fetch reliable public lineup/availability reports and emit side-specific xG factors.

- Version: `0.1.0`
- Kind: `source`
- Priority: `265`
- Events: `feature_signals_requested`
- Reads: `lineup_availability`, `football_data_match_details`
- Writes: `lineup_availability`, `lineup_consensus`, `extraction_diagnostics`
- Signals: `team_expected_goals_factor`
- Locales: `en`, `de`
- Environment:
  - `NEWS_API_KEY` (optional): NewsAPI key for bounded availability discovery.
- Quota: limited and ledger-required - Fixture-specific NewsAPI calls are skipped while fresh or near the quota floor.
- Confidence policy: Reliable articles are aggregated per fixture/side and capped before model use.

### `public_analysis`

Fetch reliable public pre/postgame articles and emit conservative tempo signals.

- Version: `0.1.0`
- Kind: `source`
- Priority: `270`
- Events: `feature_signals_requested`
- Reads: `public_match_analysis`
- Writes: `public_match_analysis`, `match_analysis_causes`, `match_analysis_team_adjustments`, `extraction_diagnostics`
- Signals: `total_goals_factor`
- Locales: `en`, `de`
- Environment:
  - `NEWS_API_KEY` (optional): NewsAPI key for bounded article discovery.
- Quota: limited and ledger-required - Fixture-specific NewsAPI calls are skipped while fresh or near the quota floor.
- Confidence policy: Only reliable domains above threshold are aggregated into capped total-goal factors.

### `postmatch_stats`

Normalize postmatch xG/stats into team chance-quality performance rows.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `300`
- Events: `feature_signals_requested`
- Reads: `postmatch_stats`, `public_match_analysis`
- Writes: `postmatch_stats`, `postmatch_team_performance`, `extraction_diagnostics`
- Signals: -
- Locales: `en`, `de`
- Confidence policy: xG is preferred; shots/on-target/corners provide a conservative proxy; red-card matches are down-weighted.

### `automatic_match_notes`

Aggregate public-analysis and availability rows into capped automatic match-note signals.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `310`
- Events: `feature_signals_requested`
- Reads: `public_match_analysis`, `lineup_availability`
- Writes: `automatic_match_notes`, `extraction_diagnostics`
- Signals: `total_goals_factor`, `team_expected_goals_factor`
- Locales: `en`, `de`
- Confidence policy: Signals require at least two reliable supporting rows and are capped below direct source signals.

### `player_impact`

Derive player/squad market-value impact from normalized squad rows.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `310`
- Events: `feature_signals_requested`
- Reads: `squad_players`, `squad_values`
- Writes: `squad_values`, `player_impact`
- Signals: `team_expected_goals_factor`, `total_goals_factor`
- Locales: `en`, `de`
- Confidence policy: Player-value effects are coverage weighted and capped so they cannot dominate team/result/market layers.

### `ml_outcome`

Train a deterministic Elo-delta outcome calibration model and emit H/D/A signals.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `320`
- Events: `feature_signals_requested`
- Reads: `historical_results`
- Writes: `ml_outcome_models`
- Signals: `ml_hda_probabilities`
- Locales: `en`, `de`
- Confidence policy: The model is sample-size weighted, smoothed, and blended below market odds.

### `live_calibration`

Convert finished tournament results and chance-quality rows into team expected-goal factors.

- Version: `0.1.0`
- Kind: `signal`
- Priority: `330`
- Events: `results_updated`, `feature_signals_requested`
- Reads: `postmatch_team_performance`, `prediction_backtest`, `match_analysis_team_adjustments`
- Writes: `team_calibration`, `live_global_calibration`, `calibration_decisions`
- Signals: `team_expected_goals_factor`, `total_goals_factor`, `live_draw_adjustment`, `live_score_tail_factor`, `live_favorite_outcome_factor`
- Locales: `en`, `de`
- Confidence policy: Recent tournament samples are conservative, sample-size weighted, and capped.

### `srf_experts`

Fetch SRF public expert pages and emit conservative expert consensus signals.

- Version: `0.1.0`
- Kind: `source`
- Priority: `340`
- Events: `feature_signals_requested`
- Reads: `srf_expert_predictions`
- Writes: `srf_expert_predictions`, `srf_expert_performance`, `extraction_diagnostics`
- Signals: `expert_hda_probabilities`
- Locales: `en`, `de`
- Quota: not limited and ledger-required - Public expert pages are refetched at most every 15 minutes while fixtures are open; pages without extractable picks back off for six hours because SRF hides tips until kickoff.
- Confidence policy: Expert consensus confidence rises with extracted expert count and is capped below market weight.

### `baseline_model`

Transparent Elo/goal-profile model with capped typed-signal blending.

- Version: `0.1.0`
- Kind: `model`
- Priority: `400`
- Events: `predictions_requested`
- Reads: `historical_results`, `tournament_results`
- Writes: -
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Confidence is the strongest H/D/A probability after exact-score matrix adjustments.

### `provider_optimizer_srf_ch`

Optimize exact-score tips for srf.ch scoring rules.

- Version: `0.1.0`
- Kind: `provider_optimizer`
- Priority: `700`
- Events: `provider_optimization_requested`
- Reads: -
- Writes: -
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Uses provider expected points over the neutral score matrix without changing model probabilities.

### `provider_optimizer_20min_ch`

Optimize selections for 20min.ch scoring rules.

- Version: `0.1.0`
- Kind: `provider_optimizer`
- Priority: `710`
- Events: `provider_optimization_requested`
- Reads: -
- Writes: -
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Uses provider expected points or advancement probabilities without changing model probabilities.

### `structured_output`

Persist provider-neutral predictions and provider-specific optimized tips.

- Version: `0.1.0`
- Kind: `output`
- Priority: `900`
- Events: `prediction_ready`, `provider_tip_ready`
- Reads: -
- Writes: `predictions`, `optimized_tips`
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Output-only plugin; does not affect predictions.

### `match_intel`

Persist prematch review-priority rows from predictions, provider tips, and active model signals.

- Version: `0.1.0`
- Kind: `output`
- Priority: `930`
- Events: `debug_report_requested`
- Reads: -
- Writes: `match_intel`
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Output-only plugin; helps humans inspect fragile fixtures without changing probabilities.

### `source_diagnostics`

Persist every plugin diagnostic with plugin/event metadata.

- Version: `0.1.0`
- Kind: `output`
- Priority: `940`
- Events: `debug_report_requested`
- Reads: -
- Writes: `source_diagnostics`
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Diagnostics are audit artifacts and do not affect predictions.

### `debug_report`

Persist per-fixture source coverage, model adjustments, and provider tips.

- Version: `0.1.0`
- Kind: `output`
- Priority: `950`
- Events: `debug_report_requested`
- Reads: -
- Writes: `prediction_debug_report`, `prediction_signal_impacts`
- Signals: -
- Locales: `en`, `de`
- Confidence policy: Debug rows are audit artifacts and do not affect predictions.
