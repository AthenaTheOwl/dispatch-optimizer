"""Greedy scorer — models a competent human dispatcher's mental model.

Simpler than the composite cost function. A human dispatcher considers:
- Distance (primary — look at the map, pick someone close)
- Deadline pressure (penalize tight/infeasible, but imprecise)
- Equipment waste (avoid obvious overqualification, but lenient)

Uses the route evaluator in expected mode (humans estimate mean times, not
pessimistic). The key difference from composite: no urgency multiplier on
distance, binary deadline penalties, and softer equipment penalties.
"""

from datetime import datetime
from typing import Any

from app.models import (
    Driver, Order, ColdStorage, TempRegime,
)
from app.simulation.distance import road_distance_km
from app.simulation.route_evaluator import (
    evaluate_route, build_stops, TravelTimeMode,
)
from app.solvers.base import CostFunction, CostResult


DEFAULT_DEADLINE_MISS_PENALTY = 50.0
DEFAULT_TIGHT_SLACK_PENALTY = 5.0
DEFAULT_TIGHT_SLACK_THRESHOLD = 15.0  # minutes
DEFAULT_HIGH_OVERQUAL_PENALTY = 3.0
DEFAULT_LOW_OVERQUAL_PENALTY = 0.5


def _cold_level(cs: ColdStorage) -> int:
    return {ColdStorage.NONE: 0, ColdStorage.COOLER: 1, ColdStorage.ACTIVE_FRIDGE: 2, ColdStorage.CRYO: 3}[cs]


def _min_cold_needed(order: Order) -> int:
    levels = []
    for r in order.required_temp_regimes:
        if r == TempRegime.AMBIENT:
            levels.append(0)
        elif r == TempRegime.REFRIGERATED:
            levels.append(1)
        elif r == TempRegime.FROZEN:
            levels.append(3)
        elif r == TempRegime.CRYOGENIC:
            levels.append(3)
    return max(levels) if levels else 0


class GreedyScorerCostFunction(CostFunction):
    """Models a competent human dispatcher's scoring.

    Uses expected-mode travel times (humans estimate mean, not pessimistic)
    and binary penalties instead of proportional risk.
    """

    def __init__(
        self,
        deadline_miss_penalty: float = DEFAULT_DEADLINE_MISS_PENALTY,
        tight_slack_penalty: float = DEFAULT_TIGHT_SLACK_PENALTY,
        tight_slack_threshold: float = DEFAULT_TIGHT_SLACK_THRESHOLD,
        high_overqual_penalty: float = DEFAULT_HIGH_OVERQUAL_PENALTY,
        low_overqual_penalty: float = DEFAULT_LOW_OVERQUAL_PENALTY,
    ):
        self.deadline_miss_penalty = deadline_miss_penalty
        self.tight_slack_penalty = tight_slack_penalty
        self.tight_slack_threshold = tight_slack_threshold
        self.high_overqual_penalty = high_overqual_penalty
        self.low_overqual_penalty = low_overqual_penalty

    @property
    def name(self) -> str:
        return "greedy_scorer"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "deadline_miss_penalty": self.deadline_miss_penalty,
            "tight_slack_penalty": self.tight_slack_penalty,
            "tight_slack_threshold": self.tight_slack_threshold,
            "high_overqual_penalty": self.high_overqual_penalty,
            "low_overqual_penalty": self.low_overqual_penalty,
        }

    def compute(
        self, driver: Driver, order: Order, current_time: datetime,
        stops: list | None = None,
    ) -> CostResult:
        dist = road_distance_km(driver.current_location, order.pickup_location)

        # Evaluate the full multi-stop route in expected mode (human estimates)
        if stops is None:
            stops = build_stops(order)
        evaluation = evaluate_route(
            driver, order, stops, current_time, TravelTimeMode.EXPECTED,
        )

        # Worst package slack across the actual route
        if evaluation.package_results:
            worst_slack = min(pr.slack_min for pr in evaluation.package_results)
        else:
            worst_slack = 0.0

        # Binary penalties (human doesn't compute proportional risk)
        deadline_penalty = 0.0
        if worst_slack < 0:
            deadline_penalty = self.deadline_miss_penalty
        elif worst_slack < self.tight_slack_threshold:
            deadline_penalty = self.tight_slack_penalty

        # Equipment waste (crude awareness)
        driver_level = _cold_level(driver.cold_storage)
        needed_level = _min_cold_needed(order)
        overqual = max(0, driver_level - needed_level)
        equipment_penalty = 0.0
        if overqual >= 2:
            equipment_penalty = self.high_overqual_penalty
        elif overqual == 1:
            equipment_penalty = self.low_overqual_penalty

        total = dist + deadline_penalty + equipment_penalty

        return CostResult(
            total=total,
            breakdown={
                "distance_to_pickup_km": round(dist, 2),
                "equipment_level": driver_level,
                "needed_level": needed_level,
                "slack_min": round(worst_slack, 2),
                "deadline_penalty": deadline_penalty,
                "equipment_penalty": equipment_penalty,
                "score": round(total, 2),
            },
        )
