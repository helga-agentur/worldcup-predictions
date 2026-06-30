"""openfootball/worldcup import support."""

from __future__ import annotations

import re
import datetime as dt

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.storage.ledger import normalize_datetime
from worldcup_predictions.tournament.contracts import FixtureRecord, ResultRecord
from worldcup_predictions.tournament.teams import TeamResolver

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

DATE_RE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2})\b",
    re.I,
)
STAGE_RE = re.compile(r"^▪+\s*(?P<stage>.+?)\s*$")
MATCH_RE = re.compile(
    r"^\s*(?:\((?P<match_number>\d+)\)\s+)?"
    r"(?P<local_time>\d{1,2}:\d{2})\s+UTC(?P<utc_offset>[+-]\d{1,2})\s+"
    r"(?P<body>.+?)\s+@\s+(?P<venue>.+?)\s*$"
)
SCORED_RE = re.compile(
    r"^(?P<home>.+?)\s+"
    r"(?P<home_score>\d+)-(?P<away_score>\d+)"
    r"(?:\s+\((?P<half_home>\d+)-(?P<half_away>\d+)\))?\s+"
    r"(?P<away>.+?)\s*$"
)
SCHEDULED_RE = re.compile(r"^(?P<home>.+?)\s+v\s+(?P<away>.+?)\s*$")


def parse_openfootball_text(
    text: str,
    *,
    source_id: str = "openfootball/worldcup:cup.txt",
    year: int = 2026,
    resolver: TeamResolver | None = None,
) -> tuple[list[FixtureRecord], list[ResultRecord]]:
    """Parse Football.TXT rows into canonical fixtures and final results."""

    resolver = resolver or TeamResolver.default(source="openfootball")
    fixtures: list[FixtureRecord] = []
    results: list[ResultRecord] = []
    current_stage = ""
    current_group = ""
    current_month: int | None = None
    current_day: int | None = None
    pending_fixture: FixtureRecord | None = None

    for original_line in text.splitlines():
        line = original_line.split("##", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("="):
            continue

        stage_match = STAGE_RE.match(stripped)
        if stage_match:
            current_stage = stage_match.group("stage").strip()
            current_group = current_stage if current_stage.lower().startswith("group ") else ""
            pending_fixture = None
            continue

        date_match = DATE_RE.match(stripped)
        if date_match:
            current_month = MONTHS[date_match.group("month").lower()]
            current_day = int(date_match.group("day"))
            pending_fixture = None
            continue

        match = MATCH_RE.match(line)
        if match and current_month and current_day:
            body = match.group("body").strip()
            scored = SCORED_RE.match(body)
            scheduled = SCHEDULED_RE.match(body)
            parsed = scored or scheduled
            if not parsed:
                pending_fixture = None
                continue

            home = resolver.resolve(parsed.group("home"))
            away = resolver.resolve(parsed.group("away"))
            event_date = _event_date(
                year,
                current_month,
                current_day,
                match.group("local_time"),
                match.group("utc_offset"),
            )
            fixture = FixtureRecord(
                event_date=event_date,
                home_team=home,
                away_team=away,
                stage=current_stage or None,
                group=current_group or None,
                source_id=match.group("match_number") or None,
                venue=match.group("venue").strip(),
                status="final" if scored else "scheduled",
                metadata={
                    "source": source_id,
                    "local_time": match.group("local_time"),
                    "utc_offset": f"UTC{match.group('utc_offset')}",
                    "match_number": match.group("match_number") or "",
                },
            )
            fixtures.append(fixture)
            pending_fixture = fixture
            if scored:
                results.append(
                    ResultRecord(
                        event_date=event_date,
                        home_team=home,
                        away_team=away,
                        score=ScoreTip(int(parsed.group("home_score")), int(parsed.group("away_score"))),
                        source=source_id,
                        metadata={
                            "half_home_score": parsed.group("half_home") or None,
                            "half_away_score": parsed.group("half_away") or None,
                            "goals_text": "",
                        },
                    )
                )
            continue

        if pending_fixture and line[:1].isspace() and results:
            last = results[-1]
            if last.fixture_key == pending_fixture.key:
                goals_text = " ".join(
                    value
                    for value in [
                        str(last.metadata.get("goals_text") or ""),
                        stripped,
                    ]
                    if value
                )
                results[-1] = ResultRecord(
                    event_date=last.event_date,
                    home_team=last.home_team,
                    away_team=last.away_team,
                    score=last.score,
                    source=last.source,
                    status=last.status,
                    notes=last.notes,
                    metadata={**last.metadata, "goals_text": goals_text},
                )
            continue

        pending_fixture = None

    return fixtures, results


def _event_date(year: int, month: int, day: int, local_time: str, utc_offset: str) -> str:
    hour, minute = [int(part) for part in local_time.split(":", 1)]
    offset = int(utc_offset)
    local_tz = dt.timezone(dt.timedelta(hours=offset))
    local_dt = dt.datetime(year, month, day, hour, minute, tzinfo=local_tz)
    return normalize_datetime(local_dt) or local_dt.isoformat()
