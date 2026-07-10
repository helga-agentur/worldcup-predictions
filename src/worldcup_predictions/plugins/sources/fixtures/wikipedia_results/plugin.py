"""Wikipedia finished-match result source plugin.

Wikipedia tournament pages are updated within minutes of full time, so their
"footballbox" match blocks make a useful independent witness for the central
result-consensus policy. This plugin only writes observation rows to the
TOURNAMENT_RESULTS dataset; it never touches tournament state directly.
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import json
import re
import urllib.error
from typing import Any

from worldcup_predictions.core.constants import ENDPOINT_WIKIPEDIA_API
from worldcup_predictions.core.contracts import Artifact, Diagnostic, ScoreTip, parse_utc_datetime
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest
from worldcup_predictions.tournament import ResultRecord, TeamResolver, TournamentState
from worldcup_predictions.tournament.repository import write_results

# Distinct from the "wikipedia" squads source so ledger backoffs do not couple.
SOURCE_WIKIPEDIA_RESULTS = "wikipedia_results"

# Knockout page first; the tournament is in the knockout phase. Group pages can
# be appended here later without further code changes.
WIKIPEDIA_RESULT_PAGES = ("2026 FIFA World Cup knockout stage",)

EXTRACTOR_ID = "wikipedia_footballbox_v1"

_FOOTBALLBOX_ANCHOR_RE = re.compile(r"class=[\"'][^\"']*\bfootballbox\b", re.IGNORECASE)
_FSCORE_CELL_RE = re.compile(
    r"<t[hd]\b[^>]*class=[\"'][^\"']*\bfscore\b[^\"']*[\"'][^>]*>(.*?)</t[hd]>",
    re.IGNORECASE | re.DOTALL,
)
_FHOME_CELL_RE = re.compile(
    r"<t[hd]\b[^>]*class=[\"'][^\"']*\bfhome\b[^\"']*[\"'][^>]*>(.*?)</t[hd]>",
    re.IGNORECASE | re.DOTALL,
)
_FAWAY_CELL_RE = re.compile(
    r"<t[hd]\b[^>]*class=[\"'][^\"']*\bfaway\b[^\"']*[\"'][^>]*>(.*?)</t[hd]>",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
# Wikipedia uses EN DASH (U+2013) between scores; be tolerant of close cousins.
_DASH_VARIANTS = ("–", "—", "−", "‐", "‑")
_SCORE_TEXT_RE = re.compile(
    r"(\d{1,2})\s*-\s*(\d{1,2})\s*(?P<aet>\(\s*a\.e\.t\.?\s*\))?",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
    re.IGNORECASE,
)
_PENALTIES_MARKER_RE = re.compile(r">\s*Penalties\s*<", re.IGNORECASE)
_MAX_BLOCK_LENGTH = 30000


class WikipediaResultsPlugin(BasePlugin):
    """Fetch Wikipedia tournament pages and extract finished-match score witnesses."""

    id = "wikipedia_results"
    version = "0.1.0"
    priority = 115
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Extract finished-match scores from Wikipedia footballbox blocks as result-consensus witnesses.",
        datasets_written=(TOURNAMENT_RESULTS, EXTRACTION_DIAGNOSTICS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Public Wikipedia tournament pages refresh through the source ledger on a short interval during the tournament.",
        ),
        confidence_policy="Wikipedia score rows are witness observations only; results enter tournament state solely through the central source-consensus policy.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Wikipedia results")
        state = runtime.tournament_state()
        diagnostics: list[Diagnostic] = []
        results: list[ResultRecord] = []
        extraction_rows: list[dict[str, Any]] = []
        for page_title in WIKIPEDIA_RESULT_PAGES:
            page_result = self._fetch_page(runtime, state, page_title=page_title)
            diagnostics.extend(page_result.diagnostics)
            results.extend(page_result.metadata.get("results") or [])
            extraction_rows.extend(page_result.metadata.get("extraction_rows") or [])
        result_count = write_results(runtime.storage, _dedupe_results(results), source=self.id, run_id=runtime.context.run_id)
        extraction_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": extraction_count}),
            ],
            diagnostics=diagnostics,
            metadata={"results": result_count, "extraction_rows": extraction_count, "pages": len(WIKIPEDIA_RESULT_PAGES)},
        )

    def _fetch_page(self, runtime: SourceRuntime, state: TournamentState, *, page_title: str) -> PluginResult:
        request = SourceRequest(
            source=SOURCE_WIKIPEDIA_RESULTS,
            endpoint=ENDPOINT_WIKIPEDIA_API,
            purpose="world_cup_results_page",
            params={"title": page_title},
            fixture_key=None,
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=30),
            quota_scope=SOURCE_WIKIPEDIA_RESULTS,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Wikipedia results page", decision.reason, metadata={"page": page_title, **decision.metadata})
        try:
            payload, _headers = runtime.fetch_json(
                ENDPOINT_WIKIPEDIA_API,
                {
                    "action": "parse",
                    "page": page_title,
                    "prop": "text",
                    "format": "json",
                    "redirects": 1,
                    "formatversion": 2,
                },
            )
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc, metadata={"page": page_title})
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "Wikipedia results page fetch failed.",
                        metadata={"page": page_title, "error": str(exc)},
                    )
                ]
            )
        page_html = (((payload or {}).get("parse") or {}).get("text") or "") if isinstance(payload, dict) else ""
        matches = parse_wikipedia_footballbox_matches(page_html)
        results, unmatched = wikipedia_result_records(matches, state=state, page_title=page_title)
        runtime.record_success(
            request,
            message="Fetched Wikipedia results page.",
            metadata={"page": page_title, "parsed_matches": len(matches), "results": len(results), "unmatched": unmatched},
        )
        diagnostics: list[Diagnostic] = []
        extraction_rows: list[dict[str, Any]] = []
        if page_html and not matches:
            # A 200 page with zero parsed matches is a structured extraction
            # failure worth surfacing, not a silent no-op.
            extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_WIKIPEDIA_RESULTS,
                    extractor=EXTRACTOR_ID,
                    status="rejected",
                    reason="no_footballbox_matches_parsed",
                    source_name="Wikipedia",
                    source_url=_page_url(page_title),
                    title=page_title,
                    severity="warning",
                    metadata={"page": page_title, "page_html_length": len(page_html)},
                )
            )
            diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "Wikipedia results page returned no parseable footballbox matches.",
                    metadata={"page": page_title, "page_html_length": len(page_html)},
                )
            )
        elif matches and not results:
            diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "Wikipedia footballbox matches did not map to any known fixture.",
                    metadata={"page": page_title, "parsed_matches": len(matches)},
                )
            )
        return runtime.result(diagnostics=diagnostics, metadata={"results": results, "extraction_rows": extraction_rows})


def parse_wikipedia_footballbox_matches(page_html: str) -> list[dict[str, Any]]:
    """Parse finished-match observations from Wikipedia footballbox blocks.

    Anything ambiguous (missing team, non-score text such as a kickoff time or
    a placeholder, malformed score) is skipped rather than guessed at.
    """

    matches: list[dict[str, Any]] = []
    for index, block in enumerate(_footballbox_blocks(page_html or "")):
        parsed = _parse_footballbox_block(block)
        if parsed is None:
            continue
        parsed["block_index"] = index
        matches.append(parsed)
    return matches


def wikipedia_result_records(
    matches: list[dict[str, Any]],
    *,
    state: TournamentState,
    page_title: str,
) -> tuple[list[ResultRecord], int]:
    """Map parsed Wikipedia matches onto known fixtures as witness result rows.

    Rows reuse the matched fixture's canonical event date and team references so
    the resulting fixture_key lines up exactly with the fixture it confirms.
    Returns the records plus the count of parsed matches that could not be
    matched to exactly one fixture (skipped, never guessed).
    """

    resolver = TeamResolver.default(source=SOURCE_WIKIPEDIA_RESULTS)
    fixtures_by_pair: dict[tuple[str, str], list[Any]] = {}
    for fixture in state.fixtures:
        fixtures_by_pair.setdefault((fixture.home_team.key, fixture.away_team.key), []).append(fixture)

    records: list[ResultRecord] = []
    unmatched = 0
    for match in matches:
        home = resolver.resolve(str(match.get("home_team") or ""))
        away = resolver.resolve(str(match.get("away_team") or ""))
        home_score = int(match["home_score"])
        away_score = int(match["away_score"])
        swapped = False
        candidates = _candidate_fixtures(fixtures_by_pair.get((home.key, away.key), []), match.get("date"))
        if not candidates:
            candidates = _candidate_fixtures(fixtures_by_pair.get((away.key, home.key), []), match.get("date"))
            swapped = bool(candidates)
        if len(candidates) != 1:
            unmatched += 1
            continue
        fixture = candidates[0]
        if swapped:
            home_score, away_score = away_score, home_score
        metadata: dict[str, Any] = {
            "parser": EXTRACTOR_ID,
            "page": page_title,
            "after_extra_time": bool(match.get("after_extra_time")),
            "orientation": "swapped" if swapped else "as_listed",
        }
        notes = "Wikipedia footballbox result."
        penalties = match.get("penalties")
        if penalties:
            pen_home, pen_away = (int(penalties[0]), int(penalties[1]))
            if swapped:
                pen_home, pen_away = pen_away, pen_home
            metadata["home_penalty_score"] = pen_home
            metadata["away_penalty_score"] = pen_away
            notes = "Wikipedia footballbox result with penalty shoot-out."
        elif match.get("after_extra_time"):
            notes = "Wikipedia footballbox result after extra time."
        records.append(
            ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=ScoreTip(home_score, away_score),
                source=SOURCE_WIKIPEDIA_RESULTS,
                status="final",
                notes=notes,
                metadata=metadata,
            )
        )
    return records, unmatched


def _candidate_fixtures(fixtures: list[Any], match_date: str | None) -> list[Any]:
    if not fixtures:
        return []
    if not match_date:
        return list(fixtures)
    parsed_date = _parse_iso_date(match_date)
    if parsed_date is None:
        return list(fixtures)
    candidates = []
    for fixture in fixtures:
        kickoff = parse_utc_datetime(fixture.event_date)
        if kickoff is None:
            continue
        # Wikipedia lists stadium-local dates while fixtures are UTC, so allow
        # one day of slack in either direction.
        if abs((kickoff.date() - parsed_date).days) <= 1:
            candidates.append(fixture)
    return candidates


def _footballbox_blocks(page_html: str) -> list[str]:
    starts = [match.start() for match in _FOOTBALLBOX_ANCHOR_RE.finditer(page_html)]
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(page_html)
        blocks.append(page_html[start:min(end, start + _MAX_BLOCK_LENGTH)])
    return blocks


def _parse_footballbox_block(block: str) -> dict[str, Any] | None:
    home_cell = _FHOME_CELL_RE.search(block)
    away_cell = _FAWAY_CELL_RE.search(block)
    score_cells = list(_FSCORE_CELL_RE.finditer(block))
    if home_cell is None or away_cell is None or not score_cells:
        return None
    home_team = _team_label(home_cell.group(1))
    away_team = _team_label(away_cell.group(1))
    if not home_team or not away_team or home_team == away_team:
        return None
    score = _parse_score_text(score_cells[0].group(1))
    if score is None:
        return None
    home_score, away_score, after_extra_time = score
    penalties = _parse_penalties(block, score_cells)
    if penalties is not None:
        after_extra_time = True
    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "after_extra_time": after_extra_time,
        "penalties": penalties,
        "date": _block_date(block),
    }


def _parse_penalties(block: str, score_cells: list[re.Match[str]]) -> tuple[int, int] | None:
    marker = _PENALTIES_MARKER_RE.search(block)
    if marker is None:
        return None
    for cell in score_cells:
        if cell.start() <= marker.start():
            continue
        parsed = _parse_score_text(cell.group(1))
        if parsed is None:
            # The first fscore cell after the marker should be the shoot-out
            # score; anything else means we cannot extract it reliably.
            return None
        pen_home, pen_away, _aet = parsed
        if pen_home == pen_away:
            # A shoot-out cannot end level; treat as unreliable extraction.
            return None
        return pen_home, pen_away
    return None


def _team_label(cell_html: str) -> str:
    for anchor in _ANCHOR_RE.finditer(cell_html):
        label = _strip_tags(anchor.group(1))
        if label:
            return label
    return _strip_tags(cell_html)


def _parse_score_text(cell_html: str) -> tuple[int, int, bool] | None:
    text = _strip_tags(cell_html)
    for dash in _DASH_VARIANTS:
        text = text.replace(dash, "-")
    match = _SCORE_TEXT_RE.fullmatch(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), bool(match.group("aet"))


def _block_date(block: str) -> str | None:
    match = _DATE_RE.search(_strip_tags(block))
    if match is None:
        return None
    month = _MONTHS.get(match.group(2).casefold())
    if month is None:
        return None
    try:
        parsed = dt.date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None
    return parsed.isoformat()


def _parse_iso_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _dedupe_results(results: list[ResultRecord]) -> list[ResultRecord]:
    by_key: dict[str, ResultRecord] = {}
    for result in results:
        by_key[result.record_key] = result
    return list(by_key.values())


def _page_url(page_title: str) -> str:
    return f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"


def _strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html_lib.unescape(text).split())
