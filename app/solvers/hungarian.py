"""Hungarian solver — optimal batch assignment using pluggable cost function.

Refactored to use the route evaluator as the single source of truth
for route timing and metrics. Feasibility is checked against the optimized
stop order (which matches what Hungarian actually executes).
"""

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.models import (
    Order, Route, Assignment, PackageDeliveryInfo, DispatchResult, Scenario, DriverStatus,
)
from app.simulation.route_evaluator import (
    evaluate_route, build_stops, RouteEvaluation, TravelTimeMode,
)
from app.solvers.base import Solver, CostFunction, ConstraintChecker, RouteOptimizer


INFEASIBLE_COST = 1e6


class HungarianSolver(Solver):
    """Optimal batch assignment using the Hungarian algorithm.

    Builds a cost matrix from the injected cost function, solves for
    globally optimal assignment, then uses route evaluations from the
    evaluator for all metrics.

    Feasibility is checked against the optimized stop sequence that
    the solver will actually execute.
    """

    @property
    def name(self) -> str:
        return "hungarian"

    def solve(self, scenario: Scenario) -> DispatchResult:
        active_drivers = [d for d in scenario.drivers if d.status != DriverStatus.OFFLINE]

        if not active_drivers or not scenario.orders:
            return DispatchResult(
                algorithm_name=f"Hungarian ({self.cost_function.name})",
                assignments=[],
                unassigned_orders=[o.id for o in scenario.orders],
            )

        n_drivers = len(active_drivers)
        n_orders = len(scenario.orders)
        size = max(n_drivers, n_orders)

        # Build cost matrix
        cost_matrix = np.full((size, size), INFEASIBLE_COST)
        breakdown_cache: dict[tuple[int, int], dict[str, float]] = {}
        # Cache evaluated routes for assigned pairs
        evaluation_cache: dict[tuple[int, int], RouteEvaluation] = {}

        for i, driver in enumerate(active_drivers):
            for j, order in enumerate(scenario.orders):
                # Build and optimize stops
                raw_stops = build_stops(order)
                optimized_stops = self.route_optimizer.optimize(
                    list(raw_stops), driver.current_location,
                )

                # Check feasibility against the ACTUAL optimized stop sequence
                feasible, _ = self.constraint_checker.is_feasible(
                    driver, order, scenario.current_time,
                    stops=optimized_stops,
                )
                if not feasible:
                    cost_matrix[i][j] = INFEASIBLE_COST
                    breakdown_cache[(i, j)] = {"infeasible": INFEASIBLE_COST}
                    continue

                cost_result = self.cost_function.compute(
                    driver, order, scenario.current_time,
                    stops=optimized_stops,
                )
                cost_matrix[i][j] = cost_result.total
                breakdown_cache[(i, j)] = cost_result.breakdown

                # Cache the route evaluation for use after assignment
                evaluation = evaluate_route(
                    driver, order, optimized_stops,
                    scenario.current_time, TravelTimeMode.CONSERVATIVE,
                )
                evaluation_cache[(i, j)] = evaluation

        # Solve
        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        assignments: list[Assignment] = []
        assigned_order_ids: set[str] = set()

        for i, j in zip(row_ind, col_ind):
            if i >= n_drivers or j >= n_orders:
                continue
            if cost_matrix[i][j] >= INFEASIBLE_COST:
                continue

            driver = active_drivers[i]
            order = scenario.orders[j]
            breakdown = breakdown_cache.get((i, j), {})
            evaluation = evaluation_cache.get((i, j))
            assigned_order_ids.add(order.id)

            if evaluation is None:
                # Should not happen for feasible assignments, but defend
                continue

            route = Route(
                driver_id=driver.id,
                stops=evaluation.stops,
                total_distance_km=evaluation.total_distance_km,
                total_time_minutes=evaluation.total_time_min,
            )

            pickup_time = evaluation.stop_etas[0].travel_time_to_stop_min if evaluation.stop_etas else 0.0

            pkg_deliveries = [
                PackageDeliveryInfo(
                    package_id=pr.package_id,
                    delivery_time=pr.delivery_time,
                    deadline=pr.deadline,
                    on_time=pr.on_time,
                    slack_min=pr.slack_min,
                )
                for pr in evaluation.package_results
            ]

            assignments.append(Assignment(
                driver_id=driver.id,
                order_id=order.id,
                route=route,
                estimated_pickup_time_min=pickup_time,
                estimated_total_time_min=evaluation.total_time_min,
                total_distance_km=evaluation.total_distance_km,
                cost_score=cost_matrix[i][j],
                cost_breakdown=breakdown,
                package_deliveries=pkg_deliveries,
            ))

        # Unassigned orders
        unassigned = [o.id for o in scenario.orders if o.id not in assigned_order_ids]

        total_dist = sum(a.total_distance_km for a in assignments)
        total_time = sum(a.estimated_total_time_min for a in assignments)

        return DispatchResult(
            algorithm_name=f"Hungarian ({self.cost_function.name})",
            assignments=assignments,
            unassigned_orders=unassigned,
            total_distance_km=total_dist,
            total_time_minutes=total_time,
        )
