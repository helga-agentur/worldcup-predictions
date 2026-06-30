"""Project-level defaults for the workflow runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import tomllib

from worldcup_predictions.core.constants import PROJECT_USER_AGENT


@dataclass(frozen=True)
class SourceDefaults:
    """Defaults shared by public source plugins."""

    timeout_seconds: int = 20
    default_refresh_minutes: int = 30
    weather_refresh_minutes: int = 60
    expert_refresh_minutes: int = 15
    odds_quota_remaining_floor: int = 2
    news_quota_remaining_floor: int = 5


@dataclass(frozen=True)
class ProjectConfig:
    """Repository-level workflow configuration.

    This layer centralizes stable defaults. Source-specific runtime state such
    as remaining quota still belongs in the source ledger, not config.
    """

    default_locale: str = "en"
    supported_locales: tuple[str, ...] = ("en", "de")
    user_agent: str = PROJECT_USER_AGENT
    source_defaults: SourceDefaults = field(default_factory=SourceDefaults)


DEFAULT_CONFIG = ProjectConfig()


def load_project_config(project_root: Path) -> ProjectConfig:
    """Load optional `worldcup_predictions.toml` overrides from the project root."""

    path = Path(project_root) / "worldcup_predictions.toml"
    if not path.exists():
        return DEFAULT_CONFIG
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return _project_config_from_mapping(data)


def _project_config_from_mapping(data: dict[str, Any]) -> ProjectConfig:
    config = DEFAULT_CONFIG
    project = data.get("project", {})
    if isinstance(project, dict):
        supported_locales = project.get("supported_locales", config.supported_locales)
        if isinstance(supported_locales, list):
            supported_locales = tuple(str(locale) for locale in supported_locales)
        config = replace(
            config,
            default_locale=str(project.get("default_locale", config.default_locale)),
            supported_locales=supported_locales,
            user_agent=str(project.get("user_agent", config.user_agent)),
        )

    source = data.get("source_defaults", {})
    if isinstance(source, dict):
        current = config.source_defaults
        config = replace(
            config,
            source_defaults=replace(
                current,
                timeout_seconds=int(source.get("timeout_seconds", current.timeout_seconds)),
                default_refresh_minutes=int(source.get("default_refresh_minutes", current.default_refresh_minutes)),
                weather_refresh_minutes=int(source.get("weather_refresh_minutes", current.weather_refresh_minutes)),
                expert_refresh_minutes=int(source.get("expert_refresh_minutes", current.expert_refresh_minutes)),
                odds_quota_remaining_floor=int(source.get("odds_quota_remaining_floor", current.odds_quota_remaining_floor)),
                news_quota_remaining_floor=int(source.get("news_quota_remaining_floor", current.news_quota_remaining_floor)),
            ),
        )
    return config
