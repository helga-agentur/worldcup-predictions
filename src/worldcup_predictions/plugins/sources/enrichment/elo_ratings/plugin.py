"""eloratings.net national-team Elo rating plugin.

Fetches the World Football Elo Ratings TSV, stores per-team ratings, and emits
conservative H/D/A probability signals for open fixtures. Venues are treated as
neutral (host advantage is intentionally ignored for a 48-team World Cup where
almost every side travels), so the win expectancy uses the raw rating difference.
"""

from __future__ import annotations

import datetime as dt
import math
import urllib.error
from typing import Any, Mapping

from worldcup_predictions.core.contracts import Diagnostic, Signal
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import EXPERT_HDA_PROBABILITIES
from worldcup_predictions.model.baseline import expected_result
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.source_utils import optional_float
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, utc_now
from worldcup_predictions.tournament import TeamResolver
from worldcup_predictions.tournament.contracts import FixtureRecord


SOURCE_ELO_RATINGS = "elo_ratings"
ENDPOINT_ELO_RATINGS_WORLD = "https://www.eloratings.net/World.tsv"
ELO_RATINGS = "elo_ratings"  # registered centrally in core/datasets.py

# Elo ratings only move after matches are played, so a 12-hour refresh keeps the
# table current through every tournament matchday without hammering the site.
ELO_REFRESH_INTERVAL = dt.timedelta(hours=12)

# Matches the expert H/D/A weight cap (SIGNAL_WEIGHT_EXPERT_HDA = 0.20 in
# core/constants.py); the model clamps expert_hda_probabilities to that cap.
SIGNAL_WEIGHT_ELO_HDA = 0.20
SIGNAL_CONFIDENCE_ELO = 0.60

# Internationals draw baseline, damped as the rating gap grows: mismatched
# matches end level far less often than games between equals.
BASE_DRAW_PROBABILITY = 0.28
DRAW_DAMPING_SCALE = 600.0

# eloratings.net uses its own two-letter team codes (mostly ISO 3166-1 alpha-2,
# with quirks such as EN=England and SQ=Scotland). Map each 2026 World Cup
# participant's code to the canonical English name understood by the team
# resolver; codes outside this map are non-participants and are skipped.
ELO_CODE_TO_TEAM: dict[str, str] = {
    "DZ": "Algeria",
    "AR": "Argentina",
    "AU": "Australia",
    "AT": "Austria",
    "BE": "Belgium",
    "BA": "Bosnia and Herzegovina",
    "BR": "Brazil",
    "CA": "Canada",
    "CV": "Cape Verde",
    "CO": "Colombia",
    "HR": "Croatia",
    "CW": "Curaçao",
    "CZ": "Czech Republic",
    "CD": "DR Congo",
    "EC": "Ecuador",
    "EG": "Egypt",
    "EN": "England",
    "FR": "France",
    "DE": "Germany",
    "GH": "Ghana",
    "HT": "Haiti",
    "IR": "Iran",
    "IQ": "Iraq",
    "CI": "Ivory Coast",
    "JP": "Japan",
    "JO": "Jordan",
    "MX": "Mexico",
    "MA": "Morocco",
    "NL": "Netherlands",
    "NZ": "New Zealand",
    "NO": "Norway",
    "PA": "Panama",
    "PY": "Paraguay",
    "PT": "Portugal",
    "QA": "Qatar",
    "SA": "Saudi Arabia",
    "SQ": "Scotland",
    "SN": "Senegal",
    "ZA": "South Africa",
    "KR": "South Korea",
    "ES": "Spain",
    "SE": "Sweden",
    "CH": "Switzerland",
    "TN": "Tunisia",
    "TR": "Turkey",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
}


class EloRatingsPlugin(BasePlugin):
    """Fetch eloratings.net world ratings and emit H/D/A probability signals."""

    id = "elo_ratings"
    version = "0.1.0"
    priority = 265
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch eloratings.net national-team Elo ratings and emit neutral-venue H/D/A probability signals.",
        datasets_read=(ELO_RATINGS,),
        datasets_written=(ELO_RATINGS, EXTRACTION_DIAGNOSTICS),
        signals_emitted=(EXPERT_HDA_PROBABILITIES,),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Ratings only change after matches, so the public TSV is refetched at most every 12 hours.",
        ),
        confidence_policy="Elo H/D/A signals use a fixed conservative confidence and share the capped expert weight.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Elo ratings")

        state = runtime.tournament_state()

        fetch_result = self._fetch_ratings(runtime)
        diagnostics: list[Diagnostic] = list(fetch_result.diagnostics)
        written = int(fetch_result.metadata.get("written_rows") or 0)

        rows = runtime.read_latest(ELO_RATINGS)
        signals = elo_signals_from_rows(rows, state.open_fixtures())
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[runtime.structured_artifact(ELO_RATINGS, rows_written=written, signals=len(signals))],
            diagnostics=diagnostics,
            metadata={"written_rows": written, "signals": len(signals)},
        )

    def _fetch_ratings(self, runtime: SourceRuntime) -> PluginResult:
        request = SourceRequest(
            source=SOURCE_ELO_RATINGS,
            endpoint=ENDPOINT_ELO_RATINGS_WORLD,
            purpose="national_team_elo_ratings",
            quota_cost=0,
            min_refresh_interval=ELO_REFRESH_INTERVAL,
            quota_scope=SOURCE_ELO_RATINGS,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Elo ratings", decision.reason, metadata=decision.metadata)

        try:
            body, _headers = runtime.fetch_text(ENDPOINT_ELO_RATINGS_WORLD)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Elo ratings fetch failed; stored rating rows will be used.",
                        metadata={"error": str(exc)},
                    )
                ],
            )

        rows = parse_elo_rating_rows(body or "")
        runtime.record_success(
            request,
            message="Fetched eloratings.net world ratings.",
            metadata={"rows": len(rows)},
        )
        if not rows:
            runtime.write_records(
                EXTRACTION_DIAGNOSTICS,
                [
                    extraction_diagnostic_row(
                        source=SOURCE_ELO_RATINGS,
                        extractor="parse_elo_rating_rows",
                        status="rejected",
                        reason="no_usable_elo_ratings_in_tsv",
                        source_url=ENDPOINT_ELO_RATINGS_WORLD,
                        severity="warning",
                        metadata={"body_length": len(body or "")},
                    )
                ],
            )
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="No usable Elo ratings were parsed from the eloratings.net TSV; stored rating rows will be used.",
                        metadata={"body_length": len(body or "")},
                    )
                ],
            )
        count = runtime.write_records(ELO_RATINGS, rows)
        return runtime.result(metadata={"written_rows": count})


