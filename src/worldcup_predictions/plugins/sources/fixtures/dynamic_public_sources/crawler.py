"""HTML parsing helpers for dynamic public-source discovery.

Scrapy is used only for selector parsing when it is installed. Fetching stays in
``SourceRuntime`` so ledger checks, HTTP cache validators, and diagnostics remain
centralized.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable

from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.slots import has_defined_teams


@dataclass(frozen=True)
class DiscoveredLink:
    """One same-domain public page that may contain fixture facts."""

    url: str
    label: str
    reason: str


def scrapy_selector_available() -> bool:
    """Return whether Scrapy's selector parser can be used."""

    try:
        from scrapy import Selector  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def html_to_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def page_title(value: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", value, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())


def page_description(value: str) -> str:
    match = re.search(
        r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(
            r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']description["\']',
            value,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if not match:
        return ""
    return " ".join(html.unescape(match.group(1)).split())


def discover_candidate_links(
    page: str,
    *,
    base_url: str,
    state: TournamentState,
    limit: int,
) -> list[DiscoveredLink]:
    """Return same-domain links likely to mention current tournament fixtures."""

    if limit <= 0:
        return []
    base = urllib.parse.urlparse(base_url)
    if not base.scheme or not base.netloc:
        return []
    terms = _fixture_terms(state)
    candidates: list[DiscoveredLink] = []
    seen: set[str] = set()
    for href, label in _links(page):
        url = _normalized_url(href, base_url)
        if not url or url in seen:
            continue
        seen.add(url)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.casefold().removeprefix("www.") != base.netloc.casefold().removeprefix("www."):
            continue
        reason = _link_reason(url, label, terms)
        if not reason:
            continue
        candidates.append(DiscoveredLink(url=url, label=label[:160], reason=reason))
        if len(candidates) >= limit:
            break
    return candidates


def _links(page: str) -> Iterable[tuple[str, str]]:
    try:
        from scrapy import Selector
    except ModuleNotFoundError:
        yield from _regex_links(page)
        return
    selector = Selector(text=page)
    for node in selector.css("a"):
        href = str(node.attrib.get("href") or "").strip()
        label = " ".join(value.strip() for value in node.css("::text").getall() if value.strip())
        if href:
            yield href, label


def _regex_links(page: str) -> Iterable[tuple[str, str]]:
    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page, flags=re.IGNORECASE | re.DOTALL):
        href = html.unescape(match.group(1)).strip()
        label = html_to_text(match.group(2))[:160]
        if href:
            yield href, label


def _normalized_url(href: str, base_url: str) -> str:
    joined = urllib.parse.urljoin(base_url, href.strip())
    parsed = urllib.parse.urlparse(joined)
    if not parsed.scheme or not parsed.netloc:
        return ""
    parsed = parsed._replace(fragment="")
    return urllib.parse.urlunparse(parsed)


def _fixture_terms(state: TournamentState) -> tuple[str, ...]:
    terms: set[str] = {
        "world cup",
        "worldcup",
        "world-cup",
        "fifa",
        "score",
        "scores",
        "fixture",
        "fixtures",
        "match",
        "matches",
        "odds",
        "prediction",
        "preview",
        "report",
    }
    for fixture in state.fixtures:
        if not has_defined_teams(fixture):
            continue
        for team in (fixture.home_team, fixture.away_team):
            if team.name:
                terms.add(team.name.casefold())
            if team.fifa_code:
                terms.add(team.fifa_code.casefold())
    return tuple(sorted(terms, key=len, reverse=True))


def _link_reason(url: str, label: str, terms: tuple[str, ...]) -> str:
    haystack = f"{url} {label}".casefold()
    for term in terms:
        if term and term in haystack:
            return f"matched_term:{term}"
    return ""
