"""Deterministic i18n country resolver.

FIFA country codes are the canonical team identifiers. spaCy or other NLP tools
may detect candidate spans later, but identity assignment belongs here.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Iterable, Mapping


ENTITY_TYPE = "country"


def normalize_entity_text(value: str) -> str:
    """Normalize names and aliases for deterministic matching."""

    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class Country:
    """A participating country keyed by its FIFA code."""

    fifa_code: str
    names: Mapping[str, str]
    codes: tuple[str, ...] = field(default_factory=tuple)
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    ambiguous_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    source_aliases: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Country":
        return cls(
            fifa_code=str(data["fifa_code"]).upper(),
            names=dict(data.get("names", {})),
            codes=tuple(str(code).upper() for code in data.get("codes", [])),
            aliases=_locale_tuple_mapping(data.get("aliases", {})),
            ambiguous_aliases=_locale_tuple_mapping(data.get("ambiguous_aliases", {})),
            source_aliases={
                str(source): _locale_tuple_mapping(locale_aliases)
                for source, locale_aliases in dict(data.get("source_aliases", {})).items()
            },
        )


@dataclass(frozen=True)
class AliasMatch:
    """One registry alias matched against input text."""

    country_code: str
    alias: str
    locale: str | None
    method: str
    source: str | None = None
    is_ambiguous: bool = False


@dataclass(frozen=True)
class ResolvedEntity:
    """Resolver output for a country/team mention."""

    entity_type: str
    canonical_id: str | None
    matched_text: str
    matched_alias: str | None
    matched_locale: str | None
    confidence: float
    method: str
    candidates: tuple[str, ...] = field(default_factory=tuple)
    source: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.canonical_id is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.canonical_id is None and bool(self.candidates)


class CountryRegistry:
    """Resolve country mentions to canonical FIFA country codes."""

    def __init__(self, countries: Iterable[Country]) -> None:
        self._countries = {country.fifa_code: country for country in countries}
        self._codes: dict[str, str] = {}
        self._aliases: dict[str, list[AliasMatch]] = {}
        for country in self._countries.values():
            self._index_country(country)

    @property
    def countries(self) -> Mapping[str, Country]:
        return self._countries

    def get(self, fifa_code: str) -> Country:
        return self._countries[fifa_code.upper()]

    def resolve(self, text: str, *, locale: str | None = None, source: str | None = None) -> ResolvedEntity | None:
        """Resolve a country mention.

        Returns None for unknown text. Returns an unresolved ResolvedEntity with
        candidates for ambiguous aliases.
        """

        stripped = text.strip()
        if not stripped:
            return None

        code_match = self._codes.get(stripped.upper())
        if code_match:
            return ResolvedEntity(
                entity_type=ENTITY_TYPE,
                canonical_id=code_match,
                matched_text=text,
                matched_alias=stripped.upper(),
                matched_locale=None,
                confidence=1.0,
                method="code",
            )

        normalized = normalize_entity_text(stripped)
        matches = list(self._aliases.get(normalized, []))
        if not matches:
            return None

        matches = self._prefer_source(matches, source)
        matches = self._prefer_locale(matches, locale)

        if any(match.is_ambiguous for match in matches):
            return _ambiguous_result(text, matches)

        codes = sorted({match.country_code for match in matches})
        if len(codes) != 1:
            return _ambiguous_result(text, matches)

        best = self._best_match(matches, locale, source)
        return ResolvedEntity(
            entity_type=ENTITY_TYPE,
            canonical_id=best.country_code,
            matched_text=text,
            matched_alias=best.alias,
            matched_locale=best.locale,
            confidence=1.0,
            method=best.method,
            source=best.source,
        )

    def _index_country(self, country: Country) -> None:
        for code in {country.fifa_code, *country.codes}:
            self._codes[code.upper()] = country.fifa_code

        for locale, name in country.names.items():
            self._add_alias(name, AliasMatch(country.fifa_code, name, locale, "name"))
        for locale, aliases in country.aliases.items():
            for alias in aliases:
                self._add_alias(alias, AliasMatch(country.fifa_code, alias, locale, "alias"))
        for locale, aliases in country.ambiguous_aliases.items():
            for alias in aliases:
                self._add_alias(alias, AliasMatch(country.fifa_code, alias, locale, "ambiguous_alias", is_ambiguous=True))
        for source, locale_aliases in country.source_aliases.items():
            for locale, aliases in locale_aliases.items():
                for alias in aliases:
                    self._add_alias(alias, AliasMatch(country.fifa_code, alias, locale, "source_alias", source=source))

    def _add_alias(self, alias: str, match: AliasMatch) -> None:
        normalized = normalize_entity_text(alias)
        if not normalized:
            return
        self._aliases.setdefault(normalized, []).append(match)

    @staticmethod
    def _prefer_source(matches: list[AliasMatch], source: str | None) -> list[AliasMatch]:
        if not source:
            return matches
        source_matches = [match for match in matches if match.source == source]
        return source_matches or matches

    @staticmethod
    def _prefer_locale(matches: list[AliasMatch], locale: str | None) -> list[AliasMatch]:
        if not locale:
            return matches
        locale_matches = [match for match in matches if match.locale == locale]
        return locale_matches or matches

    @staticmethod
    def _best_match(matches: list[AliasMatch], locale: str | None, source: str | None) -> AliasMatch:
        return sorted(
            matches,
            key=lambda match: (
                match.source != source if source else True,
                match.locale != locale if locale else True,
                _method_rank(match.method),
                match.alias,
            ),
        )[0]


def load_country_registry() -> CountryRegistry:
    with resources.files("worldcup_predictions.resources").joinpath("countries.json").open(encoding="utf-8") as handle:
        payload = json.load(handle)
    countries = [Country.from_dict(item) for item in payload["countries"]]
    return CountryRegistry(countries)


def _locale_tuple_mapping(data: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    return {str(locale): tuple(str(alias) for alias in aliases) for locale, aliases in dict(data).items()}


def _method_rank(method: str) -> int:
    return {
        "source_alias": 0,
        "name": 1,
        "alias": 2,
        "ambiguous_alias": 99,
    }.get(method, 50)


def _ambiguous_result(text: str, matches: list[AliasMatch]) -> ResolvedEntity:
    candidates = tuple(sorted({match.country_code for match in matches}))
    best = sorted(matches, key=lambda match: (match.alias, match.locale or "", match.country_code))[0]
    return ResolvedEntity(
        entity_type=ENTITY_TYPE,
        canonical_id=None,
        matched_text=text,
        matched_alias=best.alias,
        matched_locale=best.locale,
        confidence=0.0,
        method="ambiguous",
        candidates=candidates,
        source=best.source,
    )

