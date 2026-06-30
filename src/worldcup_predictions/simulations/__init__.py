"""Tournament simulation helpers."""

from worldcup_predictions.simulations.contracts import (
    SimulationOutcome,
    SimulationResult,
    SimulationSummary,
    TeamStanding,
)
from worldcup_predictions.simulations.monte_carlo import (
    SimulationInputs,
    TournamentSimulator,
    fallback_score_matrix,
    pair_key,
    sample_score,
)

__all__ = [
    "SimulationInputs",
    "SimulationOutcome",
    "SimulationResult",
    "SimulationSummary",
    "TeamStanding",
    "TournamentSimulator",
    "fallback_score_matrix",
    "pair_key",
    "sample_score",
]
