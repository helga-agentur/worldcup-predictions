"""Core workflow contracts and plugin runtime."""

from worldcup_predictions.core.contracts import (
    Artifact,
    Diagnostic,
    Fixture,
    OutcomeProbabilities,
    OptimizedTip,
    Prediction,
    ScoreMatrixEntry,
    ScoreTip,
    Signal,
    ProviderRuleset,
)
from worldcup_predictions.core.config import DEFAULT_CONFIG, ProjectConfig, SourceDefaults, load_project_config
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.i18n import TranslationCatalog, load_translation_catalog
from worldcup_predictions.core.metadata import DatasetContract, EnvVar, PluginKind, PluginMetadata, QuotaPolicy, SignalContract
from worldcup_predictions.core.payloads import (
    DebugReportRequestedPayload,
    FeatureSignalsRequestedPayload,
    FixturesRequestedPayload,
    PredictionsRequestedPayload,
    PredictionReadyPayload,
    ProviderOptimizationRequestedPayload,
    ProviderTipReadyPayload,
    WorkflowStartedPayload,
)
from worldcup_predictions.core.plugin import BasePlugin, PluginManager, PluginResult
from worldcup_predictions.core.workflow import PredictionWorkflow, WorkflowContext, WorkflowRun
from worldcup_predictions.entities import CountryRegistry, ResolvedEntity, load_country_registry

__all__ = [
    "Artifact",
    "BasePlugin",
    "CountryRegistry",
    "DEFAULT_CONFIG",
    "DebugReportRequestedPayload",
    "Diagnostic",
    "DatasetContract",
    "EnvVar",
    "EventName",
    "FeatureSignalsRequestedPayload",
    "Fixture",
    "FixturesRequestedPayload",
    "OutcomeProbabilities",
    "OptimizedTip",
    "PluginManager",
    "PluginKind",
    "PluginMetadata",
    "PluginResult",
    "Prediction",
    "PredictionReadyPayload",
    "PredictionWorkflow",
    "PredictionsRequestedPayload",
    "ProviderOptimizationRequestedPayload",
    "ProjectConfig",
    "QuotaPolicy",
    "ProviderTipReadyPayload",
    "ScoreMatrixEntry",
    "ScoreTip",
    "Signal",
    "SignalContract",
    "SourceDefaults",
    "ProviderRuleset",
    "WorkflowContext",
    "WorkflowRun",
    "WorkflowStartedPayload",
    "ResolvedEntity",
    "TranslationCatalog",
    "load_country_registry",
    "load_project_config",
    "load_translation_catalog",
]
