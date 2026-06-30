"""Generic deterministic entity alias registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Iterable, Mapping, Protocol

from worldcup_predictions.entities.countries import ResolvedEntity, normalize_entity_text


@dataclass(frozen=True)
class EntityAlias:
    """One canonical non-country entity with localized aliases."""

    entity_type: str
    canonical_id: str
    names: Mapping[str, str]
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    ambiguous_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EntityAlias":
        return cls(
            entity_type=str(data["entity_type"]),
            canonical_id=str(data["canonical_id"]),
            names=dict(data.get("names", {})),
            aliases=_locale_tuple_mapping(data.get("aliases", {})),
            ambiguous_aliases=_locale_tuple_mapping(data.get("ambiguous_aliases", {})),
        )


@dataclass(frozen=True)
class EntityTextSpan:
    """A text span that may be passed through the deterministic registry."""

    text: str
    start: int
    end: int
    label: str | None = None
    source: str = "deterministic_alias"


class EntitySpanDetector(Protocol):
    """Optional NLP span detector protocol."""

    def detect(self, text: str) -> list[EntityTextSpan]:
        ...


class GenericEntityRegistry:
    """Resolve non-country football aliases into canonical ids."""

    def __init__(self, entities: Iterable[EntityAlias]) -> None:
        self._entities = {(entity.entity_type, entity.canonical_id): entity for entity in entities}
        self._aliases: dict[str, list[tuple[EntityAlias, str, str, bool]]] = {}
        for entity in self._entities.values():
            self._index_entity(entity)

    @property
    def entities(self) -> Mapping[tuple[str, str], EntityAlias]:
        return self._entities

    def resolve(self, text: str, *, locale: str | None = None, entity_type: str | None = None) -> ResolvedEntity | None:
        normalized = normalize_entity_text(text)
        if not normalized:
            return None
        matches = list(self._aliases.get(normalized, []))
        if entity_type:
            matches = [match for match in matches if match[0].entity_type == entity_type]
        if locale:
            locale_matches = [match for match in matches if match[2] == locale]
            matches = locale_matches or matches
        if not matches:
            return None
        if any(is_ambiguous for _, _, _, is_ambiguous in matches):
            return _ambiguous_result(text, matches)
        ids = sorted({(entity.entity_type, entity.canonical_id) for entity, _, _, _ in matches})
        if len(ids) != 1:
            return _ambiguous_result(text, matches)
        entity, alias, matched_locale, _is_ambiguous = sorted(
            matches,
            key=lambda match: (match[2] != locale if locale else True, match[1], match[0].canonical_id),
        )[0]
        return ResolvedEntity(
            entity_type=entity.entity_type,
            canonical_id=entity.canonical_id,
            matched_text=text,
            matched_alias=alias,
            matched_locale=matched_locale,
            confidence=1.0,
            method="alias",
        )

    def detect_aliases(self, text: str, *, locale: str | None = None, entity_type: str | None = None) -> list[ResolvedEntity]:
        """Return resolved deterministic alias mentions from free text."""

        normalized = f" {normalize_entity_text(text)} "
        resolved: list[ResolvedEntity] = []
        seen: set[tuple[str, str, str]] = set()
        for alias, matches in sorted(self._aliases.items(), key=lambda item: (-len(item[0]), item[0])):
            if f" {alias} " not in normalized:
                continue
            candidate_matches = matches
            if entity_type:
                candidate_matches = [match for match in candidate_matches if match[0].entity_type == entity_type]
            if not candidate_matches:
                continue
            result = self.resolve(alias, locale=locale, entity_type=entity_type)
            if result is None:
                continue
            key = (result.entity_type, result.canonical_id or ",".join(result.candidates), result.matched_alias or alias)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(result)
        return resolved

    def resolve_spans(self, spans: Iterable[EntityTextSpan], *, locale: str | None = None) -> list[ResolvedEntity]:
        resolved = []
        for span in spans:
            result = self.resolve(span.text, locale=locale)
            if result is not None:
                resolved.append(result)
        return resolved

    def _index_entity(self, entity: EntityAlias) -> None:
        for locale, name in entity.names.items():
            self._add_alias(name, entity, locale, is_ambiguous=False)
        for locale, aliases in entity.aliases.items():
            for alias in aliases:
                self._add_alias(alias, entity, locale, is_ambiguous=False)
        for locale, aliases in entity.ambiguous_aliases.items():
            for alias in aliases:
                self._add_alias(alias, entity, locale, is_ambiguous=True)

    def _add_alias(self, alias: str, entity: EntityAlias, locale: str, *, is_ambiguous: bool) -> None:
        normalized = normalize_entity_text(alias)
        if not normalized:
            return
        self._aliases.setdefault(normalized, []).append((entity, alias, locale, is_ambiguous))


class RegexEntitySpanDetector:
    """Span detector backed by the deterministic alias registry."""

    def __init__(self, registry: GenericEntityRegistry) -> None:
        self.registry = registry

    def detect(self, text: str) -> list[EntityTextSpan]:
        spans: list[EntityTextSpan] = []
        folded = normalize_entity_text(text)
        for resolved in self.registry.detect_aliases(folded):
            alias = resolved.matched_alias or resolved.matched_text
            pattern = re.compile(rf"\b{re.escape(normalize_entity_text(alias))}\b")
            match = pattern.search(folded)
            if match:
                spans.append(EntityTextSpan(alias, match.start(), match.end(), label=resolved.entity_type))
        return spans


class SpacyEntitySpanDetector:
    """Optional spaCy wrapper that proposes spans but never assigns ids."""

    def __init__(self, model_name: str = "en_core_web_sm") -> None:
        try:
            import spacy  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency.
            raise RuntimeError("spaCy is optional and is not installed in this environment.") from exc
        self._nlp = spacy.load(model_name)

    def detect(self, text: str) -> list[EntityTextSpan]:
        doc = self._nlp(text)
        spans = [EntityTextSpan(ent.text, ent.start_char, ent.end_char, label=ent.label_, source="spacy") for ent in doc.ents]
        spans.extend(EntityTextSpan(chunk.text, chunk.start_char, chunk.end_char, label="NOUN_CHUNK", source="spacy") for chunk in doc.noun_chunks)
        return spans


def load_entity_registry() -> GenericEntityRegistry:
    with resources.files("worldcup_predictions.resources").joinpath("entity_aliases.json").open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return GenericEntityRegistry(EntityAlias.from_dict(item) for item in payload["entities"])


def _locale_tuple_mapping(data: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    return {str(locale): tuple(str(alias) for alias in aliases) for locale, aliases in dict(data).items()}


def _ambiguous_result(text: str, matches: list[tuple[EntityAlias, str, str, bool]]) -> ResolvedEntity:
    candidates = tuple(sorted(f"{entity.entity_type}:{entity.canonical_id}" for entity, _, _, _ in matches))
    entity, alias, locale, _is_ambiguous = sorted(matches, key=lambda match: (match[0].entity_type, match[0].canonical_id, match[1]))[0]
    return ResolvedEntity(
        entity_type=entity.entity_type,
        canonical_id=None,
        matched_text=text,
        matched_alias=alias,
        matched_locale=locale,
        confidence=0.0,
        method="ambiguous",
        candidates=candidates,
    )

