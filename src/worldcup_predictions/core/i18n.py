"""Core translation boundary.

Plugins should emit canonical ids, numeric values, and metadata. User-facing
language belongs to core/output layers through this catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "de")


@dataclass(frozen=True)
class TranslationCatalog:
    """Small key/value translation catalog with fallback semantics."""

    locale: str
    messages: dict[str, str]
    fallback: "TranslationCatalog | None" = None

    def translate(self, key: str, **values: Any) -> str:
        template = self.messages.get(key)
        if template is None and self.fallback is not None:
            template = self.fallback.messages.get(key)
        if template is None:
            template = key
        return template.format(**values)


def load_translation_catalog(locale: str | None = None) -> TranslationCatalog:
    locale = normalize_locale(locale)
    fallback = None
    if locale != DEFAULT_LOCALE:
        fallback = _load_catalog(DEFAULT_LOCALE, fallback=None)
    return _load_catalog(locale, fallback=fallback)


def normalize_locale(locale: str | None) -> str:
    normalized = (locale or DEFAULT_LOCALE).split("_", 1)[0].split("-", 1)[0].casefold()
    if normalized not in SUPPORTED_LOCALES:
        return DEFAULT_LOCALE
    return normalized


def _load_catalog(locale: str, *, fallback: TranslationCatalog | None) -> TranslationCatalog:
    resource = resources.files("worldcup_predictions.resources").joinpath("i18n", f"{locale}.json")
    with resource.open(encoding="utf-8") as handle:
        messages = json.load(handle)
    return TranslationCatalog(locale=locale, messages=dict(messages), fallback=fallback)
