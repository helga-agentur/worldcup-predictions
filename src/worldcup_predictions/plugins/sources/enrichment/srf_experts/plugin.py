"""SRF public expert prediction plugin.

The wmtippspiel.srf.ch expert pages are a Rails app that mounts React
components with HTML-escaped JSON in ``data-react-props``. Every pick is a
``ScoreBet`` component (team names, kickoff, the expert's ``picks`` pair, and
the bet state), the round navigation is a ``SelectRaceweek/index`` component,
and per-round point totals come from a ``Chart`` component. Picks are visible
pregame. A previous incarnation of this plugin scraped the rendered text of
the base page only, which contained no parseable picks and led to the plugin
being dropped; this version reads the component JSON and walks the rounds.
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import json
import re
import urllib.error
import urllib.parse
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
from worldcup_predictions.plugins.providers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.tournament import TeamResolver
from worldcup_predictions.tournament.contracts import FixtureRecord


_REACT_COMPONENT_RE = re.compile(
    r'data-react-class="([^"]+)"[^>]*data-react-props="([^"]*)"',
    re.DOTALL,
)

# Experts can revise a tip until its deadline, and revisions are trend
# information, so pages with open bets are polled on every scheduled run
# (gated only by expert_refresh_minutes). Rounds whose bets are all settled
# cannot change; rounds with no bets yet (future finals before the pairing
# resolves) are re-probed every few hours so new bets are picked up promptly.
FINISHED_ROUND_REFETCH = dt.timedelta(days=7)
EMPTY_ROUND_REFETCH = dt.timedelta(hours=6)


class SrfExpertsPlugin(BasePlugin):
    """Fetch SRF expert pages and emit fixture-level consensus signals."""

    id = "srf_experts"
    version = "0.2.0"
    priority = 340
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch SRF public expert round pages and emit conservative expert consensus signals.",
        datasets_read=(SRF_EXPERT_PREDICTIONS_DATASET,),
        datasets_written=(SRF_EXPERT_PREDICTIONS_DATASET, SRF_EXPERT_PERFORMANCE, EXTRACTION_DIAGNOSTICS),
        signals_emitted=(EXPERT_HDA_PROBABILITIES,),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Expert round pages poll hourly near tip deadlines, every six hours otherwise; settled rounds re-probe weekly.",
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
        fixtures = list(state.fixtures)
        known_round_urls = _round_urls_from_ledger(runtime)
        for expert_id, url in SRF_EXPERT_URLS.items():
            base = self._fetch_expert_page(runtime, fixtures, expert_id=expert_id, url=url)
            diagnostics.extend(base.diagnostics)
            written += int(base.metadata.get("written_rows") or 0)
            # Round pages are discovered on the base page; when that fetch is
            # freshness-skipped, previously seen round URLs from the ledger
            # keep their own cadence alive.
            round_urls = list(base.metadata.get("round_urls") or [])
            for known in known_round_urls:
                if known.startswith(f"{url}/round/") and known not in round_urls:
                    round_urls.append(known)
            for round_url in round_urls:
                result = self._fetch_expert_page(runtime, fixtures, expert_id=expert_id, url=round_url)
                diagnostics.extend(result.diagnostics)
                written += int(result.metadata.get("written_rows") or 0)

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

    def _fetch_expert_page(
        self,
        runtime: SourceRuntime,
        fixtures: list[FixtureRecord],
        *,
        expert_id: str,
        url: str,
    ) -> PluginResult:
        request = SourceRequest(
            source=SOURCE_SRF_EXPERTS,
            endpoint=url,
            purpose="expert_predictions",
            params={"expert": expert_id},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.expert_refresh_minutes),
            quota_scope=SOURCE_SRF_EXPERTS,
            rate_limit_backoff=dt.timedelta(hours=6),
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
                        metadata={"expert": expert_id, "url": url, "error": str(exc)},
                    )
                ],
            )
        bets, round_urls = parse_expert_react_components(html, base_url=url)
        rows = srf_expert_rows_from_bets(bets, expert_id=expert_id, expert_url=url, fixtures=fixtures)
        runtime.record_success(
            request,
            message="Fetched SRF expert page.",
            metadata={"bets": len(bets), "rows": len(rows)},
            next_safe_fetch_at=normalize_datetime(next_fetch) if (next_fetch := _next_expert_fetch_at(bets, now=utc_now())) else None,
        )
        count = runtime.write_records(SRF_EXPERT_PREDICTIONS_DATASET, rows)
        diagnostics = []
        if bets and not rows:
            runtime.write_records(
                EXTRACTION_DIAGNOSTICS,
                [
                    extraction_diagnostic_row(
                        source=SOURCE_SRF_EXPERTS,
                        extractor="parse_expert_react_components",
                        status="rejected",
                        reason="expert_bets_did_not_match_any_fixture",
                        source_url=url,
                        metadata={"expert": expert_id, "bets": len(bets)},
                    )
                ],
            )
            diagnostics.append(
                runtime.diagnostic(
                    level="info",
                    message="SRF expert bets could not be matched to known fixtures.",
                    metadata={"expert": expert_id, "url": url, "bets": len(bets)},
                )
            )
        return runtime.result(
            diagnostics=diagnostics,
            metadata={"written_rows": count, "round_urls": round_urls},
        )


def _round_urls_from_ledger(runtime: SourceRuntime) -> list[str]:
    reader = getattr(runtime.storage, "read_source_ledger", None)
    if not callable(reader):
        return []
    try:
        rows = reader(source=SOURCE_SRF_EXPERTS)
    except Exception:
        return []
    return sorted({str(row.get("endpoint") or "") for row in rows if "/round/" in str(row.get("endpoint") or "")})


def _next_expert_fetch_at(bets: list[dict[str, Any]], *, now: dt.datetime) -> dt.datetime | None:
    """Poll cadence for one expert page, from its bets' tip deadlines.

    ``None`` means no explicit schedule: the ledger falls back to the
    expert_refresh_minutes freshness interval, i.e. every scheduled run.
    """

    deadlines = []
    for bet in bets:
        if str(bet.get("event_state") or "") != "open":
            continue
        deadline = parse_utc_datetime(str(bet.get("deadline") or bet.get("event_date") or ""))
        if deadline is not None and deadline > now:
            deadlines.append(deadline)
    if deadlines:
        return None
    if not bets:
        return now + EMPTY_ROUND_REFETCH
    return now + FINISHED_ROUND_REFETCH


def parse_expert_react_components(html: str, *, base_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract ScoreBet payloads and unvisited round URLs from one expert page."""

    bets: list[dict[str, Any]] = []
    round_urls: list[str] = []
    parsed_base = urllib.parse.urlparse(base_url)
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
    for name, raw_props in _REACT_COMPONENT_RE.findall(html or ""):
        try:
            props = json.loads(html_lib.unescape(raw_props))
        except (ValueError, TypeError):
            continue
        if name == "ScoreBet" and isinstance(props, dict):
            bet = props.get("bet")
            if isinstance(bet, dict):
                bets.append(bet)
        elif name == "SelectRaceweek/index" and isinstance(props, dict):
            for option in props.get("options") or []:
                if not isinstance(option, dict) or option.get("selected"):
                    continue
                option_name = str(option.get("name") or "")
                option_url = str(option.get("url") or "")
                # The Zusatzfragen round holds bonus questions, not score bets.
                if not option_url or option_name.startswith("Zusatzfragen"):
                    continue
                round_urls.append(urllib.parse.urljoin(origin, option_url))
    return bets, round_urls


