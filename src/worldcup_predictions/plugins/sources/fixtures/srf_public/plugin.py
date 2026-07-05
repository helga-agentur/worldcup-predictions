"""SRF public fixture and bonus-question source plugin."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.error
from typing import Any

from worldcup_predictions.core.constants import SOURCE_SRF_PUBLIC, SRF_BASE_URL
from worldcup_predictions.core.contracts import Artifact, ScoreTip
from worldcup_predictions.core.datasets import SRF_BONUS_QUESTIONS, SRF_FIXTURES, TOURNAMENT_FIXTURES, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_fixtures, write_results


SRF_ROUNDS = tuple(range(40, 50))


class SrfPublicPlugin(BasePlugin):
    """Fetch SRF public round pages for fixtures, results, and bonus questions."""

    id = "srf_public"
    version = "0.1.0"
    priority = 115
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch SRF public round pages and extract fixtures, final results, and bonus-question metadata.",
        datasets_read=(SRF_FIXTURES, SRF_BONUS_QUESTIONS),
        datasets_written=(SRF_FIXTURES, SRF_BONUS_QUESTIONS, TOURNAMENT_FIXTURES, TOURNAMENT_RESULTS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Public SRF round pages refresh with a short ledger interval during the tournament.",
        ),
        confidence_policy="SRF fixture rows are canonicalized through the country registry before entering tournament state.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("SRF public fixtures")
        fixture_rows: list[dict[str, Any]] = []
        bonus_rows: list[dict[str, Any]] = []
        result_rows: list[ResultRecord] = []
        diagnostics = []
        for round_id in SRF_ROUNDS:
            result = self._fetch_round(runtime, round_id)
            diagnostics.extend(result.diagnostics)
            fixture_rows.extend(result.metadata.get("fixtures") or [])
            bonus_rows.extend(result.metadata.get("bonus") or [])
            result_rows.extend(result.metadata.get("results") or [])
        srf_fixture_count = runtime.write_records(SRF_FIXTURES, fixture_rows)
        bonus_count = runtime.write_records(SRF_BONUS_QUESTIONS, bonus_rows)
        fixtures = [fixture_record_from_srf(row) for row in fixture_rows]
        tournament_fixture_count = write_fixtures(runtime.storage, fixtures, source=self.id, run_id=runtime.context.run_id)
        result_count = write_results(runtime.storage, result_rows, source=self.id, run_id=runtime.context.run_id)
        state = load_tournament_state(runtime.storage)
        write_derived_state(runtime.storage, state, run_id=runtime.context.run_id)
        runtime.context.state["tournament_state"] = state
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(SRF_FIXTURES, "structured_dataset", self.id, data={"rows": srf_fixture_count}),
                Artifact(SRF_BONUS_QUESTIONS, "structured_dataset", self.id, data={"rows": bonus_count}),
                Artifact(TOURNAMENT_FIXTURES, "structured_dataset", self.id, data={"rows": tournament_fixture_count}),
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows": result_count}),
            ],
            diagnostics=diagnostics,
            metadata={"fixtures": srf_fixture_count, "bonus": bonus_count, "tournament_fixtures": tournament_fixture_count, "results": result_count},
        )

    def _fetch_round(self, runtime: SourceRuntime, round_id: int) -> PluginResult:
        url = f"{SRF_BASE_URL}/experts/round/{round_id}"
        request = SourceRequest(
            source=SOURCE_SRF_PUBLIC,
            endpoint=url,
            purpose="srf_round",
            params={"round": round_id},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.expert_refresh_minutes),
            quota_scope=SOURCE_SRF_PUBLIC,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("SRF public round", decision.reason, metadata={"round": round_id, **decision.metadata})
        try:
            page, _headers = runtime.fetch_text(url)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "SRF public round fetch failed.", metadata={"round": round_id, "error": str(exc)})])
        fixtures = parse_srf_score_bets(page)
        bonus = parse_srf_bonus_questions(page)
        results = parse_srf_results(page)
        runtime.record_success(
            request,
            message="Fetched SRF public round.",
            metadata={"round": round_id, "fixtures": len(fixtures), "bonus": len(bonus), "results": len(results)},
        )
        return runtime.result(metadata={"fixtures": fixtures, "bonus": bonus, "results": results})


def fixture_record_from_srf(row: dict[str, Any]) -> FixtureRecord:
    resolver = TeamResolver.default(source=SOURCE_SRF_PUBLIC)
    home = resolver.resolve(str(row.get("home_team") or ""))
    away = resolver.resolve(str(row.get("away_team") or ""))
    return FixtureRecord(
        event_date=normalize_datetime(row.get("event_date")) or str(row.get("event_date") or ""),
        home_team=home,
        away_team=away,
        source_id=str(row.get("bet_id") or "") or None,
        stage=row.get("phase"),
        group=row.get("group"),
        matchday=row.get("round"),
        venue=row.get("location"),
        status=str(row.get("event_state") or "scheduled").lower(),
        metadata={"source": SOURCE_SRF_PUBLIC, "deadline": row.get("deadline"), "location": row.get("location")},
    )


def parse_srf_score_bets(page: str) -> list[dict[str, Any]]:
    rows = []
    for props in _extract_react_props(page, "ScoreBet"):
        bet = props.get("bet") or {}
        teams = bet.get("teams") or []
        if len(teams) != 2:
            continue
        round_id = int(bet.get("round") or 0)
        home = str((teams[0] or {}).get("name") or "")
        away = str((teams[1] or {}).get("name") or "")
        event_date = normalize_datetime(bet.get("event_date")) or str(bet.get("event_date") or "")
        rows.append(
            {
                "record_key": stable_hash({"bet_id": bet.get("bet_id"), "event_date": event_date, "home": home, "away": away}),
                "fixture_key": f"{event_date}|{TeamResolver.default(source=SOURCE_SRF_PUBLIC).resolve(home).key}|{TeamResolver.default(source=SOURCE_SRF_PUBLIC).resolve(away).key}",
                "bet_id": str(bet.get("bet_id") or ""),
                "round": round_id,
                "phase": _phase_for_round(round_id),
                "event_date": event_date,
                "deadline": normalize_datetime(bet.get("deadline")) or str(bet.get("deadline") or ""),
                "location": bet.get("meta_location"),
                "home_team": home,
                "away_team": away,
                "event_state": bet.get("event_state"),
                "metadata": {"source": SOURCE_SRF_PUBLIC},
            }
        )
    return rows


def parse_srf_bonus_questions(page: str) -> list[dict[str, Any]]:
    rows = []
    for props in _extract_react_props(page, "TextSelection"):
        bet = props.get("bet") or {}
        question = str(bet.get("question") or "")
        question_key = stable_hash({"provider": "srf.ch", "question": question})
        rows.append(
            {
                "record_key": f"srf.ch:{question_key}",
                "provider": "srf.ch",
                "question_key": question_key,
                "bet_id": str(bet.get("bet_id") or ""),
                "round": int(bet.get("round") or 0),
                "event_name": bet.get("event_name"),
                "deadline": normalize_datetime(bet.get("deadline")) or str(bet.get("deadline") or ""),
                "question": question,
                "answers": bet.get("answers") or [],
                "metadata": {"source": SOURCE_SRF_PUBLIC},
            }
        )
    return rows


def parse_srf_results(page: str) -> list[ResultRecord]:
    resolver = TeamResolver.default(source=SOURCE_SRF_PUBLIC)
    rows: list[ResultRecord] = []
    for props in _extract_react_props(page, "ScoreBet"):
        bet = props.get("bet") or {}
        teams = bet.get("teams") or []
        final_results = bet.get("final_results") or []
        if len(teams) != 2 or len(final_results) != 2:
            continue
        if str(bet.get("event_state") or "").casefold() not in {"over", "finished", "final"} and not bet.get("race_over"):
            continue
        try:
            score = ScoreTip(int(final_results[0]), int(final_results[1]))
        except (TypeError, ValueError):
            continue
        home = resolver.resolve(str((teams[0] or {}).get("name") or ""))
        away = resolver.resolve(str((teams[1] or {}).get("name") or ""))
        rows.append(
            ResultRecord(
                event_date=normalize_datetime(bet.get("event_date")) or str(bet.get("event_date") or ""),
                home_team=home,
                away_team=away,
                score=score,
                source=SOURCE_SRF_PUBLIC,
                notes="SRF public final_results payload.",
                metadata={
                    "bet_id": str(bet.get("bet_id") or ""),
                    "round": int(bet.get("round") or 0),
                    "event_state": bet.get("event_state"),
                    "parser": "srf_scorebet_final_results",
                },
            )
        )
    return rows


def _extract_react_props(page: str, react_class: str):
    pattern = re.compile(r'data-react-class="' + re.escape(react_class) + r'"\s+data-react-props="([^"]+)"')
    for match in pattern.finditer(page):
        try:
            yield json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue


def _phase_for_round(round_id: int) -> str:
    if round_id in {41, 42, 43}:
        return "group"
    if round_id >= 44:
        return "knockout"
    return "bonus"
