"""Concrete constraint checker implementations."""

from datetime import datetime

from app.models import Driver, Order, RouteStop
from app.constraints import check_all_constraints
from app.simulation.route_evaluator import TravelTimeMode
from app.solvers.base import ConstraintChecker


class DefaultConstraintChecker(ConstraintChecker):
    """Standard constraint checker using all 6 hard constraints.

    Delegates to the constraint module which uses the route evaluator
    as the single source of truth for route timing.
    """

    def is_feasible(
        self,
        driver: Driver,
        order: Order,
        current_time: datetime,
        stops: list[RouteStop] | None = None,
    ) -> tuple[bool, list[str]]:
        """Check feasibility, optionally against a specific stop sequence.

        Args:
            driver: Candidate driver.
            order: Order to assign.
            current_time: Dispatch time.
            stops: If provided, evaluates against this exact stop sequence.
                   If None, uses raw destination order (pessimistic default).
        """
        return check_all_constraints(
            driver, order, current_time,
            stops=stops, mode=TravelTimeMode.CONSERVATIVE,
        )
