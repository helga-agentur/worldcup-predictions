"""Player-level squad value and composition signal plugin."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any

from worldcup_predictions.core.constants import SOURCE_PLAYER_IMPACT
from worldcup_predictions.core.contracts import Artifact, Diagnostic, Signal
from worldcup_predictions.core.datasets import PLAYER_IMPACT, SQUAD_PLAYERS, SQUAD_VALUES
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.football import age_at_tournament, clamp, normalize_position, optional_float, scaled_log_adjustment
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state


class PlayerImpactPlugin(BasePlugin):
    """Convert squad-player rows into conservative team xG factors."""

    id = "player_impact"
    version = "0.1.0"
    priority = 310
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Derive player/squad market-value impact from normalized squad rows.",
        datasets_read=(SQUAD_PLAYERS, SQUAD_VALUES),
        datasets_written=(SQUAD_VALUES, PLAYER_IMPACT),
        signals_emitted=(TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR),
        confidence_policy="Player-value effects are coverage weighted and capped so they cannot dominate team/result/market layers.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; player impact was skipped.", self.id)],
            )
        squad_rows = context.storage.read_records(SQUAD_PLAYERS, latest_only=True)
        value_rows = squad_value_rows(squad_rows)
        impact_rows = player_impact_rows(squad_rows)
        value_count = context.storage.write_records(SQUAD_VALUES, value_rows, source=self.id, run_id=context.run_id)
        impact_count = context.storage.write_records(PLAYER_IMPACT, impact_rows, source=self.id, run_id=context.run_id)
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state
        signals = signals_from_impact_rows(state.open_fixtures(), impact_rows)
        diagnostics = []
        if not squad_rows:
            diagnostics.append(Diagnostic("info", "No squad-player rows are available; player-impact signals were skipped.", self.id))
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                Artifact(SQUAD_VALUES, "structured_dataset", self.id, data={"rows": value_count}),
                Artifact(PLAYER_IMPACT, "structured_dataset", self.id, data={"rows": impact_count, "signals": len(signals)}),
            ],
            diagnostics=diagnostics,
            metadata={"squad_players": len(squad_rows), "squad_values": value_count, "player_impact": impact_count, "signals": len(signals)},
        )


def squad_value_rows(squad_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    teams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in squad_rows:
        teams[str(row.get("fifa_code") or row.get("team"))].append(row)
    summaries = []
    for key, rows in sorted(teams.items()):
        valued = [row for row in rows if optional_float(row.get("market_value_in_eur"))]
        values = sorted([float(row.get("market_value_in_eur") or 0.0) for row in valued], reverse=True)
        attack = sum(float(row.get("market_value_in_eur") or 0.0) for row in valued if normalize_position(row.get("position")) == "attack")
        defense = sum(float(row.get("market_value_in_eur") or 0.0) for row in valued if normalize_position(row.get("position")) in {"defense", "goalkeeper"})
        team_name = str(rows[0].get("team") or key)
        summaries.append(
            {
                "record_key": key,
                "team": team_name,
                "fifa_code": rows[0].get("fifa_code"),
                "matched_players": len(valued),
                "squad_players": len(rows),
                "match_rate": len(valued) / len(rows) if rows else 0.0,
                "total_market_value_eur": sum(values) if values else None,
                "top15_market_value_eur": sum(values[:15]) if values else None,
                "attack_market_value_eur": attack or None,
                "defense_market_value_eur": defense or None,
                "source": "squad_players",
            }
        )
    return summaries


def player_impact_rows(squad_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    teams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    squad_counts = Counter()
    for row in squad_rows:
        key = str(row.get("fifa_code") or row.get("team") or "")
        if not key:
            continue
        squad_counts[key] += 1
        value = optional_float(row.get("market_value_in_eur")) or 0.0
        if value <= 0:
            continue
        teams[key].append(
            {
                "team": row.get("team"),
                "fifa_code": row.get("fifa_code"),
                "value": value,
                "age": age_at_tournament(row.get("date_of_birth")),
                "bucket": normalize_position(row.get("position")),
                "club": row.get("current_club_name") or "",
                "match_score": optional_float(row.get("match_score")) or 0.0,
            }
        )

    raw = {team_key: _team_player_summary(players, squad_counts[team_key]) for team_key, players in teams.items()}
    attack_median = median([row["attack_index_eur"] for row in raw.values() if row["attack_index_eur"] > 0] or [1.0])
    defense_median = median([row["defense_index_eur"] for row in raw.values() if row["defense_index_eur"] > 0] or [1.0])
    club_pair_median = median([row["same_club_pair_share"] for row in raw.values()] or [0.0])

    rows = []
    for team_key, row in sorted(raw.items()):
        coverage = row["valued_players"] / row["squad_players"] if row["squad_players"] else 0.0
        confidence = clamp(coverage * row["match_score_average"], 0.0, 1.0)
        age_adjustment = (row["prime_age_share"] - 0.55) * 0.018 * confidence
        cohesion_adjustment = (row["same_club_pair_share"] - club_pair_median) * 0.06 * confidence
        attack_adjustment = scaled_log_adjustment(row["attack_index_eur"], attack_median, confidence, cap=0.055) + age_adjustment
        defense_adjustment = scaled_log_adjustment(row["defense_index_eur"], defense_median, confidence, cap=0.055) + age_adjustment
        total_adjustment = clamp((attack_adjustment - defense_adjustment) * 0.12 + cohesion_adjustment, -0.015, 0.015)
        rows.append(
            {
                "record_key": team_key,
                "team": row["team"],
                "fifa_code": row["fifa_code"],
                **row,
                "confidence": confidence,
                "attack_adjustment": clamp(attack_adjustment, -0.06, 0.06),
                "defense_adjustment": clamp(defense_adjustment, -0.06, 0.06),
                "total_goals_adjustment": total_adjustment,
                "source": "squad player market-value layer",
            }
        )
    return rows


def _team_player_summary(players: list[dict[str, Any]], squad_count: int) -> dict[str, Any]:
    sorted_players = sorted(players, key=lambda row: row["value"], reverse=True)
    top_xi = _select_top_xi(sorted_players)
    top_xvi = sorted_players[:16]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in top_xvi:
        buckets[player["bucket"]].append(player)
    ages = [player["age"] for player in top_xi if player.get("age") is not None]
    first = sorted_players[0]
    return {
        "team": first.get("team"),
        "fifa_code": first.get("fifa_code"),
        "squad_players": squad_count,
        "valued_players": len(sorted_players),
        "top_xi_value_eur": sum(player["value"] for player in top_xi),
        "top_xvi_value_eur": sum(player["value"] for player in top_xvi),
        "attack_index_eur": _indexed_value(buckets, {"attack": 1.0, "midfield": 0.35, "defense": 0.05}),
        "defense_index_eur": _indexed_value(buckets, {"defense": 1.0, "goalkeeper": 0.70, "midfield": 0.25}),
        "midfield_index_eur": sum(player["value"] for player in buckets.get("midfield", [])),
        "goalkeeper_index_eur": sum(player["value"] for player in buckets.get("goalkeeper", [])),
        "average_top_xi_age": sum(ages) / len(ages) if ages else None,
        "prime_age_share": sum(1 for age in ages if 23 <= age <= 30) / len(ages) if ages else 0.5,
        "same_club_pair_share": _same_club_pair_share(top_xvi),
        "match_score_average": sum(player["match_score"] for player in sorted_players) / len(sorted_players) if sorted_players else 0.0,
    }


def signals_from_impact_rows(fixtures, impact_rows: list[dict[str, Any]]) -> list[Signal]:
    by_team = {str(row.get("fifa_code") or row.get("team")): row for row in impact_rows}
    signals: list[Signal] = []
    for fixture in fixtures:
        for side, team in (("home", fixture.home_team), ("away", fixture.away_team)):
            row = by_team.get(team.key)
            if not row:
                continue
            confidence = float(row.get("confidence") or 0.0)
            attack_adjustment = float(row.get("attack_adjustment") or 0.0)
            if abs(attack_adjustment) >= 0.005:
                signals.append(
                    Signal(
                        name=TEAM_EXPECTED_GOALS_FACTOR,
                        source=SOURCE_PLAYER_IMPACT,
                        fixture_key=fixture.key,
                        value=1.0 + attack_adjustment,
                        weight=0.55,
                        confidence=confidence,
                        rationale="Player-value squad attack index.",
                        metadata={"side": side, "team": row.get("team"), "fifa_code": row.get("fifa_code")},
                    )
                )
            total_adjustment = float(row.get("total_goals_adjustment") or 0.0)
            if side == "home" and abs(total_adjustment) >= 0.003:
                signals.append(
                    Signal(
                        name=TOTAL_GOALS_FACTOR,
                        source=SOURCE_PLAYER_IMPACT,
                        fixture_key=fixture.key,
                        value=1.0 + total_adjustment,
                        weight=0.45,
                        confidence=confidence,
                        rationale="Player-value squad composition total-goals adjustment.",
                        metadata={"team": row.get("team"), "fifa_code": row.get("fifa_code")},
                    )
                )
    return signals


def _select_top_xi(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        buckets[player["bucket"]].append(player)
    selected = []
    for bucket, count in {"goalkeeper": 1, "defense": 4, "midfield": 3, "attack": 3}.items():
        selected.extend(sorted(buckets[bucket], key=lambda row: row["value"], reverse=True)[:count])
    selected_ids = {id(player) for player in selected}
    selected.extend([player for player in players if id(player) not in selected_ids][: max(0, 11 - len(selected))])
    return selected[:11]


def _indexed_value(buckets: dict[str, list[dict[str, Any]]], weights: dict[str, float]) -> float:
    return sum(sum(player["value"] for player in buckets.get(bucket, [])) * weight for bucket, weight in weights.items())


def _same_club_pair_share(players: list[dict[str, Any]]) -> float:
    if len(players) < 2:
        return 0.0
    clubs = Counter(player.get("club") or "" for player in players if player.get("club"))
    pairs = sum(count * (count - 1) / 2 for count in clubs.values() if count >= 2)
    total_pairs = len(players) * (len(players) - 1) / 2
    return pairs / total_pairs if total_pairs else 0.0