def parse_elo_rating_rows(tsv: str, *, resolver: TeamResolver | None = None) -> list[dict[str, Any]]:
    """Parse eloratings.net World.tsv rows into participant rating rows.

    Column 1 (0-indexed) is the world rank, column 2 the eloratings team code,
    and column 3 the current rating. Codes outside the participant map and rows
    with unparseable numbers are skipped silently (they are non-participants or
    trailing noise, not errors).
    """

    resolver = resolver or TeamResolver.default(source=SOURCE_ELO_RATINGS)
    observed_at = normalize_datetime(utc_now()) or ""
    rows: list[dict[str, Any]] = []
    for line in (tsv or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        code = parts[2].strip()
        team_name = ELO_CODE_TO_TEAM.get(code)
        if team_name is None:
            continue
        try:
            rank = int(str(parts[1]).strip())
            rating = int(round(float(str(parts[3]).strip())))
        except (TypeError, ValueError):
            continue
        team = resolver.resolve(team_name)
        if team.fifa_code is None:
            continue
        rows.append(
            {
                "record_key": team.name,
                "team": team.name,
                "fifa_code": team.fifa_code,
                "elo_code": code,
                "elo_rating": rating,
                "elo_rank": rank,
                "observed_at_utc": observed_at,
                "source": SOURCE_ELO_RATINGS,
            }
        )
    return rows


def elo_hda_probabilities(home_elo: float, away_elo: float) -> tuple[float, float, float]:
    """Split an Elo win expectancy into neutral-venue H/D/A probabilities.

    The win expectancy We = 1/(1+10^(-diff/400)) reuses the baseline model's
    ``expected_result``. The draw share starts at an internationals-appropriate
    baseline and decays with the rating gap; the remainder is split by We.
    """

    diff = float(home_elo) - float(away_elo)
    win_expectancy = expected_result(float(home_elo), float(away_elo))
    prob_draw = BASE_DRAW_PROBABILITY * math.exp(-abs(diff) / DRAW_DAMPING_SCALE)
    prob_home = (1.0 - prob_draw) * win_expectancy
    prob_away = 1.0 - prob_draw - prob_home
    return prob_home, prob_draw, prob_away


def elo_signals_from_rows(rows: list[dict[str, Any]], fixtures: list[FixtureRecord]) -> list[Signal]:
    """Emit one H/D/A probability signal per open fixture with two known ratings."""

    latest = _latest_ratings_by_team(rows)
    signals: list[Signal] = []
    for fixture in fixtures:
        home = _team_rating(latest, fixture.home_team.fifa_code, fixture.home_team.name)
        away = _team_rating(latest, fixture.away_team.fifa_code, fixture.away_team.name)
        if home is None or away is None:
            continue
        home_elo = optional_float(home.get("elo_rating"))
        away_elo = optional_float(away.get("elo_rating"))
        if home_elo is None or away_elo is None:
            continue
        prob_home, prob_draw, prob_away = elo_hda_probabilities(home_elo, away_elo)
        signals.append(
            Signal(
                name=EXPERT_HDA_PROBABILITIES,
                source=SOURCE_ELO_RATINGS,
                fixture_key=fixture.key,
                value=None,
                weight=SIGNAL_WEIGHT_ELO_HDA,
                confidence=SIGNAL_CONFIDENCE_ELO,
                rationale="World Football Elo Ratings win expectancy with a damped international draw model (neutral venue).",
                metadata={
                    "prob_home": prob_home,
                    "prob_draw": prob_draw,
                    "prob_away": prob_away,
                    "expert_count": 1,
                    "model": "elo_win_expectancy_damped_draw_v1",
                    "home_elo": home_elo,
                    "away_elo": away_elo,
                    "elo_diff": home_elo - away_elo,
                    "home_elo_rank": home.get("elo_rank"),
                    "away_elo_rank": away.get("elo_rank"),
                    "observed_at_utc": home.get("observed_at_utc"),
                },
            )
        )
    return signals


def _latest_ratings_by_team(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (str(row.get("fifa_code") or ""), str(row.get("team") or "")):
            if not key:
                continue
            current = latest.get(key)
            if current is None or _row_observed_at(row) >= _row_observed_at(current):
                latest[key] = row
    return latest


def _team_rating(latest: Mapping[str, dict[str, Any]], fifa_code: str | None, name: str) -> dict[str, Any] | None:
    if fifa_code and fifa_code in latest:
        return latest[fifa_code]
    return latest.get(name)


def _row_observed_at(row: Mapping[str, Any]) -> str:
    record = row.get("_record")
    record_observed = record.get("observed_at_utc") if isinstance(record, Mapping) else None
    return str(row.get("observed_at_utc") or record_observed or "")
