"""Built-in workflow plugins."""

from worldcup_predictions.plugins.baseline_model import BaselineModelPlugin
from worldcup_predictions.plugins.automatic_match_notes import AutomaticMatchNotesPlugin
from worldcup_predictions.plugins.debug_report import DebugReportPlugin
from worldcup_predictions.plugins.fifa_match_centre import FifaMatchCentrePlugin
from worldcup_predictions.plugins.football_data import FootballDataPlugin
from worldcup_predictions.plugins.historical_results_source import HistoricalResultsSourcePlugin
from worldcup_predictions.plugins.kaggle_source import KaggleSourcePlugin
from worldcup_predictions.plugins.lineup_availability import LineupAvailabilityPlugin
from worldcup_predictions.plugins.live_calibration import LiveCalibrationPlugin
from worldcup_predictions.plugins.market_odds import MarketOddsPlugin
from worldcup_predictions.plugins.market_trend import MarketTrendPlugin
from worldcup_predictions.plugins.match_intel import MatchIntelPlugin
from worldcup_predictions.plugins.ml_outcome import MlOutcomePlugin
from worldcup_predictions.plugins.openfootball_source import OpenFootballSourcePlugin
from worldcup_predictions.plugins.phase_context import PhaseContextPlugin
from worldcup_predictions.plugins.postmatch_stats import PostmatchStatsPlugin
from worldcup_predictions.plugins.player_impact import PlayerImpactPlugin
from worldcup_predictions.plugins.public_analysis import PublicAnalysisPlugin
from worldcup_predictions.plugins.public_score_sources import PublicScoreSourcesPlugin
from worldcup_predictions.plugins.result_monitoring import ResultMonitoringPlugin
from worldcup_predictions.plugins.source_diagnostics import SourceDiagnosticsPlugin
from worldcup_predictions.plugins.srf_experts import SrfExpertsPlugin
from worldcup_predictions.plugins.srf_public import SrfPublicPlugin
from worldcup_predictions.plugins.structured_output.plugin import StructuredOutputPlugin
from worldcup_predictions.plugins.tournament_state import TournamentStatePlugin
from worldcup_predictions.plugins.transfermarkt_source import TransfermarktSourcePlugin
from worldcup_predictions.plugins.weather import WeatherPlugin
from worldcup_predictions.plugins.wikipedia_squads import WikipediaSquadsPlugin
from worldcup_predictions.plugins.provider_optimizers import (
    SrfChProviderOptimizerPlugin,
    TwentyMinChProviderOptimizerPlugin,
)


def builtin_plugins():
    return [
        TournamentStatePlugin(),
        SrfPublicPlugin(),
        OpenFootballSourcePlugin(),
        FifaMatchCentrePlugin(),
        HistoricalResultsSourcePlugin(),
        FootballDataPlugin(),
        PublicScoreSourcesPlugin(),
        ResultMonitoringPlugin(),
        KaggleSourcePlugin(),
        WikipediaSquadsPlugin(),
        TransfermarktSourcePlugin(),
        MarketOddsPlugin(),
        MarketTrendPlugin(),
        PhaseContextPlugin(),
        WeatherPlugin(),
        PublicAnalysisPlugin(),
        LineupAvailabilityPlugin(),
        PostmatchStatsPlugin(),
        AutomaticMatchNotesPlugin(),
        PlayerImpactPlugin(),
        MlOutcomePlugin(),
        LiveCalibrationPlugin(),
        SrfExpertsPlugin(),
        BaselineModelPlugin(),
        SrfChProviderOptimizerPlugin(),
        TwentyMinChProviderOptimizerPlugin(),
        StructuredOutputPlugin(),
        MatchIntelPlugin(),
        SourceDiagnosticsPlugin(),
        DebugReportPlugin(),
    ]
