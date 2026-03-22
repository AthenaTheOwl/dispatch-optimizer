"""Pure distance cost function — simplest baseline."""

from datetime import datetime
from typing import Any

from app.models import Driver, Order
from app.simulation.distance import road_distance_km
from app.solvers.base import CostFunction, CostResult


class DistanceCostFunction(CostFunction):
    """Assigns cost purely based on distance to pickup.

    No deadline awareness, no equipment preference, no urgency weighting.
    The absolute simplest cost function — useful as a baseline to measure
    how much value each cost component adds.
    """

    @property
    def name(self) -> str:
        return "distance_only"

    def compute(
        self, driver: Driver, order: Order, current_time: datetime,
        stops: list | None = None,
    ) -> CostResult:
        dist = road_distance_km(driver.current_location, order.pickup_location)
        return CostResult(
            total=dist,
            breakdown={"distance_to_pickup_km": dist},
        )
