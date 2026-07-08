"""Canonical structured dataset names and contracts."""

from __future__ import annotations

from worldcup_predictions.core.metadata import DatasetContract


PREDICTIONS = "predictions"
OPTIMIZED_TIPS = "optimized_tips"
TOURNAMENT_FIXTURES = "tournament_fixtures"
TOURNAMENT_RESULTS = "tournament_results"
RESULT_UPDATE_AUDIT = "result_update_audit"
TOURNAMENT_STANDINGS = "tournament_standings"
TOURNAMENT_RESULT_CHECKS = "tournament_result_checks"
GROUP_STATE_SIGNALS = "group_state_signals"
PHASE_CONTEXT_SIGNALS = "phase_context_signals"
HISTORICAL_RESULTS = "historical_results"
MARKET_ODDS = "market_odds"
WEATHER_OBSERVATIONS = "weather_observations"
PUBLIC_MATCH_ANALYSIS = "public_match_analysis"
PUBLIC_SOURCE_PAGES = "public_source_pages"
PUBLIC_SOURCE_CLAIMS = "public_source_claims"
PUBLIC_CLAIM_CONSENSUS = "public_claim_consensus"
PUBLIC_SOURCE_REPUTATION = "public_source_reputation"
PUBLIC_MARKET_OBSERVATIONS = "public_market_observations"
LINEUP_AVAILABILITY = "lineup_availability"
LINEUP_CONSENSUS = "lineup_consensus"
POSTMATCH_STATS = "postmatch_stats"
POSTMATCH_TEAM_PERFORMANCE = "postmatch_team_performance"
TEAM_CALIBRATION = "team_calibration"
LIVE_GLOBAL_CALIBRATION = "live_global_calibration"
CALIBRATION_DECISIONS = "calibration_decisions"
SRF_EXPERT_PREDICTIONS = "srf_expert_predictions"
PREDICTION_DEBUG_REPORT = "prediction_debug_report"
PREDICTION_BACKTEST = "prediction_backtest"
PREDICTION_LEDGER = "prediction_ledger"
PUBLISHED_PREDICTION_LEDGER = "published_prediction_ledger"
PUBLISHED_PREDICTION_SEED = "published_prediction_seed"
MODEL_CALIBRATION = "model_calibration"
PREDICTION_AUDIT = "prediction_audit"
PREDICTION_REPORTS = "prediction_reports"
FOOTBALL_DATA_TEAMS = "football_data_teams"
FOOTBALL_DATA_COMPETITION = "football_data_competition"
FOOTBALL_DATA_STANDINGS = "football_data_standings"
FOOTBALL_DATA_MATCH_DETAILS = "football_data_match_details"
FIFA_MATCH_DETAILS = "fifa_match_details"
SQUAD_PLAYERS = "squad_players"
SQUAD_VALUES = "squad_values"
PLAYER_IMPACT = "player_impact"
ML_OUTCOME_MODELS = "ml_outcome_models"
PREDICTION_SNAPSHOTS = "prediction_snapshots"
PREDICTION_COMPARISONS = "prediction_comparisons"
PREDICTION_RUN_SUMMARIES = "prediction_run_summaries"
PREDICTION_EXPORTS = "prediction_exports"
BASELINE_BUNDLES = "baseline_bundles"
MATCH_INTEL = "match_intel"
POSTMATCH_LEARNING = "postmatch_learning"
POSTMATCH_REVIEW_QUEUE = "postmatch_review_queue"
MATCH_ANALYSIS_CAUSES = "match_analysis_causes"
MATCH_ANALYSIS_TEAM_ADJUSTMENTS = "match_analysis_team_adjustments"
PROVIDER_POINTS = "provider_points"
PROVIDER_KNOCKOUT_AUDIT = "provider_knockout_audit"
PROVIDER_BONUS_TRACKER = "provider_bonus_tracker"
PLUGIN_RUN_DIAGNOSTICS = "plugin_run_diagnostics"
PLUGIN_EVENT_OUTPUTS = "plugin_event_outputs"
PREDICTION_SIGNAL_IMPACTS = "prediction_signal_impacts"
DIAGNOSTICS_COMPLETENESS_AUDIT = "diagnostics_completeness_audit"
SOURCE_DIAGNOSTICS = "source_diagnostics"
EXTRACTION_DIAGNOSTICS = "extraction_diagnostics"
AUTOMATIC_MATCH_NOTES = "automatic_match_notes"
ENTITY_VALIDATION = "entity_validation"
ENTITY_ALIASES_GENERATED = "entity_aliases_generated"
SHOOTOUTS = "shootouts"
KAGGLE_DATASETS = "kaggle_datasets"
WIKIPEDIA_SQUADS = "wikipedia_squads"
TRANSFERMARKT_SEARCH_RESULTS = "transfermarkt_search_results"
SRF_FIXTURES = "srf_fixtures"
SRF_BONUS_QUESTIONS = "srf_bonus_questions"
SRF_EXPERT_PERFORMANCE = "srf_expert_performance"
MARKET_OUTRIGHTS = "market_outrights"
MARKET_TRENDS = "market_trends"
SIMULATION_RUNS = "simulation_runs"
SIMULATION_SUMMARY = "simulation_summary"
DATA_UPDATE_HOOKS = "data_update_hooks"
AUTOMATION_HOOKS = "automation_hooks"


