"""Canonical knockout slot identities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from worldcup_predictions.simulations.worldcup_2026 import ROUND_NAMES

if TYPE_CHECKING:
    from worldcup_predictions.tournament.contracts import FixtureRecord, TeamRef


SLOT_CODE_RE = re.compile(r"^[WL]\d{1,3}$", re.I)
WINNER_SLOT_TEXT_RE = re.compile(r"^(?:winner|sieger)\s+(?:of\s+)?(?:match|spiel|m)?\s*(?P<number>\d{1,3})$", re.I)
LOSER_SLOT_TEXT_RE = re.compile(r"^(?:loser|verlierer)\s+(?:of\s+)?(?:match|spiel|m)?\s*(?P<number>\d{1,3})$", re.I)
ROUND_LABELS_DE = {
    "Round of 32": "Sechzehntelfinal",
    "Round of 16": "Achtelfinal",
    "Quarter-final": "Viertelfinal",
    "Semi-final": "Halbfinal",
    "Final": "Final",
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
    if locale.casefold().startswith("de"):
        label = "Sieger" if prefix == "W" else "Verlierer"
        round_label = ROUND_LABELS_DE.get(round_name or "")
        if round_label:
            return f"{label} {round_label} {round_position}" if round_position else f"{label} {round_label}"
        return f"{label} Spiel {number}"
    label = "Winner" if prefix == "W" else "Loser"
    if round_name:
        return f"{label} {round_name} {round_position}" if round_position else f"{label} {round_name}"
    return f"{label} Match {number}"


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
