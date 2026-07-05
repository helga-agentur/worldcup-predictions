"""Wikipedia-derived squad source plugin."""

from __future__ import annotations

import datetime as dt
import html as html_lib
import json
import re
import urllib.error
from typing import Any

from worldcup_predictions.core.constants import ENDPOINT_WIKIPEDIA_API, SOURCE_WIKIPEDIA
from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import SQUAD_PLAYERS, WIKIPEDIA_SQUADS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash, utc_now
from worldcup_predictions.tournament import TournamentState


class WikipediaSquadsPlugin(BasePlugin):
    """Fetch public national-team pages and extract current-squad table rows."""

    id = "wikipedia_squads"
    version = "0.1.0"
    priority = 126
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Extract squad facts from Wikipedia national-team pages with attribution and diagnostics.",
        datasets_read=(WIKIPEDIA_SQUADS,),
        datasets_written=(WIKIPEDIA_SQUADS, SQUAD_PLAYERS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Wikipedia API pages refresh daily and store only parsed squad facts.",
        ),
        confidence_policy="Wikipedia squad rows are supplemental and merged by canonical country code.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Wikipedia squads")
        state = runtime.tournament_state()
        teams = _teams_from_state(state)
        diagnostics: list[Diagnostic] = []
        rows: list[dict[str, Any]] = []
        for team_key, team_name in teams.items():
            result = self._fetch_team(runtime, team_key=team_key, team_name=team_name)
            diagnostics.extend(result.diagnostics)
            rows.extend(result.metadata.get("rows") or [])
        squad_count = runtime.write_records(WIKIPEDIA_SQUADS, rows)
        player_count = runtime.write_records(SQUAD_PLAYERS, [squad_player_row(row) for row in rows])
        if teams and not rows:
            diagnostics.append(runtime.diagnostic("info", "Wikipedia squad extraction produced no rows for current teams."))
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(WIKIPEDIA_SQUADS, "structured_dataset", self.id, data={"rows": squad_count}),
                Artifact(SQUAD_PLAYERS, "structured_dataset", self.id, data={"rows": player_count}),
            ],
            diagnostics=diagnostics,
            metadata={"teams": len(teams), "squad_rows": squad_count, "player_rows": player_count},
        )

    def _fetch_team(self, runtime: SourceRuntime, *, team_key: str, team_name: str) -> PluginResult:
        title = f"{team_name} national football team"
        request = SourceRequest(
            source=SOURCE_WIKIPEDIA,
            endpoint=ENDPOINT_WIKIPEDIA_API,
            purpose="national_team_squad_page",
            params={"title": title},
            fixture_key=None,
            quota_cost=0,
            min_refresh_interval=dt.timedelta(days=1),
            quota_scope=SOURCE_WIKIPEDIA,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Wikipedia squad page", decision.reason, metadata={"team": team_name, **decision.metadata})
        try:
            payload, _headers = runtime.fetch_json(
                ENDPOINT_WIKIPEDIA_API,
                {
                    "action": "parse",
                    "page": title,
                    "prop": "text",
                    "format": "json",
                    "redirects": 1,
                    "formatversion": 2,
                },
            )
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "Wikipedia squad fetch failed.", metadata={"team": team_name, "error": str(exc)})])
        page_html = (((payload or {}).get("parse") or {}).get("text") or "") if isinstance(payload, dict) else ""
        rows = wikipedia_squad_rows(page_html, team_key=team_key, team_name=team_name, page_title=title)
        runtime.record_success(request, message="Fetched Wikipedia squad page.", metadata={"team": team_name, "rows": len(rows)})
        diagnostics = []
        if not rows:
            diagnostics.append(runtime.diagnostic("info", "No current-squad rows were extracted from Wikipedia page.", metadata={"team": team_name, "title": title}))
        return runtime.result(diagnostics=diagnostics, metadata={"rows": rows})


def wikipedia_squad_rows(page_html: str, *, team_key: str, team_name: str, page_title: str) -> list[dict[str, Any]]:
    current_section = _current_squad_section(page_html)
    if not current_section:
        return []
    observed_at = normalize_datetime(utc_now()) or ""
    rows = []
    for index, row_html in enumerate(re.findall(r"<tr\b.*?</tr>", current_section, flags=re.IGNORECASE | re.DOTALL)):
        text = _strip_tags(row_html)
        if not text or "caps" in text.casefold() and "goals" in text.casefold():
            continue
        player = _first_player_link(row_html)
        if not player:
            continue
        position = _position_from_row(text)
        rows.append(
            {
                "record_key": stable_hash({"team": team_key, "player": player, "source": page_title}),
                "team": team_name,
                "fifa_code": team_key if len(team_key) == 3 else None,
                "player_name": player,
                "position": position,
                "source_page": page_title,
                "observed_at_utc": observed_at,
                "row_index": index,
                "metadata": {"source": SOURCE_WIKIPEDIA, "row_text": text[:240]},
            }
        )
    return rows


def squad_player_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_key": f"wikipedia:{row.get('record_key')}",
        "team": row.get("team"),
        "fifa_code": row.get("fifa_code"),
        "player_name": row.get("player_name"),
        "source_player_id": None,
        "position": row.get("position"),
        "date_of_birth": None,
        "nationality": row.get("team"),
        "current_club_name": None,
        "market_value_in_eur": None,
        "match_score": 0.70,
        "metadata": {"source": SOURCE_WIKIPEDIA, "source_page": row.get("source_page")},
    }


def _teams_from_state(state: TournamentState) -> dict[str, str]:
    teams: dict[str, str] = {}
    for fixture in state.open_fixtures() or state.fixtures:
        teams[fixture.home_team.key] = fixture.home_team.name
        teams[fixture.away_team.key] = fixture.away_team.name
    return dict(sorted(teams.items()))


def _current_squad_section(page_html: str) -> str:
    match = re.search(r"(Current squad|Current players|Squad)</span>.*?(<table\b.*?)(?:<h2|<h3|$)", page_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return match.group(2)


def _first_player_link(row_html: str) -> str:
    for match in re.finditer(r'<a\b[^>]*title="([^"]+)"[^>]*>(.*?)</a>', row_html, flags=re.IGNORECASE | re.DOTALL):
        title = html_lib.unescape(match.group(1))
        label = _strip_tags(match.group(2))
        candidate = label or title
        if candidate and not any(skip in candidate.casefold() for skip in ("national", "football", "team", "club")):
            return candidate
    return ""


def _position_from_row(text: str) -> str:
    for position in ("Goalkeeper", "Defender", "Midfielder", "Forward"):
        if position.casefold() in text.casefold():
            return position
    for short in ("GK", "DF", "MF", "FW"):
        if re.search(rf"\b{short}\b", text):
            return short
    return ""


def _strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html_lib.unescape(text).split())