DATASET_CONTRACTS: dict[str, DatasetContract] = {
    PREDICTIONS: DatasetContract(PREDICTIONS, "Provider-neutral prediction output.", ("fixture_key", "event_date")),
    OPTIMIZED_TIPS: DatasetContract(OPTIMIZED_TIPS, "Provider-specific optimized tips.", ("fixture_key", "provider")),
    TOURNAMENT_FIXTURES: DatasetContract(TOURNAMENT_FIXTURES, "Canonical tournament fixtures.", ("fixture_key", "event_date", "home_team", "away_team")),
    TOURNAMENT_RESULTS: DatasetContract(TOURNAMENT_RESULTS, "Source-derived final-score observations before tournament-state consensus.", ("fixture_key", "home_score", "away_score")),
    RESULT_UPDATE_AUDIT: DatasetContract(RESULT_UPDATE_AUDIT, "Audit rows for newly discovered or changed final scores.", ("fixture_key", "update_type", "source")),
    TOURNAMENT_STANDINGS: DatasetContract(TOURNAMENT_STANDINGS, "Derived group standings.", ("group", "team", "points")),
    TOURNAMENT_RESULT_CHECKS: DatasetContract(TOURNAMENT_RESULT_CHECKS, "Cross-source result consensus checks and confirmation status."),
    GROUP_STATE_SIGNALS: DatasetContract(GROUP_STATE_SIGNALS, "Derived group-state motivation rows.", ("fixture_key",)),
    PHASE_CONTEXT_SIGNALS: DatasetContract(PHASE_CONTEXT_SIGNALS, "Phase-aware knockout and final-group-match context rows.", ("fixture_key", "phase_context")),
    HISTORICAL_RESULTS: DatasetContract(HISTORICAL_RESULTS, "Historical international football results.", ("date", "home_team", "away_team")),
    MARKET_ODDS: DatasetContract(MARKET_ODDS, "Extracted public market odds facts.", ("fixture_key",)),
    MARKET_TRENDS: DatasetContract(MARKET_TRENDS, "Market movement facts derived from the odds snapshot history.", ("fixture_key",)),
    WEATHER_OBSERVATIONS: DatasetContract(WEATHER_OBSERVATIONS, "Aggregated match-window weather rows.", ("fixture_key",)),
    PUBLIC_MATCH_ANALYSIS: DatasetContract(PUBLIC_MATCH_ANALYSIS, "Reliable public pre/postgame analysis rows.", ("fixture_key", "phase")),
    PUBLIC_SOURCE_PAGES: DatasetContract(PUBLIC_SOURCE_PAGES, "Fetched public-source page metadata and content fingerprints.", ("url", "domain", "status")),
    PUBLIC_SOURCE_CLAIMS: DatasetContract(PUBLIC_SOURCE_CLAIMS, "Atomic fixture, result, market, and analysis claims extracted from dynamic public sources.", ("claim_id", "claim_type", "source_url", "domain")),
    PUBLIC_CLAIM_CONSENSUS: DatasetContract(PUBLIC_CLAIM_CONSENSUS, "Grouped public-source claim consensus and weighted support.", ("claim_type", "consensus_key")),
    PUBLIC_SOURCE_REPUTATION: DatasetContract(PUBLIC_SOURCE_REPUTATION, "Domain-level public-source reputation by claim type.", ("domain", "claim_type", "source_score")),
    PUBLIC_MARKET_OBSERVATIONS: DatasetContract(PUBLIC_MARKET_OBSERVATIONS, "Allowed public-page market observations normalized for trend diagnostics.", ("fixture_key", "domain")),
    LINEUP_AVAILABILITY: DatasetContract(LINEUP_AVAILABILITY, "Automatic injury, suspension, rotation, and availability rows.", ("fixture_key", "affected_side")),
    LINEUP_CONSENSUS: DatasetContract(LINEUP_CONSENSUS, "Aggregated lineup/availability consensus rows by fixture and side.", ("fixture_key", "affected_side")),
    POSTMATCH_STATS: DatasetContract(POSTMATCH_STATS, "Postmatch xG/stat input rows.", ("fixture_key",)),
    POSTMATCH_TEAM_PERFORMANCE: DatasetContract(POSTMATCH_TEAM_PERFORMANCE, "Derived team chance-quality performance rows.", ("fixture_key", "side")),
    TEAM_CALIBRATION: DatasetContract(TEAM_CALIBRATION, "Team-level live calibration rows.", ("team",)),
    LIVE_GLOBAL_CALIBRATION: DatasetContract(LIVE_GLOBAL_CALIBRATION, "Global live calibration factors learned from played tournament matches.", ("record_key",)),
    CALIBRATION_DECISIONS: DatasetContract(CALIBRATION_DECISIONS, "Report-only calibration recommendation and value-change audit rows.", ("parameter", "action")),
    SRF_EXPERT_PREDICTIONS: DatasetContract(SRF_EXPERT_PREDICTIONS, "Extracted SRF expert predictions.", ("fixture_key", "expert_id")),
    PREDICTION_DEBUG_REPORT: DatasetContract(PREDICTION_DEBUG_REPORT, "Per-fixture prediction debug report.", ("fixture_key",)),
    PREDICTION_BACKTEST: DatasetContract(PREDICTION_BACKTEST, "Backtest evaluation rows.", ("fixture_key", "points")),
    PREDICTION_LEDGER: DatasetContract(PREDICTION_LEDGER, "Past and future match-level prediction ledger.", ("fixture_key", "status")),
    PUBLISHED_PREDICTION_LEDGER: DatasetContract(PUBLISHED_PREDICTION_LEDGER, "Website-facing prediction ledger with locked past predictions.", ("fixture_key", "status")),
    PUBLISHED_PREDICTION_SEED: DatasetContract(PUBLISHED_PREDICTION_SEED, "Archived pre-refactor published predictions used as a parity seed.", ("fixture_key", "srf_tip")),
    MODEL_CALIBRATION: DatasetContract(MODEL_CALIBRATION, "Model calibration/tuning run summaries.", ("calibration_id",)),
    PREDICTION_AUDIT: DatasetContract(PREDICTION_AUDIT, "Audit rows for frozen pre-match prediction snapshots.", ("fixture_key", "snapshot_id")),
    PREDICTION_REPORTS: DatasetContract(PREDICTION_REPORTS, "Human-readable generated report manifests.", ("report_key", "path")),
    FOOTBALL_DATA_TEAMS: DatasetContract(FOOTBALL_DATA_TEAMS, "football-data.org team and squad metadata.", ("team",)),
    FOOTBALL_DATA_COMPETITION: DatasetContract(FOOTBALL_DATA_COMPETITION, "football-data.org competition metadata.", ("competition_id",)),
    FOOTBALL_DATA_STANDINGS: DatasetContract(FOOTBALL_DATA_STANDINGS, "football-data.org standings rows.", ("team",)),
    FOOTBALL_DATA_MATCH_DETAILS: DatasetContract(FOOTBALL_DATA_MATCH_DETAILS, "football-data.org per-match detail rows.", ("fixture_key",)),
    FIFA_MATCH_DETAILS: DatasetContract(FIFA_MATCH_DETAILS, "FIFA public match-centre calendar facts and official match metadata.", ("fixture_key", "fifa_match_id")),
    SQUAD_PLAYERS: DatasetContract(SQUAD_PLAYERS, "Canonical squad-player rows with optional market-value fields.", ("team", "player_name")),
    SQUAD_VALUES: DatasetContract(SQUAD_VALUES, "Team-level squad market-value summaries.", ("team",)),
    PLAYER_IMPACT: DatasetContract(PLAYER_IMPACT, "Team-level player-impact model factors.", ("team",)),
    ML_OUTCOME_MODELS: DatasetContract(ML_OUTCOME_MODELS, "Lightweight trained outcome-model snapshots.", ("model_id",)),
    PREDICTION_SNAPSHOTS: DatasetContract(PREDICTION_SNAPSHOTS, "Frozen prediction rows for regression comparisons.", ("snapshot_id", "fixture_key")),
    PREDICTION_COMPARISONS: DatasetContract(PREDICTION_COMPARISONS, "Prediction snapshot comparison rows.", ("comparison_id", "fixture_key")),
    PREDICTION_RUN_SUMMARIES: DatasetContract(PREDICTION_RUN_SUMMARIES, "Cron/run-level prediction manifests with plugin and source context.", ("run_id", "snapshot_id")),
    PREDICTION_EXPORTS: DatasetContract(PREDICTION_EXPORTS, "One-file prediction export manifests.", ("export_id", "path")),
    BASELINE_BUNDLES: DatasetContract(BASELINE_BUNDLES, "Refactor-safety baseline bundle manifests.", ("baseline_id", "path")),
    MATCH_INTEL: DatasetContract(MATCH_INTEL, "Prematch review-priority rows derived from predictions and signals.", ("fixture_key",)),
    POSTMATCH_LEARNING: DatasetContract(POSTMATCH_LEARNING, "Joined prediction-miss, stat, and cause-learning rows.", ("fixture_key",)),
    POSTMATCH_REVIEW_QUEUE: DatasetContract(POSTMATCH_REVIEW_QUEUE, "Prioritized postmatch review tasks.", ("fixture_key",)),
    MATCH_ANALYSIS_CAUSES: DatasetContract(MATCH_ANALYSIS_CAUSES, "Structured postmatch cause labels extracted from public analysis.", ("fixture_key", "cause_type")),
    MATCH_ANALYSIS_TEAM_ADJUSTMENTS: DatasetContract(MATCH_ANALYSIS_TEAM_ADJUSTMENTS, "Small team-level adjustments inferred from public analysis causes.", ("fixture_key", "team")),
    PROVIDER_POINTS: DatasetContract(PROVIDER_POINTS, "Provider-specific points for optimized recommended tips.", ("provider", "fixture_key")),
    PROVIDER_KNOCKOUT_AUDIT: DatasetContract(PROVIDER_KNOCKOUT_AUDIT, "Knockout-stage provider optimizer comparison rows.", ("fixture_key", "provider")),
    PROVIDER_BONUS_TRACKER: DatasetContract(PROVIDER_BONUS_TRACKER, "Virtual provider bonus and match-point tracker.", ("provider", "question_key")),
    PLUGIN_RUN_DIAGNOSTICS: DatasetContract(PLUGIN_RUN_DIAGNOSTICS, "Core plugin input/output diagnostics for each event emission.", ("run_id", "plugin_id", "event")),
    PLUGIN_EVENT_OUTPUTS: DatasetContract(PLUGIN_EVENT_OUTPUTS, "Append-only core journal of detailed plugin event outputs.", ("run_id", "plugin_id", "event", "output_type")),
    PREDICTION_SIGNAL_IMPACTS: DatasetContract(PREDICTION_SIGNAL_IMPACTS, "Per-fixture model signal impact rows for audit/backtesting.", ("fixture_key", "signal_name")),
    DIAGNOSTICS_COMPLETENESS_AUDIT: DatasetContract(DIAGNOSTICS_COMPLETENESS_AUDIT, "Audit rows that verify plugin/core decision diagnostics are complete enough for later tuning.", ("audit_id", "scope", "subject", "status")),
    SOURCE_DIAGNOSTICS: DatasetContract(SOURCE_DIAGNOSTICS, "Source-level diagnostics that explain missing, rejected, or skipped rows.", ("source",)),
    EXTRACTION_DIAGNOSTICS: DatasetContract(EXTRACTION_DIAGNOSTICS, "Detailed extraction acceptance and rejection rows.", ("source", "extractor", "status", "reason")),
    AUTOMATIC_MATCH_NOTES: DatasetContract(AUTOMATIC_MATCH_NOTES, "Capped automatic match notes derived from analysis, lineup, and postmatch evidence.", ("fixture_key",)),
    ENTITY_VALIDATION: DatasetContract(ENTITY_VALIDATION, "Entity registry and imported-row validation findings.", ("entity_type", "status")),
    ENTITY_ALIASES_GENERATED: DatasetContract(ENTITY_ALIASES_GENERATED, "Generated entity alias candidates derived from structured rows.", ("entity_type", "canonical_id", "alias")),
    SHOOTOUTS: DatasetContract(SHOOTOUTS, "Historical penalty shootout rows.", ("date", "home_team", "away_team")),
    KAGGLE_DATASETS: DatasetContract(KAGGLE_DATASETS, "Kaggle dataset search/download metadata.", ("dataset_ref",)),
    WIKIPEDIA_SQUADS: DatasetContract(WIKIPEDIA_SQUADS, "Wikipedia-derived squad rows with attribution.", ("team", "player_name")),
    TRANSFERMARKT_SEARCH_RESULTS: DatasetContract(TRANSFERMARKT_SEARCH_RESULTS, "Robots-aware Transfermarkt search result facts.", ("team", "result_title")),
    SRF_FIXTURES: DatasetContract(SRF_FIXTURES, "SRF public fixture rows.", ("fixture_key",)),
    SRF_BONUS_QUESTIONS: DatasetContract(SRF_BONUS_QUESTIONS, "SRF public bonus question rows.", ("question_key",)),
    SRF_EXPERT_PERFORMANCE: DatasetContract(SRF_EXPERT_PERFORMANCE, "Historical SRF expert scoring/reliability rows.", ("expert_id",)),
    MARKET_OUTRIGHTS: DatasetContract(MARKET_OUTRIGHTS, "Public tournament outright winner odds.", ("team",)),
    SIMULATION_RUNS: DatasetContract(SIMULATION_RUNS, "Monte Carlo tournament simulation rows or compressed sample facts.", ("simulation_id",)),
    SIMULATION_SUMMARY: DatasetContract(SIMULATION_SUMMARY, "Aggregated tournament simulation outputs for provider bonus optimization.", ("simulation_id",)),
    DATA_UPDATE_HOOKS: DatasetContract(DATA_UPDATE_HOOKS, "Applied one-shot runtime data update hooks.", ("hook_id", "status")),
    AUTOMATION_HOOKS: DatasetContract(AUTOMATION_HOOKS, "Applied one-shot scheduled automation hooks.", ("hook_id", "action", "status")),
}


def known_dataset_names() -> tuple[str, ...]:
    return tuple(sorted(DATASET_CONTRACTS))
