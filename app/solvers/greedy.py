"""Greedy solver — sequential assignment using pluggable cost function.

Refactored to use the route evaluator as the single source of truth
for route timing and metrics. Feasibility is checked against the raw
stop order (which matches what greedy actually executes).
"""

from app.models import (
    Order, Route, Assignment, PackageDeliveryInfo, DispatchResult, Scenario, DriverStatus,
)
from app.simulation.route_evaluator import (
    evaluate_route, build_stops, TravelTimeMode,
)
from app.solvers.base import Solver, CostFunction, ConstraintChecker, RouteOptimizer


class GreedySolver(Solver):
    """Sequential greedy dispatch — models a competent human dispatcher.

    Processes orders one at a time in urgency order. For each order,
    scores all feasible drivers and picks the best. No global view.

    Route optimizer is NoOp by default (greedy uses raw stop order).
    Feasibility is checked against the same stop order the solver executes.
    """

    @property
    def name(self) -> str:
        return "greedy"

    def solve(self, scenario: Scenario) -> DispatchResult:
        urgency_priority = {"stat": 0, "urgent": 1, "routine": 2, "standard": 3}
        sorted_orders = sorted(
            scenario.orders,
            key=lambda o: (
                urgency_priority.get(o.urgency.value, 99),
                o.tightest_deadline,
            ),
        )

        available_drivers = {
            d.id: d for d in scenario.drivers if d.status != DriverStatus.OFFLINE
        }
        assigned_driver_ids: set[str] = set()
        assignments: list[Assignment] = []
        unassigned: list[str] = []

        for order in sorted_orders:
            best_driver = None
            best_cost_result = None
            best_score = float("inf")
            best_evaluation = None

            # Build stops and apply route optimizer
            raw_stops = build_stops(order)

            for driver_id, driver in available_drivers.items():
                if driver_id in assigned_driver_ids:
                    continue

                # Optimize stops for this driver (greedy default: NoOp)
                optimized_stops = self.route_optimizer.optimize(
                    list(raw_stops), driver.current_location,
                )

                # Check feasibility against the ACTUAL stop sequence
                feasible, _ = self.constraint_checker.is_feasible(
                    driver, order, scenario.current_time,
                    stops=optimized_stops,
                )
                if not feasible:
                    continue

                cost_result = self.cost_function.compute(
                    driver, order, scenario.current_time,
                    stops=optimized_stops,
                )

                if cost_result.total < best_score:
                    best_score = cost_result.total
                    best_driver = driver
                    best_cost_result = cost_result
                    # Evaluate the route we'll actually execute
                    best_evaluation = evaluate_route(
                        driver, order, optimized_stops,
                        scenario.current_time, TravelTimeMode.CONSERVATIVE,
                    )

            if best_driver is None or best_evaluation is None:
                unassigned.append(order.id)
                continue

            route = Route(
                driver_id=best_driver.id,
                stops=best_evaluation.stops,
                total_distance_km=best_evaluation.total_distance_km,
                total_time_minutes=best_evaluation.total_time_min,
            )

            pkg_deliveries = [
                PackageDeliveryInfo(
                    package_id=pr.package_id,
                    delivery_time=pr.delivery_time,
                    deadline=pr.deadline,
                    on_time=pr.on_time,
                    slack_min=pr.slack_min,
                )
                for pr in best_evaluation.package_results
            ]

            assignments.append(Assignment(
                driver_id=best_driver.id,
                order_id=order.id,
                route=route,
                estimated_pickup_time_min=best_evaluation.stop_etas[0].travel_time_to_stop_min if best_evaluation.stop_etas else 0.0,
                estimated_total_time_min=best_evaluation.total_time_min,
                total_distance_km=best_evaluation.total_distance_km,
                cost_score=best_score,
                cost_breakdown=best_cost_result.breakdown if best_cost_result else {},
                package_deliveries=pkg_deliveries,
            ))

            assigned_driver_ids.add(best_driver.id)

        total_dist = sum(a.total_distance_km for a in assignments)
        total_time = sum(a.estimated_total_time_min for a in assignments)

        return DispatchResult(
            algorithm_name=f"Greedy ({self.cost_function.name})",
            assignments=assignments,
            unassigned_orders=unassigned,
            total_distance_km=total_dist,
            total_time_minutes=total_time,
        )
