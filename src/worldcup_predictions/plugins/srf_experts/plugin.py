"""SRF public expert prediction plugin."""

from __future__ import annotations

import datetime as dt
import re
import urllib.error
from collections import defaultdict
from typing import Any

from worldcup_predictions.core.constants import (
    SIGNAL_CONFIDENCE_SRF_EXPERT_BASE,
    SIGNAL_CONFIDENCE_SRF_EXPERT_MAX,
    SIGNAL_CONFIDENCE_SRF_EXPERT_PER_PICK,
    SIGNAL_WEIGHT_SRF_EXPERT,
    SOURCE_SRF_EXPERTS,
    SRF_EXPERT_URLS,
)
from worldcup_predictions.core.contracts import Diagnostic, ScoreTip, Signal, parse_utc_datetime
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.datasets import SRF_EXPERT_PREDICTIONS as SRF_EXPERT_PREDICTIONS_DATASET
from worldcup_predictions.core.datasets import SRF_EXPERT_PERFORMANCE
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import EXPERT_HDA_PROBABILITIES
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash, utc_now
from worldcup_predictions.plugins.provider_optimizers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.tournament.contracts import FixtureRecord


# SRF hides tips until each match's countdown expires, so zero-pick pages are
# expected pregame; re-probe them slowly instead of every expert_refresh cycle.
ZERO_PICK_REFETCH_BACKOFF = dt.timedelta(hours=6)


class SrfExpertsPlugin(BasePlugin):
    """Fetch SRF expert pages and emit fixture-level consensus signals."""

    id = "srf_experts"
    version = "0.1.0"
    priority = 340
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch SRF public expert pages and emit conservative expert consensus signals.",
        datasets_read=(SRF_EXPERT_PREDICTIONS_DATASET,),
        datasets_written=(SRF_EXPERT_PREDICTIONS_DATASET, SRF_EXPERT_PERFORMANCE, EXTRACTION_DIAGNOSTICS),
        signals_emitted=(EXPERT_HDA_PROBABILITIES,),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Public expert pages are refetched at most every 15 minutes while fixtures are open; pages without extractable picks back off for six hours because SRF hides tips until kickoff.",
        ),
        confidence_policy="Expert consensus confidence rises with extracted expert count and is capped below market weight.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("SRF experts")
        state = runtime.tournament_state()

        diagnostics: list[Diagnostic] = []
        written = 0
        open_fixtures = state.open_fixtures()
        if open_fixtures:
            for expert_id, url in SRF_EXPERT_URLS.items():
                result = self._fetch_expert(runtime, open_fixtures, expert_id=expert_id, url=url)
                diagnostics.extend(result.diagnostics)
                written += int(result.metadata.get("written_rows") or 0)
        else:
            diagnostics.append(
                runtime.diagnostic(
                    level="info",
                    message="No open fixtures are available; SRF expert fetch was skipped.",
                )
            )
        rows = runtime.read_latest(SRF_EXPERT_PREDICTIONS_DATASET)
        performance_rows = srf_expert_performance_rows(rows, state)
        performance_count = runtime.write_records(SRF_EXPERT_PERFORMANCE, performance_rows)
        weights = expert_weights_from_performance(performance_rows)
        signals = srf_expert_signals_from_rows(rows, weights=weights)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                runtime.structured_artifact(SRF_EXPERT_PREDICTIONS_DATASET, rows_written=written, signals=len(signals)),
                runtime.structured_artifact(SRF_EXPERT_PERFORMANCE, rows_written=performance_count),
            ],
            diagnostics=diagnostics,
            metadata={"written_rows": written, "performance_rows": performance_count, "signals": len(signals)},
        )

    def _fetch_expert(self, runtime: SourceRuntime, fixtures: list[FixtureRecord], *, expert_id: str, url: str) -> PluginResult:
        request = SourceRequest(
            source=SOURCE_SRF_EXPERTS,
            endpoint=url,
            purpose="expert_predictions",
            params={"expert": expert_id},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.expert_refresh_minutes),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("SRF expert", decision.reason, metadata={"expert": expert_id, **decision.metadata})
        try:
            html, _headers = runtime.fetch_text(url)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="SRF expert fetch failed; stored expert rows will be used.",
                        metadata={"expert": expert_id, "error": str(exc)},
                    )
                ],
            )
        rows = parse_srf_expert_rows(html, expert_id=expert_id, expert_url=url, fixtures=fixtures)
        # SRF hides everyone's tips until each match's countdown expires, so
        # pregame expert pages usually contain no picks at all. A zero-pick
        # page is re-probed on a slow cadence instead of every quarter hour,
        # and the miss is recorded as a structured extraction diagnostic.
        runtime.record_success(
            request,
            message="Fetched SRF expert page.",
            metadata={"rows": len(rows)},
            next_safe_fetch_at=None if rows else normalize_datetime(utc_now() + ZERO_PICK_REFETCH_BACKOFF),
        )
        count = runtime.write_records(SRF_EXPERT_PREDICTIONS_DATASET, rows)
        diagnostics = []
        if not rows:
            runtime.write_records(
                EXTRACTION_DIAGNOSTICS,
                [
                    extraction_diagnostic_row(
                        source=SOURCE_SRF_EXPERTS,
                        extractor="parse_srf_expert_rows",
                        status="rejected",
                        reason="no_expert_picks_on_page",
                        source_url=url,
                        metadata={"expert": expert_id, "open_fixtures": len(fixtures)},
                    )
                ],
            )
            diagnostics.append(
                runtime.diagnostic(
                    level="info",
                    message="No SRF expert predictions were extracted; SRF hides tips until kickoff.",
                    metadata={"expert": expert_id},
                )
            )
        return runtime.result(diagnostics=diagnostics, metadata={"written_rows": count})


