"""Team canonicalization helpers for tournament data."""

from __future__ import annotations

from dataclasses import dataclass

from worldcup_predictions.entities import CountryRegistry, load_country_registry
from worldcup_predictions.tournament.contracts import TeamRef


@dataclass
class TeamResolver:
    """Resolve source labels into stable team references."""

    registry: CountryRegistry
    locale: str | None = None
    source: str | None = None

    @classmethod
    def default(cls, *, locale: str | None = None, source: str | None = None) -> "TeamResolver":
        return cls(load_country_registry(), locale=locale, source=source)

    def resolve(self, label: str) -> TeamRef:
        normalized_label = " ".join(str(label or "").split())
        resolved = self.registry.resolve(normalized_label, locale=self.locale, source=self.source)
        if resolved and resolved.is_resolved and resolved.canonical_id:
            country = self.registry.get(resolved.canonical_id)
            display_name = country.names.get("en") or normalized_label
            return TeamRef(display_name, resolved.canonical_id)
        return TeamRef(normalized_label, None)
