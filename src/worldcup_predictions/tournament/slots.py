"""Canonical knockout slot identities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from worldcup_predictions.core.i18n import load_translation_catalog
from worldcup_predictions.simulations.worldcup_2026 import ROUND_NAMES

if TYPE_CHECKING:
    from worldcup_predictions.tournament.contracts import FixtureRecord, TeamRef


SLOT_CODE_RE = re.compile(r"^[WL]\d{1,3}$", re.I)
WINNER_SLOT_TEXT_RE = re.compile(r"^(?:winner|sieger)\s+(?:of\s+)?(?:match|spiel|m)?\s*(?P<number>\d{1,3})$", re.I)
LOSER_SLOT_TEXT_RE = re.compile(r"^(?:loser|verlierer)\s+(?:of\s+)?(?:match|spiel|m)?\s*(?P<number>\d{1,3})$", re.I)
ROUND_SLOT_TEXT_RE = re.compile(
    r"^(?P<outcome>winner|sieger|loser|verlierer)\s+"
    r"(?P<round>round\s+of\s+32|round\s+of\s+16|quarter[- ]final|semi[- ]final|final|"
    r"sechzehntelfinal|achtelfinal|viertelfinal|halbfinal)\s+"
    r"(?:(?:match|spiel)\s+)?(?P<position>\d{1,2})$",
    re.I,
)
ROUND_LABEL_KEYS = {
    "Round of 32": "slot.round.round_of_32",
    "Round of 16": "slot.round.round_of_16",
    "Quarter-final": "slot.round.quarter_final",
    "Semi-final": "slot.round.semi_final",
    "Final": "slot.round.final",
}
ROUND_ALIASES = {
    "round of 32": "Round of 32",
    "sechzehntelfinal": "Round of 32",
    "round of 16": "Round of 16",
    "achtelfinal": "Round of 16",
    "quarter-final": "Quarter-final",
    "quarter final": "Quarter-final",
    "viertelfinal": "Quarter-final",
    "semi-final": "Semi-final",
    "semi final": "Semi-final",
    "halbfinal": "Semi-final",
    "final": "Final",
}


def canonical_slot_code(value: object) -> str:
    """Return the canonical slot code for source labels such as ``W75``."""

    text = " ".join(str(value or "").split())
    if not text:
        return ""
    compact = text.replace(" ", "").upper()
    if SLOT_CODE_RE.fullmatch(compact):
        return compact
    winner = WINNER_SLOT_TEXT_RE.fullmatch(text)
    if winner:
        return f"W{int(winner.group('number'))}"
    loser = LOSER_SLOT_TEXT_RE.fullmatch(text)
    if loser:
        return f"L{int(loser.group('number'))}"
    round_slot = ROUND_SLOT_TEXT_RE.fullmatch(text)
    if round_slot:
        outcome = round_slot.group("outcome").casefold()
        prefix = "W" if outcome in {"winner", "sieger"} else "L"
        round_name = ROUND_ALIASES.get(" ".join(round_slot.group("round").casefold().replace("-", " ").split()))
        position = int(round_slot.group("position"))
        if round_name:
            match_number = _match_number_for_round_position(round_name, position)
            if match_number is not None:
                return f"{prefix}{match_number}"
    return ""


def slot_team_ref(value: object) -> "TeamRef | None":
    code = canonical_slot_code(value)
    if not code:
        return None
    from worldcup_predictions.tournament.contracts import TeamRef

    return TeamRef(code, None) if code else None


def is_slot_team(team: "TeamRef") -> bool:
    return bool(canonical_slot_code(team.key) or canonical_slot_code(team.name) or canonical_slot_code(team.fifa_code))


def has_defined_teams(fixture: "FixtureRecord") -> bool:
    return bool(fixture.home_team.fifa_code and fixture.away_team.fifa_code) and not (
        is_slot_team(fixture.home_team) or is_slot_team(fixture.away_team)
    )


def slot_display_name(value: object, *, locale: str = "de") -> str:
    code = canonical_slot_code(value)
    if not code:
        return ""
    prefix = code[:1]
    number = int(code[1:])
    round_name, round_position = _slot_round_position(number)
    catalog = load_translation_catalog(locale)
    outcome = "winner" if prefix == "W" else "loser"
    round_label_key = ROUND_LABEL_KEYS.get(round_name or "")
    if round_label_key:
        round_label = catalog.translate(round_label_key)
        if round_position:
            return catalog.translate(f"slot.{outcome}_round_position", round=round_label, position=round_position)
        return catalog.translate(f"slot.{outcome}_round", round=round_label)
    return catalog.translate(f"slot.{outcome}_match", match=number)


def _slot_round_position(match_number: int) -> tuple[str | None, int | None]:
    round_name = ROUND_NAMES.get(f"M{match_number}")
    if not round_name:
        return None, None
    round_match_numbers = sorted(
        int(match_id.removeprefix("M"))
        for match_id, name in ROUND_NAMES.items()
        if name == round_name
    )
    if len(round_match_numbers) == 1:
        return round_name, None
    return round_name, round_match_numbers.index(match_number) + 1


def _match_number_for_round_position(round_name: str, position: int) -> int | None:
    round_match_numbers = sorted(
        int(match_id.removeprefix("M"))
        for match_id, name in ROUND_NAMES.items()
        if name == round_name
    )
    if not round_match_numbers:
        return None
    if len(round_match_numbers) == 1 and position == 1:
        return round_match_numbers[0]
    if 1 <= position <= len(round_match_numbers):
        return round_match_numbers[position - 1]
    return None