def parse_srf_expert_rows(
    html: str,
    *,
    expert_id: str,
    expert_url: str,
    fixtures: list[FixtureRecord],
) -> list[dict[str, Any]]:
    text = _html_to_text(html)
    observed_at = normalize_datetime(utc_now()) or ""
    rows = []
    for fixture in fixtures:
        home_index = text.casefold().find(fixture.home_team.name.casefold())
        away_index = text.casefold().find(fixture.away_team.name.casefold())
        if home_index < 0 or away_index < 0:
            continue
        start = max(0, min(home_index, away_index) - 200)
        end = min(len(text), max(home_index, away_index) + 400)
        snippet = text[start:end]
        score = _score_from_snippet(snippet)
        if score is None:
            continue
        home_score, away_score = score
        rows.append(
            {
                "record_key": stable_hash({"fixture_key": fixture.key, "expert": expert_id, "score": score}),
                "fixture_key": fixture.key,
                "event_date": fixture.event_date,
                "home_team": fixture.home_team.name,
                "away_team": fixture.away_team.name,
                "home_fifa_code": fixture.home_team.fifa_code,
                "away_fifa_code": fixture.away_team.fifa_code,
                "expert_id": expert_id,
                "expert_url": expert_url,
                "tip_home": home_score,
                "tip_away": away_score,
                "observed_at_utc": observed_at,
                "metadata": {"snippet": snippet[:300]},
            }
        )
    return rows


def srf_expert_signals_from_rows(rows: list[dict[str, Any]], *, weights: dict[str, float] | None = None) -> list[Signal]:
    weights = weights or {}
    by_fixture: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        expert_id = str(row.get("expert_id") or "")
        if not fixture_key or not expert_id:
            continue
        current = by_fixture.setdefault(fixture_key, {}).get(expert_id)
        observed = str(row.get("observed_at_utc") or row.get("_record", {}).get("observed_at_utc") or "")
        current_observed = str(current.get("observed_at_utc") or current.get("_record", {}).get("observed_at_utc") or "") if current else ""
        if current is None or observed >= current_observed:
            by_fixture[fixture_key][expert_id] = row
    signals = []
    for fixture_key, expert_rows in by_fixture.items():
        picks = list(expert_rows.values())
        if not picks:
            continue
        home = draw = away = 0.0
        total_goals = 0.0
        total_weight = 0.0
        for row in picks:
            expert_weight = float(weights.get(str(row.get("expert_id") or ""), 1.0))
            tip_home = int(row.get("tip_home") or 0)
            tip_away = int(row.get("tip_away") or 0)
            total_goals += (tip_home + tip_away) * expert_weight
            total_weight += expert_weight
            if tip_home > tip_away:
                home += expert_weight
            elif tip_home < tip_away:
                away += expert_weight
            else:
                draw += expert_weight
        count = len(picks)
        if total_weight <= 0:
            total_weight = 1.0
        prob_home = home / total_weight
        prob_draw = draw / total_weight
        prob_away = away / total_weight
        signals.append(
            Signal(
                name="expert_hda_probabilities",
                source=SOURCE_SRF_EXPERTS,
                fixture_key=fixture_key,
                value=prob_home,
                weight=SIGNAL_WEIGHT_SRF_EXPERT,
                confidence=min(SIGNAL_CONFIDENCE_SRF_EXPERT_MAX, SIGNAL_CONFIDENCE_SRF_EXPERT_BASE + SIGNAL_CONFIDENCE_SRF_EXPERT_PER_PICK * count),
                rationale="SRF public expert consensus, weighted by each expert's tournament accuracy.",
                metadata={
                    "prob_home": prob_home,
                    "prob_draw": prob_draw,
                    "prob_away": prob_away,
                    "expert_count": count,
                    "average_total_goals": total_goals / total_weight,
                    "performance_weighted": bool(weights),
                    "picks": [
                        {
                            "expert_id": row.get("expert_id"),
                            "tip_home": row.get("tip_home"),
                            "tip_away": row.get("tip_away"),
                            "weight": float(weights.get(str(row.get("expert_id") or ""), 1.0)),
                        }
                        for row in picks
                    ],
                },
            )
        )
    return signals


