"""Built-in workflow plugins."""

from worldcup_predictions.plugins.models.baseline_model import BaselineModelPlugin
from worldcup_predictions.plugins.signals.automatic_match_notes import AutomaticMatchNotesPlugin
from worldcup_predictions.plugins.diagnostics.debug_report import DebugReportPlugin
from worldcup_predictions.plugins.sources.fixtures.fifa_match_centre import FifaMatchCentrePlugin
from worldcup_predictions.plugins.sources.fixtures.football_data import FootballDataPlugin
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources import DynamicPublicSourcesPlugin
from worldcup_predictions.plugins.sources.history.historical_results_source import HistoricalResultsSourcePlugin
from worldcup_predictions.plugins.sources.enrichment.kaggle_source import KaggleSourcePlugin
from worldcup_predictions.plugins.sources.enrichment.lineup_availability import LineupAvailabilityPlugin
from worldcup_predictions.plugins.signals.live_calibration import LiveCalibrationPlugin
from worldcup_predictions.plugins.sources.markets.market_odds import MarketOddsPlugin
from worldcup_predictions.plugins.signals.market_trend import MarketTrendPlugin
from worldcup_predictions.plugins.diagnostics.match_intel import MatchIntelPlugin
from worldcup_predictions.plugins.signals.ml_outcome import MlOutcomePlugin
from worldcup_predictions.plugins.sources.fixtures.openfootball_source import OpenFootballSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.fixturedownload_source import FixtureDownloadSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.openligadb_source import OpenLigaDbSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.thesportsdb_source import TheSportsDbSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.wikipedia_results import WikipediaResultsPlugin
from worldcup_predictions.plugins.signals.phase_context import PhaseContextPlugin
from worldcup_predictions.plugins.sources.enrichment.postmatch_stats import PostmatchStatsPlugin
from worldcup_predictions.plugins.signals.player_impact import PlayerImpactPlugin
from worldcup_predictions.plugins.sources.enrichment.public_analysis import PublicAnalysisPlugin
from worldcup_predictions.plugins.sources.enrichment.google_news_rss import GoogleNewsRssPlugin
from worldcup_predictions.plugins.sources.fixtures.public_score_sources import PublicScoreSourcesPlugin
from worldcup_predictions.plugins.workflow.result_monitoring import ResultMonitoringPlugin
from worldcup_predictions.plugins.workflow.source_diagnostics import SourceDiagnosticsPlugin
from worldcup_predictions.plugins.sources.fixtures.srf_public import SrfPublicPlugin
from worldcup_predictions.plugins.workflow.structured_output.plugin import StructuredOutputPlugin
from worldcup_predictions.plugins.workflow.tournament_state import TournamentStatePlugin
from worldcup_predictions.plugins.sources.enrichment.transfermarkt_source import TransfermarktSourcePlugin
from worldcup_predictions.plugins.sources.enrichment.weather import WeatherPlugin
from worldcup_predictions.plugins.sources.enrichment.elo_ratings import EloRatingsPlugin
from worldcup_predictions.plugins.sources.enrichment.wikipedia_squads import WikipediaSquadsPlugin
from worldcup_predictions.plugins.providers import (
    SrfChProviderOptimizerPlugin,
    TwentyMinChProviderOptimizerPlugin,
)


def builtin_plugins():
    return [
        TournamentStatePlugin(),
        SrfPublicPlugin(),
        OpenFootballSourcePlugin(),
        FixtureDownloadSourcePlugin(),
        OpenLigaDbSourcePlugin(),
        TheSportsDbSourcePlugin(),
        WikipediaResultsPlugin(),
        FifaMatchCentrePlugin(),
        HistoricalResultsSourcePlugin(),
        FootballDataPlugin(),
        PublicScoreSourcesPlugin(),
        DynamicPublicSourcesPlugin(),
        ResultMonitoringPlugin(),
        KaggleSourcePlugin(),
        WikipediaSquadsPlugin(),
        TransfermarktSourcePlugin(),
        MarketOddsPlugin(),
        MarketTrendPlugin(),
        PhaseContextPlugin(),
        WeatherPlugin(),
        EloRatingsPlugin(),
        PublicAnalysisPlugin(),
        GoogleNewsRssPlugin(),
        LineupAvailabilityPlugin(),
        PostmatchStatsPlugin(),
        AutomaticMatchNotesPlugin(),
        PlayerImpactPlugin(),
        MlOutcomePlugin(),
        LiveCalibrationPlugin(),
        BaselineModelPlugin(),
        SrfChProviderOptimizerPlugin(),
        TwentyMinChProviderOptimizerPlugin(),
        StructuredOutputPlugin(),
        MatchIntelPlugin(),
        SourceDiagnosticsPlugin(),
        DebugReportPlugin(),
    ]
