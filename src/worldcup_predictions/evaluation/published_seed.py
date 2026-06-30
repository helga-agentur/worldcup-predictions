"""Archived published prediction seed rows.

The seed preserves pre-refactor published tips for matches that were already
finished before the new hourly snapshot history existed. It is not a model
input for future matches; it only keeps public historical accounting stable.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


SEED_RESOURCE = "published_prediction_seed.json"


def load_published_prediction_seed_rows() -> list[dict[str, Any]]:
    """Load bundled archived pre-refactor prediction rows."""

    try:
        text = files("worldcup_predictions.resources").joinpath(SEED_RESOURCE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    payload = json.loads(text)
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]
