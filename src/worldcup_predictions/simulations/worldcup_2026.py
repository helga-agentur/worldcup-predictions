"""2026 World Cup bracket helpers."""

from __future__ import annotations

from typing import Any

ROUND_OF_32: list[tuple[str, str, str]] = [
    ("M73", "2A", "2B"),
    ("M74", "1E", "3A/B/C/D/F"),
    ("M75", "1F", "2C"),
    ("M76", "1C", "2F"),
    ("M77", "1I", "3C/D/F/G/H"),
    ("M78", "2E", "2I"),
    ("M79", "1A", "3C/E/F/H/I"),
    ("M80", "1L", "3E/H/I/J/K"),
    ("M81", "1D", "3B/E/F/I/J"),
    ("M82", "1G", "3A/E/H/I/J"),
    ("M83", "2K", "2L"),
    ("M84", "1H", "2J"),
    ("M85", "1B", "3E/F/G/I/J"),
    ("M86", "1J", "2H"),
    ("M87", "1K", "3D/E/I/J/L"),
    ("M88", "2D", "2G"),
]

NEXT_ROUNDS: list[list[tuple[str, str, str]]] = [
    [
        ("M89", "M74", "M77"),
        ("M90", "M73", "M75"),
        ("M91", "M76", "M78"),
        ("M92", "M79", "M80"),
        ("M93", "M83", "M84"),
        ("M94", "M81", "M82"),
        ("M95", "M86", "M88"),
        ("M96", "M85", "M87"),
    ],
    [
        ("M97", "M89", "M90"),
        ("M98", "M93", "M94"),
        ("M99", "M91", "M92"),
        ("M100", "M95", "M96"),
    ],
    [
        ("M101", "M97", "M98"),
        ("M102", "M99", "M100"),
    ],
    [("M104", "M101", "M102")],
]

ROUND_NAMES: dict[str, str] = {
    "M73": "Round of 32",
    "M74": "Round of 32",
    "M75": "Round of 32",
    "M76": "Round of 32",
    "M77": "Round of 32",
    "M78": "Round of 32",
    "M79": "Round of 32",
    "M80": "Round of 32",
    "M81": "Round of 32",
    "M82": "Round of 32",
    "M83": "Round of 32",
    "M84": "Round of 32",
    "M85": "Round of 32",
    "M86": "Round of 32",
    "M87": "Round of 32",
    "M88": "Round of 32",
    "M89": "Round of 16",
    "M90": "Round of 16",
    "M91": "Round of 16",
    "M92": "Round of 16",
    "M93": "Round of 16",
    "M94": "Round of 16",
    "M95": "Round of 16",
    "M96": "Round of 16",
    "M97": "Quarter-final",
    "M98": "Quarter-final",
    "M99": "Quarter-final",
    "M100": "Quarter-final",
    "M101": "Semi-final",
    "M102": "Semi-final",
    "M104": "Final",
}


def group_letter(group_name: str | None) -> str | None:
    if not group_name:
        return None
    return (
        group_name.rsplit("_", 1)[-1]
        .replace("Group ", "")
        .replace("GROUP_", "")
        .replace("Gruppe ", "")
        .upper()
    )


def slot_candidates(slot: str) -> list[str]:
    if not slot.startswith("3"):
        return []
    return slot[1:].split("/")


def resolve_slot(
    slot: str,
    placements: dict[str, str],
    third_assignments: dict[str, str | None],
) -> str | None:
    if slot.startswith("3"):
        group = third_assignments.get(slot)
        return placements.get(f"3{group}") if group else None
    return placements.get(slot)


def assign_third_place_slots(third_rankings: list[dict[str, Any]]) -> dict[str, str | None]:
    """Assign best third-place teams to 2026 round-of-32 slots.

    FIFA's allocation table depends on exactly which third-place teams qualify.
    This constrained assignment preserves slot eligibility and favors stronger
    third-place teams when multiple valid allocations exist.
    """

    qualified = [entry["group"] for entry in third_rankings[:8]]
    third_slots = [
        slot
        for _match_id, home, away in ROUND_OF_32
        for slot in (home, away)
        if slot.startswith("3")
    ]
    strength = {entry["group"]: index for index, entry in enumerate(third_rankings)}
    candidates = {
        slot: [group for group in slot_candidates(slot) if group in qualified]
        for slot in third_slots
    }
    assignments: dict[str, str | None] = {}

    def search(remaining_slots: list[str], used: set[str]) -> bool:
        if not remaining_slots:
            return True
        slot = min(
            remaining_slots,
            key=lambda item: (len([group for group in candidates[item] if group not in used]), item),
        )
        options = sorted(
            [group for group in candidates[slot] if group not in used],
            key=lambda group: strength.get(group, 99),
        )
        for group in options:
            assignments[slot] = group
            if search([item for item in remaining_slots if item != slot], used | {group}):
                return True
            assignments.pop(slot, None)
        return False

    if search(third_slots, set()):
        return assignments

    return {
        slot: next((group for group in candidates[slot] if group in qualified), None)
        for slot in third_slots
    }


def round_of_32_matches(
    placements: dict[str, str],
    third_rankings: list[dict[str, Any]],
) -> list[dict[str, str | None]]:
    third_assignments = assign_third_place_slots(third_rankings)
    return [
        {
            "match_id": match_id,
            "home": resolve_slot(home_slot, placements, third_assignments),
            "away": resolve_slot(away_slot, placements, third_assignments),
            "home_slot": home_slot,
            "away_slot": away_slot,
        }
        for match_id, home_slot, away_slot in ROUND_OF_32
    ]


def next_round_matches(
    previous_winners: dict[str, str],
    round_template: list[tuple[str, str, str]],
) -> list[dict[str, str | None]]:
    return [
        {
            "match_id": match_id,
            "home": previous_winners.get(home_source),
            "away": previous_winners.get(away_source),
            "home_slot": home_source,
            "away_slot": away_source,
        }
        for match_id, home_source, away_source in round_template
    ]
