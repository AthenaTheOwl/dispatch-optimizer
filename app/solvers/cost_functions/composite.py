"""Composite weighted cost function — the primary cost model.

Multi-factor cost function with tunable weights. Uses the route evaluator
for deadline slack computation so costing reflects the actual multi-stop
route, not a single-package direct delivery proxy.

Parameter sweeping over these weights is how we find optimal configurations.
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


INFEASIBLE_COST = 1e6

# Default weights
DEFAULT_URGENCY_MULTIPLIERS = {"stat": 3.0, "urgent": 2.0, "routine": 1.0, "standard": 0.8}
DEFAULT_DEADLINE_RISK_WEIGHT = 10.0
DEFAULT_SHIFT_OVERTIME_PENALTY = 500.0
DEFAULT_OVERQUALIFICATION_WEIGHT = 5.0
DEFAULT_DELIVERY_DISTANCE_WEIGHT = 0.5


def _cold_storage_level(cs: ColdStorage) -> int:
    return {ColdStorage.NONE: 0, ColdStorage.COOLER: 1, ColdStorage.ACTIVE_FRIDGE: 2, ColdStorage.CRYO: 3}[cs]


def _min_cold_storage_needed(order: Order) -> int:
    levels = []
    for regime in order.required_temp_regimes:
        if regime == TempRegime.AMBIENT:
            levels.append(0)
        elif regime == TempRegime.REFRIGERATED:
            levels.append(1)
        elif regime == TempRegime.FROZEN:
            levels.append(3)
        elif regime == TempRegime.CRYOGENIC:
            levels.append(3)
    return max(levels) if levels else 0


class CompositeCostFunction(CostFunction):
    """Multi-factor cost function with tunable weights.

    Components:
    1. Distance x urgency multiplier (higher urgency = distance matters more)
    2. Deadline risk penalty (from route evaluator: worst package slack across multi-stop route)
    3. Shift overtime penalty (hard penalty if route exceeds shift)
    4. Over-qualification penalty (don't waste high-capability on standard orders)
    5. Multi-destination delivery distance (from route evaluator)

    All weights are configurable -- parameter sweeping finds optimal values.
    """

    def __init__(
        self,
        urgency_multipliers: dict[str, float] | None = None,
        deadline_risk_weight: float = DEFAULT_DEADLINE_RISK_WEIGHT,
        shift_overtime_penalty: float = DEFAULT_SHIFT_OVERTIME_PENALTY,
        overqualification_weight: float = DEFAULT_OVERQUALIFICATION_WEIGHT,
        delivery_distance_weight: float = DEFAULT_DELIVERY_DISTANCE_WEIGHT,
    ):
        self.urgency_multipliers = urgency_multipliers or dict(DEFAULT_URGENCY_MULTIPLIERS)
        self.deadline_risk_weight = deadline_risk_weight
        self.shift_overtime_penalty = shift_overtime_penalty
        self.overqualification_weight = overqualification_weight
        self.delivery_distance_weight = delivery_distance_weight

    @property
    def name(self) -> str:
        return "composite"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "urgency_multipliers": self.urgency_multipliers,
            "deadline_risk_weight": self.deadline_risk_weight,
            "shift_overtime_penalty": self.shift_overtime_penalty,
            "overqualification_weight": self.overqualification_weight,
            "delivery_distance_weight": self.delivery_distance_weight,
        }

    def compute(
        self,
        driver: Driver,
        order: Order,
        current_time: datetime,
        stops: list | None = None,
    ) -> CostResult:
        breakdown: dict[str, float] = {}

        # 1. Distance x urgency
        dist_to_pickup = road_distance_km(driver.current_location, order.pickup_location)
        urgency_mult = self.urgency_multipliers.get(order.urgency.value, 1.0)
        distance_cost = dist_to_pickup * urgency_mult
        breakdown["distance_to_pickup"] = dist_to_pickup
        breakdown["urgency_multiplier"] = urgency_mult
        breakdown["distance_cost"] = distance_cost

        # Evaluate the full multi-stop route (conservative mode for planning)
        if stops is None:
            stops = build_stops(order)
        evaluation = evaluate_route(
            driver, order, stops, current_time, TravelTimeMode.CONSERVATIVE,
        )

        # 2. Deadline risk — from route evaluator (worst package slack)
        if evaluation.package_results:
            worst_slack = min(pr.slack_min for pr in evaluation.package_results)
        else:
            worst_slack = 0.0
        deadline_penalty = max(0, -worst_slack) * self.deadline_risk_weight
        breakdown["deadline_slack_min"] = worst_slack
        breakdown["deadline_penalty"] = deadline_penalty

        # 3. Shift overtime — from route evaluator
        shift_penalty = self.shift_overtime_penalty if not evaluation.shift_feasible else 0.0
        breakdown["shift_penalty"] = shift_penalty

        # 4. Over-qualification
        driver_level = _cold_storage_level(driver.cold_storage)
        needed_level = _min_cold_storage_needed(order)
        overqual = max(0, driver_level - needed_level)
        overqual_penalty = overqual * self.overqualification_weight
        breakdown["overqualification_penalty"] = overqual_penalty
        breakdown["driver_cold_level"] = driver_level
        breakdown["needed_cold_level"] = needed_level

        # 5. Multi-destination delivery distance — from route evaluator
        # Total route distance minus the pickup leg
        pickup_leg = evaluation.stop_etas[0].travel_time_to_stop_min if evaluation.stop_etas else 0
        delivery_dist = evaluation.total_distance_km - road_distance_km(
            driver.current_location, order.pickup_location,
        )
        delivery_dist = max(0.0, delivery_dist)
        breakdown["delivery_route_dist"] = delivery_dist

        total = (
            distance_cost
            + deadline_penalty
            + shift_penalty
            + overqual_penalty
            + delivery_dist * self.delivery_distance_weight
        )
        breakdown["total"] = total

        return CostResult(total=total, breakdown=breakdown)