def srf_expert_rows_from_bets(
    bets: list[dict[str, Any]],
    *,
    expert_id: str,
    expert_url: str,
    fixtures: list[FixtureRecord],
) -> list[dict[str, Any]]:
    resolver = TeamResolver.default(source=SOURCE_SRF_EXPERTS)
    fixtures_by_pair: dict[tuple[str, str], list[FixtureRecord]] = {}
    for fixture in fixtures:
        fixtures_by_pair.setdefault((fixture.home_team.key, fixture.away_team.key), []).append(fixture)

    observed_at = normalize_datetime(utc_now()) or ""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bet in bets:
        if str(bet.get("type") or "score") != "score" or bet.get("censored"):
            continue
        picks = bet.get("picks")
        if not isinstance(picks, (list, tuple)) or len(picks) != 2 or any(p is None for p in picks):
            continue
        teams = bet.get("teams") or []
        if len(teams) != 2:
            continue
        # teams[].id repeats the away id on SRF's side; names are reliable.
        home = resolver.resolve(str(teams[0].get("name") or ""))
        away = resolver.resolve(str(teams[1].get("name") or ""))
        fixture = _closest_fixture(fixtures_by_pair.get((home.key, away.key), []), str(bet.get("event_date") or ""))
        if fixture is None:
            continue
        try:
            tip_home, tip_away = int(picks[0]), int(picks[1])
        except (TypeError, ValueError):
            continue
        record_key = stable_hash({"fixture_key": fixture.key, "expert": expert_id, "score": [tip_home, tip_away]})
        if record_key in seen:
            continue
        seen.add(record_key)
        rows.append(
            {
                "record_key": record_key,
                "fixture_key": fixture.key,
                "event_date": fixture.event_date,
                "home_team": fixture.home_team.name,
                "away_team": fixture.away_team.name,
                "home_fifa_code": fixture.home_team.fifa_code,
                "away_fifa_code": fixture.away_team.fifa_code,
                "expert_id": expert_id,
                "expert_url": expert_url,
                "tip_home": tip_home,
                "tip_away": tip_away,
                "observed_at_utc": observed_at,
                "metadata": {
                    "bet_id": bet.get("bet_id"),
                    "round": bet.get("round"),
                    "event_state": bet.get("event_state"),
                    "deadline": bet.get("deadline"),
                },
            }
        )
    return rows


def _closest_fixture(candidates: list[FixtureRecord], event_date: str) -> FixtureRecord | None:
    if not candidates:
        return None
    bet_kickoff = parse_utc_datetime(event_date)
    if bet_kickoff is None:
        return candidates[0] if len(candidates) == 1 else None
    best: tuple[dt.timedelta, FixtureRecord] | None = None
    for fixture in candidates:
        kickoff = parse_utc_datetime(fixture.event_date)
        if kickoff is None:
            continue
        delta = abs(kickoff - bet_kickoff)
        if delta <= dt.timedelta(hours=36) and (best is None or delta < best[0]):
            best = (delta, fixture)
    return best[1] if best else None


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
