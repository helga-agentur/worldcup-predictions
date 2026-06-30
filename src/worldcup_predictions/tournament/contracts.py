"""Tournament state contracts."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from worldcup_predictions.core.contracts import Fixture, ScoreTip, parse_utc_datetime
from worldcup_predictions.storage.ledger import normalize_datetime, stable_hash


def fixture_key(event_date: str, home_team: str, away_team: str) -> str:
    return f"{normalize_datetime(event_date) or event_date}|{home_team}|{away_team}"


@dataclass(frozen=True)
class TeamRef:
    """A team display label plus optional canonical FIFA code."""

    name: str
    fifa_code: str | None = None

    @property
    def key(self) -> str:
        return self.fifa_code or self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fifa_code": self.fifa_code,
        }


@dataclass(frozen=True)
class FixtureRecord:
    """Canonical fixture row used by tournament state and predictions."""

    event_date: str
    home_team: TeamRef
    away_team: TeamRef
    stage: str | None = None
    group: str | None = None
    matchday: int | None = None
    source_id: str | None = None
    venue: str | None = None
    status: str = "scheduled"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return fixture_key(self.event_date, self.home_team.key, self.away_team.key)

    @property
    def kickoff_at(self) -> dt.datetime | None:
        return parse_utc_datetime(self.event_date)

    def to_fixture(self) -> Fixture:
        return Fixture(
            event_date=self.event_date,
            home_team=self.home_team.name,
            away_team=self.away_team.name,
            source_id=self.source_id,
            stage=self.stage,
            group=self.group,
            matchday=self.matchday,
            metadata={
                **self.metadata,
                "home_fifa_code": self.home_team.fifa_code,
                "away_fifa_code": self.away_team.fifa_code,
                "venue": self.venue,
                "status": self.status,
            },
        )

    def to_record(self) -> dict[str, Any]:
        payload = {
            "record_key": self.record_key,
            "fixture_key": self.key,
            "event_date": self.event_date,
            "home_team": self.home_team.name,
            "away_team": self.away_team.name,
            "home_fifa_code": self.home_team.fifa_code,
            "away_fifa_code": self.away_team.fifa_code,
            "stage": self.stage,
            "group": self.group,
            "matchday": self.matchday,
            "source_id": self.source_id,
            "venue": self.venue,
            "status": self.status,
            "metadata": self.metadata,
        }
        return payload

    @property
    def record_key(self) -> str:
        """Stable storage identity for one source fixture slot.

        A fixture key intentionally includes the currently known teams. Knockout
        placeholders can be renamed by an upstream source while still referring
        to the same source slot, so use source ids for replacement when present.
        """

        if self.source_id:
            source = str(self.metadata.get("source") or "")
            return stable_hash(
                {
                    "source": source,
                    "source_id": self.source_id,
                    "event_date": normalize_datetime(self.event_date) or self.event_date,
                }
            )
        return self.key

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> "FixtureRecord":
        return cls(
            event_date=str(row["event_date"]),
            home_team=TeamRef(str(row["home_team"]), row.get("home_fifa_code")),
            away_team=TeamRef(str(row["away_team"]), row.get("away_fifa_code")),
            stage=row.get("stage"),
            group=row.get("group"),
            matchday=_optional_int(row.get("matchday")),
            source_id=row.get("source_id"),
            venue=row.get("venue"),
            status=str(row.get("status") or "scheduled"),
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ResultRecord:
    """One source-derived full-time result observation."""

    event_date: str
    home_team: TeamRef
    away_team: TeamRef
    score: ScoreTip
    source: str = "source-derived"
    status: str = "final"
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fixture_key(self) -> str:
        return fixture_key(self.event_date, self.home_team.key, self.away_team.key)

    @property
    def record_key(self) -> str:
        return stable_hash(
            {
                "event_date": normalize_datetime(self.event_date) or self.event_date,
                "home": self.home_team.key,
                "away": self.away_team.key,
                "source": self.source,
            }
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "record_key": self.record_key,
            "fixture_key": self.fixture_key,
            "event_date": normalize_datetime(self.event_date) or self.event_date,
            "home_team": self.home_team.name,
            "away_team": self.away_team.name,
            "home_fifa_code": self.home_team.fifa_code,
            "away_fifa_code": self.away_team.fifa_code,
            "home_score": self.score.home,
            "away_score": self.score.away,
            "status": self.status,
            "source": self.source,
            "notes": self.notes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> "ResultRecord":
        return cls(
            event_date=str(row["event_date"]),
            home_team=TeamRef(str(row["home_team"]), row.get("home_fifa_code")),
            away_team=TeamRef(str(row["away_team"]), row.get("away_fifa_code")),
            score=ScoreTip(int(row["home_score"]), int(row["away_score"])),
            source=str(row.get("source") or "unknown"),
            status=str(row.get("status") or "final"),
            notes=row.get("notes"),
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass
class GroupStanding:
    """Current group standing for one team."""

    team: TeamRef
    group: str
    played: int = 0
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def record(self, goals_for: int, goals_against: int) -> None:
        self.played += 1
        self.goals_for += goals_for
        self.goals_against += goals_against
        if goals_for > goals_against:
            self.wins += 1
            self.points += 3
        elif goals_for == goals_against:
            self.draws += 1
            self.points += 1
        else:
            self.losses += 1

    def to_record(self, rank: int) -> dict[str, Any]:
        return {
            "record_key": f"{self.group}:{self.team.key}",
            "team": self.team.name,
            "fifa_code": self.team.fifa_code,
            "group": self.group,
            "rank": rank,
            "played": self.played,
            "points": self.points,
            "goal_difference": self.goal_difference,
            "goals_for": self.goals_for,
            "goals_against": self.goals_against,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
        }


@dataclass(frozen=True)
class TournamentState:
    """Current fixtures, results, standings, and derived group context."""

    fixtures: list[FixtureRecord]
    results: list[ResultRecord]
    standings: dict[str, list[GroupStanding]]
    result_checks: list[dict[str, Any]] = field(default_factory=list)

    def open_fixtures(self, *, now: dt.datetime | None = None, cutoff_minutes: int = 5) -> list[FixtureRecord]:
        now = now or dt.datetime.now(dt.timezone.utc)
        cutoff = dt.timedelta(minutes=cutoff_minutes)
        open_rows = []
        for fixture in self.fixtures_without_results():
            kickoff = fixture.kickoff_at
            if kickoff is not None and kickoff - cutoff <= now:
                continue
            open_rows.append(fixture)
        return sorted(open_rows, key=lambda item: (item.event_date, item.home_team.name, item.away_team.name))

    def fixtures_without_results(self) -> list[FixtureRecord]:
        result_keys = {result.fixture_key for result in self.results}
        return sorted(
            [
                fixture
                for fixture in self.fixtures
                if fixture.key not in result_keys and fixture.status != "final"
            ],
            key=lambda item: (item.event_date, item.home_team.name, item.away_team.name),
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
