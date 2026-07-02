"""Monte Carlo tournament simulator."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from string import ascii_uppercase
from typing import Any

from worldcup_predictions.core.contracts import Fixture, ScoreMatrixEntry, ScoreTip
from worldcup_predictions.simulations.contracts import (
    SimulationOutcome,
    SimulationResult,
    SimulationSummary,
    TeamStanding,
)
from worldcup_predictions.simulations.worldcup_2026 import (
    NEXT_ROUNDS,
    ROUND_NAMES,
    group_letter,
    next_round_matches,
    round_of_32_matches,
)

DEFAULT_TOP_SCORER_GOALS = (5, 6, 6, 7, 7, 8)
STAGE_GROUP = "Group stage"
STAGE_ROUND_OF_32 = "Round of 32"
STAGE_CHAMPION = "Champion"

# Penalty shootout winner from an Elo-logistic with a wide 500-point scale (flatter than
# the standard 400): equal teams are 50/50 and the favorite's edge grows smoothly with the
# rating gap. This is preferred over a raw outright-probability ratio, which compresses the
# field (most teams ~0) and over-favors top seeds in shootouts.
PENALTY_ELO_SCALE = 500.0


def pair_key(home_team: str, away_team: str) -> str:
    return f"{home_team}|{away_team}"


def bucket_more_than_15(value: int) -> str:
    if value > 15:
        return "more than 15"
    return str(value)


@dataclass(frozen=True)
class SimulationInputs:
    """Inputs required to simulate the tournament from any current state."""

    fixtures: list[Fixture]
    known_results: dict[str, ScoreTip] = field(default_factory=dict)
    score_matrices: dict[str, list[ScoreMatrixEntry]] = field(default_factory=dict)
    team_strengths: dict[str, float] = field(default_factory=dict)
    team_ratings: dict[str, float] = field(default_factory=dict)
    top_scorer_goals_prior: tuple[int, ...] = DEFAULT_TOP_SCORER_GOALS


class TournamentSimulator:
    """Run a 2026 World Cup simulation from fixtures, results, and score matrices."""

    def __init__(
        self,
        inputs: SimulationInputs,
        *,
        iterations: int = 20_000,
        seed: int = 20260611,
    ) -> None:
        if iterations < 1:
            raise ValueError("iterations must be at least 1")
        self.inputs = inputs
        self.iterations = iterations
        self.seed = seed
        self._group_labels_cache: dict[str, str] | None = None

    def run(self) -> SimulationSummary:
        rng = random.Random(self.seed)
        team_groups = self._team_groups()
        champion_counter: Counter[str] = Counter()
        nil_nil_counter: Counter[str] = Counter()
        top_scorer_counter: Counter[str] = Counter()
        stage_counters: dict[str, Counter[str]] = defaultdict(Counter)
        goals_counters: dict[str, Counter[str]] = defaultdict(Counter)
        group_rank_counters: dict[str, Counter[str]] = defaultdict(Counter)
        qualified_counters: dict[str, Counter[str]] = defaultdict(Counter)
        sample_results: list[SimulationResult] = []

        for iteration in range(self.iterations):
            outcome = self._simulate_once(rng)
            if iteration == 0:
                sample_results = outcome.fixture_results
            if outcome.champion:
                champion_counter[outcome.champion] += 1
            nil_nil_counter[bucket_more_than_15(outcome.nil_nil_count)] += 1
            top_scorer_counter[bucket_more_than_15(outcome.top_scorer_goals)] += 1
            for team, stage in outcome.team_stage.items():
                stage_counters[team][stage] += 1
            for team, goals in outcome.team_goals.items():
                goals_counters[team][bucket_more_than_15(goals)] += 1
            for team, rank in outcome.group_ranks.items():
                group_rank_counters[team][str(rank)] += 1
            for team in outcome.team_stage:
                qualified_counters[team]["yes" if team in outcome.group_qualified else "no"] += 1

        distributions = {
            "champion": self._counter_distribution(champion_counter),
            "nil_nil": self._counter_distribution(nil_nil_counter, sort_numeric=True),
            "top_scorer_goals": self._counter_distribution(top_scorer_counter, sort_numeric=True),
            "team_stage": {
                team: self._counter_distribution(counter)
                for team, counter in sorted(stage_counters.items())
            },
            "team_goals": {
                team: self._counter_distribution(counter, sort_numeric=True)
                for team, counter in sorted(goals_counters.items())
            },
            "group_rank": {
                team: self._counter_distribution(counter, sort_numeric=True)
                for team, counter in sorted(group_rank_counters.items())
            },
            "group_qualified": {
                team: self._counter_distribution(counter)
                for team, counter in sorted(qualified_counters.items())
            },
            "team_groups": team_groups,
        }
        champion_blend = self._champion_market_blend(champion_counter)
        if champion_blend is not None:
            distributions["champion_market_blend"] = champion_blend
        metadata = {
            "fixtures": len(self.inputs.fixtures),
            "known_results": len(self.inputs.known_results),
            "score_matrices": len(self.inputs.score_matrices),
            "sample_results": [result.to_dict() for result in sample_results],
        }
        return SimulationSummary(
            iterations=self.iterations,
            seed=self.seed,
            distributions=distributions,
            metadata=metadata,
        )

    def _simulate_once(self, rng: random.Random) -> SimulationOutcome:
        team_stage: dict[str, str] = {}
        team_goals: dict[str, int] = defaultdict(int)
        fixture_results: list[SimulationResult] = []
        standings_by_group: dict[str, dict[str, TeamStanding]] = defaultdict(dict)
        nil_nil_count = 0

        group_labels = self._group_labels()
        for fixture in self._group_fixtures():
            home = fixture.home_team
            away = fixture.away_team
            group = group_labels.get(fixture.key, "")
            team_stage.setdefault(home, STAGE_GROUP)
            team_stage.setdefault(away, STAGE_GROUP)
            score, source = self._score_for_fixture(
                fixture.key,
                home,
                away,
                rng,
            )
            winner = self._winner_from_score(home, away, score, allow_draw=True)
            result = SimulationResult(
                match_id=fixture.key,
                home_team=home,
                away_team=away,
                score=score,
                stage=fixture.stage or STAGE_GROUP,
                group=group,
                winner=winner,
                source=source,
            )
            fixture_results.append(result)
            team_goals[home] += score.home
            team_goals[away] += score.away
            nil_nil_count += int(score.home == 0 and score.away == 0)
            self._standing(standings_by_group[group], group, home).record(score.home, score.away)
            self._standing(standings_by_group[group], group, away).record(score.away, score.home)

        placements: dict[str, str] = {}
        third_rankings: list[dict[str, Any]] = []
        group_ranks: dict[str, int] = {}
        for group, standings in standings_by_group.items():
            ranked = self._rank_group(list(standings.values()), rng)
            for index, standing in enumerate(ranked, start=1):
                group_ranks[standing.team] = index
            if len(ranked) >= 1:
                placements[f"1{group}"] = ranked[0].team
            if len(ranked) >= 2:
                placements[f"2{group}"] = ranked[1].team
            if len(ranked) >= 3:
                third = ranked[2]
                placements[f"3{group}"] = third.team
                third_rankings.append(
                    {
                        "group": group,
                        "team": third.team,
                        "points": third.points,
                        "goal_difference": third.goal_difference,
                        "goals_for": third.goals_for,
                        "random": rng.random(),
                    }
                )

        third_rankings.sort(
            key=lambda item: (
                -int(item["points"]),
                -int(item["goal_difference"]),
                -int(item["goals_for"]),
                float(item["random"]),
            )
        )
        qualified = set(placements[key] for key in placements if key.startswith(("1", "2")))
        qualified.update(entry["team"] for entry in third_rankings[:8])
        for team in qualified:
            team_stage[team] = STAGE_ROUND_OF_32

        champion = self._simulate_knockout(
            placements,
            third_rankings,
            team_stage,
            team_goals,
            fixture_results,
            rng,
        )

        top_scorer_goals = rng.choice(self.inputs.top_scorer_goals_prior)
        return SimulationOutcome(
            champion=champion,
            team_stage=dict(team_stage),
            team_goals=dict(team_goals),
            nil_nil_count=nil_nil_count,
            top_scorer_goals=top_scorer_goals,
            group_ranks=group_ranks,
            group_qualified=qualified,
            fixture_results=fixture_results,
        )

    def _group_fixtures(self) -> list[Fixture]:
        fixtures = [fixture for fixture in self.inputs.fixtures if fixture.group]
        if fixtures:
            return fixtures
        return [
            fixture
            for fixture in self.inputs.fixtures
            if str(fixture.stage or "").casefold() in {"group", "group stage", "gruppe", "gruppenphase"}
        ]

    def _team_groups(self) -> dict[str, str]:
        team_groups: dict[str, str] = {}
        group_labels = self._group_labels()
        for fixture in self._group_fixtures():
            group = group_labels.get(fixture.key, "")
            if group:
                team_groups.setdefault(fixture.home_team, group)
                team_groups.setdefault(fixture.away_team, group)
        return dict(sorted(team_groups.items()))

    def _group_labels(self) -> dict[str, str]:
        """Group label per group-stage fixture key.

        Sources can deliver group fixtures as stage-only rows without group
        names. Every group is a closed round-robin, so connected components of
        the "plays against" graph recover group membership for such fixtures;
        components are lettered deterministically by earliest kickoff. Without
        this, unlabeled fixtures collapse into a single pseudo-group whose
        placements can never fill the knockout bracket, and every iteration
        ends with the same fallback champion.
        """

        if self._group_labels_cache is None:
            self._group_labels_cache = self._build_group_labels(self._group_fixtures())
        return self._group_labels_cache

    def _build_group_labels(self, fixtures: list[Fixture]) -> dict[str, str]:
        labels: dict[str, str] = {}
        unlabeled: list[Fixture] = []
        for fixture in fixtures:
            label = group_letter(fixture.group) or str(fixture.group or "")
            if label:
                labels[fixture.key] = label
            else:
                unlabeled.append(fixture)
        if not unlabeled:
            return labels

        parent: dict[str, str] = {}

        def find(team: str) -> str:
            parent.setdefault(team, team)
            while parent[team] != team:
                parent[team] = parent[parent[team]]
                team = parent[team]
            return team

        for fixture in unlabeled:
            parent[find(fixture.home_team)] = find(fixture.away_team)
        components: dict[str, list[Fixture]] = defaultdict(list)
        for fixture in unlabeled:
            components[find(fixture.home_team)].append(fixture)

        used_labels = set(labels.values())
        free_letters = (letter for letter in ascii_uppercase if letter not in used_labels)
        ordered = sorted(
            components.values(),
            key=lambda component: min((fixture.event_date, fixture.home_team) for fixture in component),
        )
        for index, component in enumerate(ordered):
            label = next(free_letters, None) or f"Z{index}"
            for fixture in component:
                labels[fixture.key] = label
        return labels

    def _simulate_knockout(
        self,
        placements: dict[str, str],
        third_rankings: list[dict[str, Any]],
        team_stage: dict[str, str],
        team_goals: dict[str, int],
        fixture_results: list[SimulationResult],
        rng: random.Random,
    ) -> str | None:
        previous_winners: dict[str, str] = {}
        matches = round_of_32_matches(placements, third_rankings)
        for match in matches:
            winner = self._simulate_knockout_match(
                match["match_id"],
                match["home"],
                match["away"],
                team_stage,
                team_goals,
                fixture_results,
                rng,
            )
            if winner:
                previous_winners[str(match["match_id"])] = winner

        for round_template in NEXT_ROUNDS:
            current_winners: dict[str, str] = {}
            for match in next_round_matches(previous_winners, round_template):
                winner = self._simulate_knockout_match(
                    match["match_id"],
                    match["home"],
                    match["away"],
                    team_stage,
                    team_goals,
                    fixture_results,
                    rng,
                )
                if winner:
                    current_winners[str(match["match_id"])] = winner
            previous_winners.update(current_winners)

        champion = previous_winners.get("M104")
        if champion:
            team_stage[champion] = STAGE_CHAMPION
            return champion
        if placements:
            fallback = next(iter(placements.values()))
            team_stage[fallback] = STAGE_CHAMPION
            return fallback
        return None

    def _simulate_knockout_match(
        self,
        match_id: str | None,
        home: str | None,
        away: str | None,
        team_stage: dict[str, str],
        team_goals: dict[str, int],
        fixture_results: list[SimulationResult],
        rng: random.Random,
    ) -> str | None:
        match_id = str(match_id)
        stage = ROUND_NAMES.get(match_id, "Knockout stage")
        if not home or not away:
            winner = home or away
            if winner:
                team_stage[winner] = self._next_stage(stage)
            return winner
        score, source = self._score_for_fixture(match_id, home, away, rng)
        winner = self._winner_from_score(home, away, score, allow_draw=False, rng=rng)
        team_stage.setdefault(home, STAGE_GROUP)
        team_stage.setdefault(away, STAGE_GROUP)
        team_stage[winner] = self._next_stage(stage)
        team_goals[home] += score.home
        team_goals[away] += score.away
        fixture_results.append(
            SimulationResult(
                match_id=match_id,
                home_team=home,
                away_team=away,
                score=score,
                stage=stage,
                winner=winner,
                source=source,
            )
        )
        return winner

    def _score_for_fixture(
        self,
        fixture_key: str,
        home: str,
        away: str,
        rng: random.Random,
    ) -> tuple[ScoreTip, str]:
        known = self._known_result(fixture_key, home, away)
        if known is not None:
            return known, "fixed"
        matrix = self._score_matrix(fixture_key, home, away)
        if not matrix:
            matrix = fallback_score_matrix()
        return sample_score(matrix, rng), "simulated"

    def _known_result(self, fixture_key: str, home: str, away: str) -> ScoreTip | None:
        candidates = [
            fixture_key,
            pair_key(home, away),
        ]
        for candidate in candidates:
            score = self.inputs.known_results.get(candidate)
            if score is not None:
                return score
        reverse_score = self.inputs.known_results.get(pair_key(away, home))
        if reverse_score is not None:
            return ScoreTip(reverse_score.away, reverse_score.home)
        return None

    def _score_matrix(self, fixture_key: str, home: str, away: str) -> list[ScoreMatrixEntry]:
        candidates = [
            fixture_key,
            pair_key(home, away),
        ]
        for candidate in candidates:
            matrix = self.inputs.score_matrices.get(candidate)
            if matrix:
                return matrix
        reverse_matrix = self.inputs.score_matrices.get(pair_key(away, home))
        if reverse_matrix:
            return [
                ScoreMatrixEntry(
                    home=entry.away,
                    away=entry.home,
                    probability=entry.probability,
                    metadata=entry.metadata,
                )
                for entry in reverse_matrix
            ]
        return []

    def _winner_from_score(
        self,
        home: str,
        away: str,
        score: ScoreTip,
        *,
        allow_draw: bool,
        rng: random.Random | None = None,
    ) -> str | None:
        if score.home > score.away:
            return home
        if score.home < score.away:
            return away
        if allow_draw:
            return None
        rng = rng or random.Random(self.seed)
        home_probability = self._penalty_home_probability(home, away)
        return home if rng.random() < home_probability else away

    def _penalty_home_probability(self, home: str, away: str) -> float:
        """Probability the home side wins a shootout (Elo-logistic, strength-ratio fallback)."""

        home_rating = self.inputs.team_ratings.get(home)
        away_rating = self.inputs.team_ratings.get(away)
        if home_rating is not None and away_rating is not None:
            return 1.0 / (1.0 + 10 ** (-(home_rating - away_rating) / PENALTY_ELO_SCALE))
        home_strength = self.inputs.team_strengths.get(home, 1.0)
        away_strength = self.inputs.team_strengths.get(away, 1.0)
        total = home_strength + away_strength
        return home_strength / total if total > 0 else 0.5

    def _next_stage(self, stage: str) -> str:
        if stage == "Final":
            return STAGE_CHAMPION
        if stage == "Semi-final":
            return "Final"
        if stage == "Quarter-final":
            return "Semi-final"
        if stage == "Round of 16":
            return "Quarter-final"
        if stage == "Round of 32":
            return "Round of 16"
        return stage

    def _standing(
        self,
        standings: dict[str, TeamStanding],
        group: str,
        team: str,
    ) -> TeamStanding:
        if team not in standings:
            standings[team] = TeamStanding(team=team, group=group)
        return standings[team]

    def _rank_group(self, standings: list[TeamStanding], rng: random.Random) -> list[TeamStanding]:
        tie_breakers = {standing.team: rng.random() for standing in standings}
        return sorted(
            standings,
            key=lambda standing: (
                -standing.points,
                -standing.goal_difference,
                -standing.goals_for,
                tie_breakers[standing.team],
            ),
        )

    def _champion_market_blend(self, champion_counter: Counter[str], *, sim_weight: float = 0.45) -> list[dict[str, Any]] | None:
        """Blend simulated champion probabilities with bookmaker outright probabilities.

        Bookmaker outrights are an efficient champion estimate and stabilize the noisy
        simulation tail. Uses the legacy 0.45 simulation / 0.55 market split. Returns
        ``None`` when no outright strengths are available.
        """

        strengths = self.inputs.team_strengths
        total = sum(champion_counter.values())
        if not strengths or total <= 0:
            return None
        teams = set(champion_counter) | set(self._team_groups())
        outright = {team: max(0.0, float(strengths.get(team, 0.0) or 0.0)) for team in teams}
        outright_total = sum(outright.values())
        if outright_total <= 0:
            return None
        blended = {
            team: sim_weight * (champion_counter.get(team, 0) / total) + (1 - sim_weight) * (outright[team] / outright_total)
            for team in teams
        }
        blended_total = sum(blended.values()) or 1.0
        rows = [
            {
                "answer": team,
                "count": round(probability / blended_total * total),
                "probability": probability / blended_total,
            }
            for team, probability in blended.items()
            if probability > 0
        ]
        rows.sort(key=lambda row: (-row["probability"], row["answer"]))
        return rows

    def _counter_distribution(
        self,
        counter: Counter[str],
        *,
        sort_numeric: bool = False,
    ) -> list[dict[str, Any]]:
        total = sum(counter.values())
        if total <= 0:
            return []

        def sort_key(item: tuple[str, int]) -> tuple[Any, ...]:
            answer, count = item
            if sort_numeric:
                numeric = 999 if answer == "more than 15" else int(answer)
                return (numeric, -count, answer)
            return (-count, answer)

        return [
            {
                "answer": answer,
                "count": count,
                "probability": count / total,
            }
            for answer, count in sorted(counter.items(), key=sort_key)
        ]


def sample_score(entries: list[ScoreMatrixEntry], rng: random.Random) -> ScoreTip:
    positive_entries = [entry for entry in entries if entry.probability > 0]
    total = sum(entry.probability for entry in positive_entries)
    if total <= 0:
        return ScoreTip(1, 1)
    target = rng.random() * total
    cumulative = 0.0
    for entry in positive_entries:
        cumulative += entry.probability
        if cumulative >= target:
            return entry.as_tip()
    return positive_entries[-1].as_tip()


def fallback_score_matrix() -> list[ScoreMatrixEntry]:
    """Return a conservative neutral matrix when no model matrix is available."""

    return [
        ScoreMatrixEntry(1, 1, 0.18, {"fallback": True}),
        ScoreMatrixEntry(1, 0, 0.16, {"fallback": True}),
        ScoreMatrixEntry(0, 1, 0.14, {"fallback": True}),
        ScoreMatrixEntry(2, 1, 0.12, {"fallback": True}),
        ScoreMatrixEntry(1, 2, 0.10, {"fallback": True}),
        ScoreMatrixEntry(0, 0, 0.10, {"fallback": True}),
        ScoreMatrixEntry(2, 0, 0.08, {"fallback": True}),
        ScoreMatrixEntry(0, 2, 0.07, {"fallback": True}),
        ScoreMatrixEntry(2, 2, 0.05, {"fallback": True}),
    ]
