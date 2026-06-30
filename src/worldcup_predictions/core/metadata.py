"""Plugin, signal, and dataset metadata contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PluginKind(StrEnum):
    """High-level plugin kinds used by the workflow."""

    SOURCE = "source"
    SIGNAL = "signal"
    MODEL = "model"
    PROVIDER_OPTIMIZER = "provider_optimizer"
    SIMULATOR = "simulator"
    EVALUATOR = "evaluator"
    OUTPUT = "output"
    WORKFLOW = "workflow"


@dataclass(frozen=True)
class EnvVar:
    """Environment variable required or optionally used by a plugin."""

    name: str
    required: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "description": self.description,
        }


@dataclass(frozen=True)
class QuotaPolicy:
    """Source quota behavior declared by a plugin."""

    quota_limited: bool = False
    ledger_required: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "quota_limited": self.quota_limited,
            "ledger_required": self.ledger_required,
            "description": self.description,
        }


@dataclass(frozen=True)
class SignalContract:
    """Documented model signal contract."""

    name: str
    value_type: str
    description: str
    metadata_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value_type": self.value_type,
            "description": self.description,
            "metadata_keys": list(self.metadata_keys),
        }


@dataclass(frozen=True)
class DatasetContract:
    """Lightweight structured dataset contract."""

    name: str
    description: str
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
        }


@dataclass(frozen=True)
class PluginMetadata:
    """Metadata declared by each plugin for discoverability and auditability."""

    plugin_id: str
    kind: PluginKind
    description: str
    datasets_read: tuple[str, ...] = ()
    datasets_written: tuple[str, ...] = ()
    signals_emitted: tuple[str, ...] = ()
    env_vars: tuple[EnvVar, ...] = ()
    quota_policy: QuotaPolicy = field(default_factory=QuotaPolicy)
    i18n_locales: tuple[str, ...] = ("en", "de")
    confidence_policy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "kind": self.kind.value,
            "description": self.description,
            "datasets_read": list(self.datasets_read),
            "datasets_written": list(self.datasets_written),
            "signals_emitted": list(self.signals_emitted),
            "env_vars": [env_var.to_dict() for env_var in self.env_vars],
            "quota_policy": self.quota_policy.to_dict(),
            "i18n_locales": list(self.i18n_locales),
            "confidence_policy": self.confidence_policy,
        }