def expert_weights_from_performance(performance_rows: list[dict[str, Any]]) -> dict[str, float]:
    """Derive per-expert consensus weights from their finished-match SRF points.

    Experts who beat the field mean are up-weighted (capped at 1.35) and those below it
    down-weighted (floored at 0.75), shrunk by an evidence factor so that with few
    finished matches all experts stay near equal weight. Returns ``{}`` (equal weight)
    until results exist.
    """

    points_by_expert: dict[str, list[float]] = defaultdict(list)
    for row in performance_rows:
        expert_id = str(row.get("expert_id") or "")
        if not expert_id:
            continue
        try:
            points_by_expert[expert_id].append(float(row.get("points") or 0.0))
        except (TypeError, ValueError):
            continue
    if not points_by_expert:
        return {}
    averages = {expert_id: sum(points) / len(points) for expert_id, points in points_by_expert.items()}
    field_mean = sum(averages.values()) / len(averages)
    weights: dict[str, float] = {}
    for expert_id, points in points_by_expert.items():
        matches = len(points)
        evidence = matches / (matches + 4)
        relative = (averages[expert_id] - field_mean) / field_mean if field_mean > 0 else 0.0
        weights[expert_id] = _clamp(1 + relative * evidence * 0.5, 0.75, 1.35)
    return weights


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def srf_expert_performance_rows(rows: list[dict[str, Any]], state) -> list[dict[str, Any]]:
    results = {result.fixture_key: result for result in state.results}
    fixtures = {fixture.key: fixture for fixture in state.fixtures}
    latest_by_expert_fixture: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        expert_id = str(row.get("expert_id") or "")
        if not fixture_key or not expert_id:
            continue
        key = (expert_id, fixture_key)
        observed = str(row.get("observed_at_utc") or row.get("_record", {}).get("observed_at_utc") or "")
        current = latest_by_expert_fixture.get(key)
        current_observed = str(current.get("observed_at_utc") or current.get("_record", {}).get("observed_at_utc") or "") if current else ""
        if current is None or observed >= current_observed:
            latest_by_expert_fixture[key] = row
    output = []
    for (expert_id, fixture_key), row in sorted(latest_by_expert_fixture.items()):
        result = results.get(fixture_key)
        fixture = fixtures.get(fixture_key)
        if result is None or fixture is None:
            continue
        try:
            tip = ScoreTip(int(row.get("tip_home")), int(row.get("tip_away")))
        except (TypeError, ValueError):
            continue
        points = srf_rules_for_fixture(fixture.to_fixture()).points_for_tip(tip, result.score)
        output.append(
            {
                "record_key": f"{expert_id}:{fixture_key}",
                "expert_id": expert_id,
                "fixture_key": fixture_key,
                "event_date": fixture.event_date,
                "home_team": fixture.home_team.name,
                "away_team": fixture.away_team.name,
                "tip": tip.as_text(),
                "actual": result.score.as_text(),
                "points": points,
                "correct_exact": tip == result.score,
                "metadata": {
                    "expert_url": row.get("expert_url"),
                    "observed_at_utc": row.get("observed_at_utc") or row.get("_record", {}).get("observed_at_utc"),
                },
            }
        )
    return output


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _score_from_snippet(snippet: str) -> tuple[int, int] | None:
    match = re.search(r"\b(\d{1,2})\s*[:\-]\s*(\d{1,2})\b", snippet)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))
