"""Outcome-driven skill scoring for H/D/A signal sources.

Every applied H/D/A adjustment stores its target probabilities, so each
signal source can be scored against the outcomes it tried to predict and its
blend weight scaled by demonstrated skill: sources that beat the published
forecast earn more weight, sources that lag it earn less, and every score is
recomputed from scratch each run so a source recovers the moment it starts
helping. Multipliers are evidence-shrunk and clamped, so small samples stay
near neutral and no source is ever silenced or crowned outright.

SRF experts get a dedicated backend: their signal only entered predictions
late in the tournament, but their raw picks cover every match, so their
consensus targets are rebuilt from ``srf_expert_predictions`` and scored over
the full history instead of the few fixtures the adjustment trail covers.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from worldcup_predictions.core.constants import SOURCE_SRF_EXPERTS
from worldcup_predictions.core.datasets import (
    PREDICTIONS,
    PUBLISHED_PREDICTION_LEDGER,
    SIGNAL_SKILL_CALIBRATION,
    SRF_EXPERT_PREDICTIONS,
)
from worldcup_predictions.core.signals import (
    EXPERT_HDA_PROBABILITIES,
    MARKET_HDA_PROBABILITIES,
    ML_HDA_PROBABILITIES,
)

HDA_SIGNALS = (MARKET_HDA_PROBABILITIES, EXPERT_HDA_PROBABILITIES, ML_HDA_PROBABILITIES)

# Sole emitter per signal type for adjustment rows recorded before the
# adjustments carried an explicit source.
LEGACY_SOURCE_BY_SIGNAL = {
    MARKET_HDA_PROBABILITIES: "market_odds",
    ML_HDA_PROBABILITIES: "ml_outcome",
}

# multiplier = clamp(1 + K * brier_edge * evidence, FLOOR, CAP)
# K calibrated on the 2026 tournament history: the SRF expert consensus
# (edge -0.154 over 100 matches) lands near the floor, the market's small
# positive edge earns a gentle boost, and small samples stay near 1.0.
SKILL_GAIN = 5.0
SKILL_MULTIPLIER_FLOOR = 0.25
SKILL_MULTIPLIER_CAP = 1.5
SKILL_EVIDENCE_HALFWEIGHT = 20


def outcome_one_hot(home_score: int, away_score: int) -> tuple[int, int, int]:
    if home_score > away_score:
        return (1, 0, 0)
    if away_score > home_score:
        return (0, 0, 1)
    return (0, 1, 0)


def brier(probs: tuple[float, float, float], actual: tuple[int, int, int]) -> float:
    return sum((p - o) ** 2 for p, o in zip(probs, actual))


def skill_multiplier(edge: float, samples: int) -> float:
    evidence = samples / (samples + SKILL_EVIDENCE_HALFWEIGHT)
    value = 1.0 + SKILL_GAIN * edge * evidence
    return max(SKILL_MULTIPLIER_FLOOR, min(SKILL_MULTIPLIER_CAP, value))


def build_signal_skill_rows(storage, state) -> list[dict[str, Any]]:
    """Score every H/D/A signal source against confirmed outcomes."""

    actual: dict[str, tuple[int, int, int]] = {
        result.fixture_key: outcome_one_hot(result.score.home, result.score.away)
        for result in state.results
    }
    if not actual:
        return []

    scores: dict[str, list[tuple[float, float]]] = defaultdict(list)

    # Backend 1: pregame prediction rows carry each applied signal's targets
    # alongside the published probabilities of the same forecast.
    for row in _latest_pregame_predictions(storage):
        fixture_key = str(row.get("fixture_key") or "")
        outcome = actual.get(fixture_key)
        if outcome is None:
            continue
        try:
            published = (float(row["prob_home"]), float(row["prob_draw"]), float(row["prob_away"]))
        except (KeyError, TypeError, ValueError):
            continue
        published_brier = brier(published, outcome)
        for adjustment in (row.get("metadata") or {}).get("signal_adjustments") or []:
            name = str(adjustment.get("signal") or "")
            if name not in HDA_SIGNALS:
                continue
            targets = (
                adjustment.get("target_home"),
                adjustment.get("target_draw"),
                adjustment.get("target_away"),
            )
            if any(value is None for value in targets):
                continue
            source = str(adjustment.get("source") or "") or LEGACY_SOURCE_BY_SIGNAL.get(name, "")
            if name == EXPERT_HDA_PROBABILITIES and not source:
                # The SRF experts are scored from their full pick history
                # below; unattributed legacy expert rows are skipped rather
                # than guessed at.
                continue
            if not source:
                continue
            if name == EXPERT_HDA_PROBABILITIES and source == SOURCE_SRF_EXPERTS:
                continue
            probs = tuple(float(value) for value in targets)
            scores[f"{name}:{source}"].append((brier(probs, outcome), published_brier))

    # Backend 2: SRF expert consensus rebuilt from stored picks, which cover
    # the whole tournament (picks lock at kickoff on SRF's side, so every
    # stored pick is pregame information regardless of when it was crawled).
    # The frozen published ledger is the pregame reference forecast here: it
    # spans the full tournament, while pregame prediction rows only exist
    # since prediction persistence began mid-tournament.
    published_by_fixture = _published_probabilities(storage)
    for fixture_key, consensus in _srf_consensus_targets(storage).items():
        outcome = actual.get(fixture_key)
        published = published_by_fixture.get(fixture_key)
        if outcome is None or published is None:
            continue
        scores[f"{EXPERT_HDA_PROBABILITIES}:{SOURCE_SRF_EXPERTS}"].append(
            (brier(consensus, outcome), brier(published, outcome))
        )

    rows = []
    for key, pairs in sorted(scores.items()):
        name, source = key.split(":", 1)
        samples = len(pairs)
        signal_brier = sum(pair[0] for pair in pairs) / samples
        published_brier = sum(pair[1] for pair in pairs) / samples
        edge = published_brier - signal_brier
        rows.append(
            {
                "record_key": key,
                "signal": name,
                "source": source,
                "samples": samples,
                "signal_brier": round(signal_brier, 4),
                "published_brier": round(published_brier, 4),
                "brier_edge": round(edge, 4),
                "multiplier": round(skill_multiplier(edge, samples), 4),
            }
        )
    return rows


def signal_skill_multipliers(storage, state) -> dict[str, float]:
    """Per-source blend multipliers, keyed "signal_name:source"."""

    try:
        rows = build_signal_skill_rows(storage, state)
    except Exception:
        return {}
    return {str(row["record_key"]): float(row["multiplier"]) for row in rows}


def write_signal_skill_rows(storage, state, *, run_id: str | None = None) -> int:
    rows = build_signal_skill_rows(storage, state)
    if not rows:
        return 0
    return storage.write_records(SIGNAL_SKILL_CALIBRATION, rows, source="signal_skill", run_id=run_id)


def _published_probabilities(storage) -> dict[str, tuple[float, float, float]]:
    published = {}
    for row in storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True):
        fixture_key = str(row.get("fixture_key") or "")
        try:
            published[fixture_key] = (
                float(row["prob_home"]),
                float(row["prob_draw"]),
                float(row["prob_away"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return published


_PREGAME_CACHE_KEY = "_signal_skill_pregame_rows"


def _latest_pregame_predictions(storage) -> list[dict[str, Any]]:
    """Latest prediction per fixture that was made before kickoff.

    Post-kickoff recomputations (day-one simulations, include_closed runs)
    know the results through live calibration and would leak them into the
    skill scores. The fixture key starts with the kickoff timestamp, so the
    guard is a plain string comparison.
    """

    cached = getattr(storage, _PREGAME_CACHE_KEY, None)
    if cached is not None:
        return cached
    latest: dict[str, dict[str, Any]] = {}
    for row in storage.read_records(PREDICTIONS, latest_only=False):
        fixture_key = str(row.get("fixture_key") or "")
        observed = str((row.get("_record") or {}).get("observed_at_utc") or "")
        kickoff = fixture_key.split("|", 1)[0]
        if not fixture_key or not observed or not kickoff or observed > kickoff:
            continue
        current = latest.get(fixture_key)
        current_observed = str((current.get("_record") or {}).get("observed_at_utc") or "") if current else ""
        if current is None or observed >= current_observed:
            latest[fixture_key] = row
    rows = list(latest.values())
    try:
        setattr(storage, _PREGAME_CACHE_KEY, rows)
    except Exception:
        pass
    return rows


def _srf_consensus_targets(storage) -> dict[str, tuple[float, float, float]]:
    picks: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for row in storage.read_records(SRF_EXPERT_PREDICTIONS, latest_only=True):
        fixture_key = str(row.get("fixture_key") or "")
        expert_id = str(row.get("expert_id") or "")
        try:
            tip = (int(row.get("tip_home")), int(row.get("tip_away")))
        except (TypeError, ValueError):
            continue
        if fixture_key and expert_id:
            picks[fixture_key][expert_id] = tip
    targets = {}
    for fixture_key, by_expert in picks.items():
        home = draw = away = 0
        for tip_home, tip_away in by_expert.values():
            if tip_home > tip_away:
                home += 1
            elif tip_home < tip_away:
                away += 1
            else:
                draw += 1
        total = home + draw + away
        if total:
            targets[fixture_key] = (home / total, draw / total, away / total)
    return targets
