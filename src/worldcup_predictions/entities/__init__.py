"""Entity registries and resolvers."""

from worldcup_predictions.entities.countries import (
    AliasMatch,
    Country,
    CountryRegistry,
    ResolvedEntity,
    load_country_registry,
    normalize_entity_text,
)
from worldcup_predictions.entities.dynamic_aliases import build_generated_alias_rows, registry_from_generated_alias_rows
from worldcup_predictions.entities.registry import (
    EntityAlias,
    EntitySpanDetector,
    EntityTextSpan,
    GenericEntityRegistry,
    RegexEntitySpanDetector,
    SpacyEntitySpanDetector,
    load_entity_registry,
)

__all__ = [
    "AliasMatch",
    "Country",
    "CountryRegistry",
    "EntityAlias",
    "EntitySpanDetector",
    "EntityTextSpan",
    "GenericEntityRegistry",
    "RegexEntitySpanDetector",
    "ResolvedEntity",
    "SpacyEntitySpanDetector",
    "load_country_registry",
    "load_entity_registry",
    "build_generated_alias_rows",
    "normalize_entity_text",
    "registry_from_generated_alias_rows",
]
