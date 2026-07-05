"""Deterministic claim extraction from dynamic public pages."""

from __future__ import annotations

import re
from typing import Any, Mapping

from worldcup_predictions.core.constants import (
    DYNAMIC_SOURCE_INITIAL_REPUTATION,
    DYNAMIC_SOURCE_MARKET_MIN_CONFIDENCE,
    SOURCE_DYNAMIC_PUBLIC,
)
from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.source_reliability import assess_source_reliability
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.crawler import (
    html_to_text,
    page_description,
    page_title,
)
from worldcup_predictions.storage.ledger import normalize_datetime, stable_hash, utc_now
from worldcup_predictions.tournament import FixtureRecord, TournamentState
from worldcup_predictions.tournament.slots import has_defined_teams


def extract_claims_from_page(
    page: str,
    *,
    state: TournamentState,
    source_url: str,
    domain: str,
    source_name: str,
    reputation_by_type: Mapping[tuple[str, str], float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract public claims, market observations, and extraction diagnostics."""

    text = html_to_text(page)
    title = page_title(page) or source_name
    description = page_description(page)
    observed_at = normalize_datetime(utc_now()) or ""
    reputation_by_type = reputation_by_type or {}
    claims: list[dict[str, Any]] = []
    market_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for fixture in state.fixtures:
        if not has_defined_teams(fixture):
            continue
        if not _mentions_fixture(text, fixture):
            continue
        window = _fixture_window(text, fixture)
        claims.append(
            _claim_row(
                claim_type="fixture_mention",
                fixture=fixture,
                source_url=source_url,
                domain=domain,
                source_name=source_name,
                title=title,
                description=description,
                observed_at=observed_at,
                value={"mentioned": True},
                value_signature="mentioned",
                extraction_confidence=0.35,
                reputation_by_type=reputation_by_type,
                metadata={"extractor": "dynamic_fixture_mention_v1"},
            )
        )
        diagnostics.append(
            extraction_diagnostic_row(
                source=SOURCE_DYNAMIC_PUBLIC,
                extractor="dynamic_fixture_mention_v1",
                status="accepted",
                reason="fixture_mentioned",
                fixture_key=fixture.key,
                source_name=source_name,
                source_url=source_url,
                title=title,
                metadata={"domain": domain},
            )
        )

        score, score_confidence, score_reason = _nearby_final_score(window, fixture)
        if score is not None:
            claims.append(
                _claim_row(
                    claim_type="result",
                    fixture=fixture,
                    source_url=source_url,
                    domain=domain,
                    source_name=source_name,
                    title=title,
                    description=description,
                    observed_at=observed_at,
                    value={"home_score": score.home, "away_score": score.away},
                    value_signature=score.as_text(),
                    extraction_confidence=score_confidence,
                    reputation_by_type=reputation_by_type,
                    metadata={"extractor": "dynamic_result_score_v1", "reason": score_reason},
                )
            )
            diagnostics.append(
                extraction_diagnostic_row(
                    source=SOURCE_DYNAMIC_PUBLIC,
                    extractor="dynamic_result_score_v1",
                    status="accepted",
                    reason=score_reason,
                    fixture_key=fixture.key,
                    phase="postgame",
                    source_name=source_name,
                    source_url=source_url,
                    title=title,
                    metadata={"domain": domain, "score": score.as_text()},
                )
            )

        market_row = _market_observation_row(
            window,
            fixture=fixture,
            source_url=source_url,
            domain=domain,
            source_name=source_name,
            observed_at=observed_at,
            reputation_by_type=reputation_by_type,
        )
        if market_row is not None:
            market_rows.append(market_row)
            claims.append(
                _claim_row(
                    claim_type="market_observation",
                    fixture=fixture,
                    source_url=source_url,
                    domain=domain,
                    source_name=source_name,
                    title=title,
                    description=description,
                    observed_at=observed_at,
                    value={
                        "prob_home": market_row.get("prob_home"),
                        "prob_draw": market_row.get("prob_draw"),
                        "prob_away": market_row.get("prob_away"),
                        "total_goals": market_row.get("total_goals"),
                    },
                    value_signature=_market_signature(market_row),
                    extraction_confidence=float(market_row.get("confidence") or 0.0),
                    reputation_by_type=reputation_by_type,
                    metadata={"extractor": "dynamic_market_observation_v1"},
                )
            )
            diagnostics.append(
                extraction_diagnostic_row(
                    source=SOURCE_DYNAMIC_PUBLIC,
                    extractor="dynamic_market_observation_v1",
                    status="accepted",
                    reason="market_observation_extracted",
                    fixture_key=fixture.key,
                    phase="pregame",
                    source_name=source_name,
                    source_url=source_url,
                    title=title,
                    metadata={"domain": domain, "confidence": market_row.get("confidence")},
                )
            )
    return claims, market_rows, diagnostics


def _claim_row(
    *,
    claim_type: str,
    fixture: FixtureRecord,
    source_url: str,
    domain: str,
    source_name: str,
    title: str,
    description: str,
    observed_at: str,
    value: dict[str, Any],
    value_signature: str,
    extraction_confidence: float,
    reputation_by_type: Mapping[tuple[str, str], float],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    source_reputation = _source_reputation(
        domain,
        claim_type,
        source_url=source_url,
        source_name=source_name,
        reputation_by_type=reputation_by_type,
    )
    claim_id = stable_hash(
        {
            "claim_type": claim_type,
            "fixture_key": fixture.key,
            "source_url": source_url,
            "value_signature": value_signature,
        }
    )
    consensus_key = ":".join(
        part
        for part in (
            claim_type,
            fixture.key,
            value_signature if claim_type in {"result", "market_observation"} else "",
        )
        if part
    )
    return {
        "record_key": claim_id,
        "claim_id": claim_id,
        "claim_type": claim_type,
        "consensus_key": consensus_key,
        "source_url": source_url,
        "domain": domain,
        "source_name": source_name,
        "fixture_key": fixture.key,
        "event_date": fixture.event_date,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "home_fifa_code": fixture.home_team.fifa_code,
        "away_fifa_code": fixture.away_team.fifa_code,
        "value": value,
        "value_signature": value_signature,
        "extraction_confidence": _clamp(extraction_confidence, 0.0, 1.0),
        "source_reputation": source_reputation,
        "claim_weight": round(_clamp(extraction_confidence, 0.0, 1.0) * source_reputation, 6),
        "observed_at_utc": observed_at,
        "title": title,
        "description": description[:400],
        "metadata": dict(metadata),
    }


def _source_reputation(
    domain: str,
    claim_type: str,
    *,
    source_url: str,
    source_name: str,
    reputation_by_type: Mapping[tuple[str, str], float],
) -> float:
    stored = reputation_by_type.get((domain, claim_type)) or reputation_by_type.get((domain, "all"))
    profile_score = assess_source_reliability(source_url, source_name).score
    if stored is None:
        return _clamp(max(DYNAMIC_SOURCE_INITIAL_REPUTATION, profile_score), 0.10, 0.95)
    return _clamp(stored * 0.75 + profile_score * 0.25, 0.10, 0.95)


def _mentions_fixture(text: str, fixture: FixtureRecord) -> bool:
    normalized = text.casefold()
    return _mentions_team(normalized, fixture.home_team.name, fixture.home_team.fifa_code) and _mentions_team(
        normalized,
        fixture.away_team.name,
        fixture.away_team.fifa_code,
    )


def _mentions_team(text: str, name: str, fifa_code: str | None) -> bool:
    if name and name.casefold() in text:
        return True
    if fifa_code and re.search(rf"\b{re.escape(fifa_code.casefold())}\b", text):
        return True
    return False


def _fixture_window(text: str, fixture: FixtureRecord, *, radius: int = 1400) -> str:
    if len(text) <= radius * 3:
        return text
    normalized = text.casefold()
    positions: list[int] = []
    for term in (fixture.home_team.name, fixture.away_team.name, fixture.home_team.fifa_code, fixture.away_team.fifa_code):
        if not term:
            continue
        position = normalized.find(term.casefold())
        if position >= 0:
            positions.append(position)
    if not positions:
        return text[: radius * 2]
    center = min(positions)
    start = max(0, center - radius)
    end = min(len(text), max(positions) + radius)
    return text[start:end]


def _nearby_final_score(window: str, fixture: FixtureRecord) -> tuple[ScoreTip | None, float, str]:
    if not re.search(r"\b(final|full time|full-time|ft|result|ended|after penalties|aet)\b", window, flags=re.IGNORECASE):
        return None, 0.0, ""
    home_terms = _team_pattern_terms(fixture.home_team.name, fixture.home_team.fifa_code)
    away_terms = _team_pattern_terms(fixture.away_team.name, fixture.away_team.fifa_code)
    for home_term in home_terms:
        for away_term in away_terms:
            home_first = re.search(
                rf"\b(?:{home_term})\b.{{0,120}}?\b(\d{{1,2}})\s*[-:]\s*(\d{{1,2}})\b.{{0,120}}?\b(?:{away_term})\b",
                window,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if home_first:
                return ScoreTip(int(home_first.group(1)), int(home_first.group(2))), 0.84, "team_ordered_final_score"
            away_first = re.search(
                rf"\b(?:{away_term})\b.{{0,120}}?\b(\d{{1,2}})\s*[-:]\s*(\d{{1,2}})\b.{{0,120}}?\b(?:{home_term})\b",
                window,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if away_first:
                return ScoreTip(int(away_first.group(2)), int(away_first.group(1))), 0.84, "reversed_team_ordered_final_score"
    generic = re.search(r"\b(final|full time|full-time|ft|result|ended)\b.{0,120}?\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b", window, flags=re.IGNORECASE | re.DOTALL)
    if generic:
        return ScoreTip(int(generic.group(2)), int(generic.group(3))), 0.72, "generic_final_score_near_fixture"
    return None, 0.0, ""


def _market_observation_row(
    window: str,
    *,
    fixture: FixtureRecord,
    source_url: str,
    domain: str,
    source_name: str,
    observed_at: str,
    reputation_by_type: Mapping[tuple[str, str], float],
) -> dict[str, Any] | None:
    if not re.search(r"\b(odds|betting|market|bookmaker|price|prices|over|under|draw)\b", window, flags=re.IGNORECASE):
        return None
    market_window = _market_window(window)
    home_price = _price_near_team(market_window, fixture.home_team.name, fixture.home_team.fifa_code)
    away_price = _price_near_team(market_window, fixture.away_team.name, fixture.away_team.fifa_code)
    draw_price = _draw_price(market_window)
    h2h = _no_vig_three_way(home_price, draw_price, away_price)
    total_line, over_probability = _total_market(market_window)
    if h2h is None and total_line is None:
        return None
    source_reputation = _source_reputation(
        domain,
        "market_observation",
        source_url=source_url,
        source_name=source_name,
        reputation_by_type=reputation_by_type,
    )
    confidence = _clamp(max(0.0, source_reputation - 0.05) + (0.12 if h2h is not None and total_line is not None else 0.0), 0.0, 0.90)
    if confidence < DYNAMIC_SOURCE_MARKET_MIN_CONFIDENCE:
        return None
    event_id = stable_hash({"source_url": source_url, "fixture_key": fixture.key, "market": "public_page"})
    return {
        "record_key": f"{fixture.key}:dynamic_public_market:{domain}:{event_id}",
        "fixture_key": fixture.key,
        "event_id": event_id,
        "event_date": fixture.event_date,
        "commence_time": fixture.event_date,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "home_fifa_code": fixture.home_team.fifa_code,
        "away_fifa_code": fixture.away_team.fifa_code,
        "observed_at_utc": observed_at,
        "domain": domain,
        "source_url": source_url,
        "source_name": source_name,
        "market_type": "public_page",
        "bookmaker_count": 1,
        "h2h_bookmaker_count": 1 if h2h is not None else 0,
        "totals_bookmaker_count": 1 if total_line is not None else 0,
        "spreads_bookmaker_count": 0,
        "prob_home": h2h[0] if h2h else None,
        "prob_draw": h2h[1] if h2h else None,
        "prob_away": h2h[2] if h2h else None,
        "h2h_margin": h2h[3] if h2h else None,
        "total_goals": total_line,
        "total_over_probability": over_probability,
        "confidence": confidence,
        "source_reputation": source_reputation,
        "metadata": {
            "source": SOURCE_DYNAMIC_PUBLIC,
            "bookmaker": domain,
            "source_url": source_url,
            "home_price": home_price,
            "draw_price": draw_price,
            "away_price": away_price,
        },
    }


def _market_window(window: str) -> str:
    match = re.search(r"\b(odds|betting|market|bookmaker|price|prices)\b", window, flags=re.IGNORECASE)
    if not match:
        return window
    return window[match.start() : match.start() + 900]


def _market_signature(row: Mapping[str, Any]) -> str:
    values = {
        "h": row.get("prob_home"),
        "d": row.get("prob_draw"),
        "a": row.get("prob_away"),
        "t": row.get("total_goals"),
    }
    return stable_hash(values)[:16]


def _team_pattern_terms(name: str, fifa_code: str | None) -> tuple[str, ...]:
    terms = [re.escape(name)]
    if fifa_code:
        terms.append(re.escape(fifa_code))
    return tuple(terms)


def _price_near_team(window: str, name: str, fifa_code: str | None) -> float | None:
    terms = _team_pattern_terms(name, fifa_code)
    for term in terms:
        for pattern in (
            rf"\b(?:{term})\b.{{0,80}}?\b(\d+\.\d+)\b",
            rf"\b(\d+\.\d+)\b.{{0,80}}?\b(?:{term})\b",
        ):
            value = _first_reasonable_price(window, pattern)
            if value is not None:
                return value
    return None


def _draw_price(window: str) -> float | None:
    for pattern in (
        r"\b(?:draw|x)\b.{0,80}?\b(\d+\.\d+)\b",
        r"\b(\d+\.\d+)\b.{0,80}?\b(?:draw|x)\b",
    ):
        value = _first_reasonable_price(window, pattern)
        if value is not None:
            return value
    return None


def _first_reasonable_price(window: str, pattern: str) -> float | None:
    for match in re.finditer(pattern, window, flags=re.IGNORECASE | re.DOTALL):
        value = _optional_float(match.group(1))
        if value is not None and 1.01 <= value <= 100.0:
            return value
    return None


def _no_vig_three_way(home: float | None, draw: float | None, away: float | None) -> tuple[float, float, float, float] | None:
    if home is None or draw is None or away is None:
        return None
    raw = [1 / home, 1 / draw, 1 / away]
    total = sum(raw)
    if total <= 0:
        return None
    return raw[0] / total, raw[1] / total, raw[2] / total, total - 1


def _total_market(window: str) -> tuple[float | None, float | None]:
    over_by_line: dict[float, float] = {}
    under_by_line: dict[float, float] = {}
    for match in re.finditer(r"\bover\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", window, flags=re.IGNORECASE):
        line = _optional_float(match.group(1))
        price = _optional_float(match.group(2))
        if line is not None and price is not None and price > 1:
            over_by_line[line] = 1 / price
    for match in re.finditer(r"\bunder\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", window, flags=re.IGNORECASE):
        line = _optional_float(match.group(1))
        price = _optional_float(match.group(2))
        if line is not None and price is not None and price > 1:
            under_by_line[line] = 1 / price
    candidates = []
    for line, over in over_by_line.items():
        under = under_by_line.get(line)
        if under is None:
            candidates.append((0.5, line, None))
            continue
        probability = over / (over + under) if over + under > 0 else None
        candidates.append((abs((probability or 0.5) - 0.5), line, probability))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _balance, line, probability = candidates[0]
    return line, probability


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
