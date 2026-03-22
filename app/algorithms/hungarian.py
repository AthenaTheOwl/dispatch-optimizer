"""Optimal batch assignment using the Hungarian algorithm with constraint-aware cost matrix."""

from datetime import datetime, timedelta
import numpy as np
from scipy.optimize import linear_sum_assignment

from app.models import (
    Driver, Order, Route, RouteStop, Assignment, DispatchResult,
    StopType, DriverStatus, ColdStorage, TempRegime,
    URGENCY_DEADLINE_MINUTES,
)
from app.constraints import check_all_constraints
from app.simulation.distance import (
    road_distance_km, travel_time_minutes, pessimistic_travel_time,
    PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES,
)
from app.algorithms.route_optimizer import optimize_route


# Cost matrix weights
INFEASIBLE_COST = 1e6
URGENCY_MULTIPLIER = {"stat": 3.0, "urgent": 2.0, "routine": 1.0, "standard": 0.8}
DEADLINE_RISK_WEIGHT = 10.0       # Per minute of negative slack
SHIFT_OVERTIME_PENALTY = 500.0
OVERQUALIFICATION_WEIGHT = 5.0    # Penalty per "wasted" level of cold storage


def _cold_storage_level(cs: ColdStorage) -> int:
    """Numeric level of cold storage capability (higher = more capable)."""
    return {ColdStorage.NONE: 0, ColdStorage.COOLER: 1, ColdStorage.ACTIVE_FRIDGE: 2, ColdStorage.CRYO: 3}[cs]


def _min_cold_storage_needed(order: Order) -> int:
    """Minimum cold storage level needed for this order's packages."""
    levels = []
    for regime in order.required_temp_regimes:
        if regime == TempRegime.AMBIENT:
            levels.append(0)
        elif regime == TempRegime.REFRIGERATED:
            levels.append(1)
        elif regime == TempRegime.FROZEN:
            levels.append(3)  # Only cryo handles frozen
        elif regime == TempRegime.CRYOGENIC:
            levels.append(3)
    return max(levels) if levels else 0


def compute_cost(
    driver: Driver,
    order: Order,
    current_time: datetime,
) -> tuple[float, dict[str, float]]:
    """
    Compute the assignment cost for a driver-order pair.

    Returns (total_cost, breakdown_dict) where breakdown explains each component.
    Infeasible assignments return INFEASIBLE_COST.
    """
    breakdown: dict[str, float] = {}

    # Hard constraint check
    feasible, violations = check_all_constraints(driver, order, current_time)
    if not feasible:
        return INFEASIBLE_COST, {"infeasible": INFEASIBLE_COST, "violations": len(violations)}

    # 1. Distance cost (km to pickup)
    dist_to_pickup = road_distance_km(driver.current_location, order.pickup_location)
    urgency_mult = URGENCY_MULTIPLIER.get(order.urgency.value, 1.0)
    distance_cost = dist_to_pickup * urgency_mult
    breakdown["distance_to_pickup"] = dist_to_pickup
    breakdown["urgency_multiplier"] = urgency_mult
    breakdown["distance_cost"] = distance_cost

    # 2. Deadline risk penalty
    pickup_time = pessimistic_travel_time(
        driver.current_location, order.pickup_location, driver.speed_kmh
    )
    tightest_deadline_minutes = (order.tightest_deadline - current_time).total_seconds() / 60
    # Estimate: pickup travel + buffer + delivery travel for tightest package
    tightest_pkg = min(order.packages, key=lambda p: p.deadline)
    delivery_time = pessimistic_travel_time(
        order.pickup_location, tightest_pkg.destination, driver.speed_kmh
    )
    total_estimated = pickup_time + PICKUP_BUFFER_MINUTES + delivery_time + DELIVERY_BUFFER_MINUTES
    slack = tightest_deadline_minutes - total_estimated

    deadline_penalty = max(0, -slack) * DEADLINE_RISK_WEIGHT
    breakdown["deadline_slack_min"] = slack
    breakdown["deadline_penalty"] = deadline_penalty

    # 3. Shift overtime penalty
    shift_remaining = (driver.shift_end - current_time).total_seconds() / 60
    if total_estimated > shift_remaining:
        breakdown["shift_penalty"] = SHIFT_OVERTIME_PENALTY
    else:
        breakdown["shift_penalty"] = 0.0

    # 4. Over-qualification penalty (don't waste high-capability driver on standard-tier order)
    driver_level = _cold_storage_level(driver.cold_storage)
    needed_level = _min_cold_storage_needed(order)
    overqual = max(0, driver_level - needed_level)
    overqual_penalty = overqual * OVERQUALIFICATION_WEIGHT
    breakdown["overqualification_penalty"] = overqual_penalty
    breakdown["driver_cold_level"] = driver_level
    breakdown["needed_cold_level"] = needed_level

    # 5. Multi-destination delivery distance (estimate total delivery route)
    delivery_dist = 0.0
    if len(order.unique_destinations) > 1:
        prev = order.pickup_location
        for dest in order.unique_destinations:
            delivery_dist += road_distance_km(prev, dest)
            prev = dest
    elif order.unique_destinations:
        delivery_dist = road_distance_km(order.pickup_location, order.unique_destinations[0])
    breakdown["delivery_route_dist"] = delivery_dist

    total = distance_cost + deadline_penalty + breakdown["shift_penalty"] + overqual_penalty + delivery_dist * 0.5
    breakdown["total"] = total

    return total, breakdown


def hungarian_dispatch(
    drivers: list[Driver],
    orders: list[Order],
    current_time: datetime,
) -> DispatchResult:
    """
    Optimal batch assignment using the Hungarian algorithm.

    Builds a cost matrix considering distance, urgency, deadlines, shift feasibility,
    and equipment over-qualification. Then solves for the globally optimal assignment.
    """
    active_drivers = [d for d in drivers if d.status != DriverStatus.OFFLINE]

    if not active_drivers or not orders:
        return DispatchResult(
            algorithm_name="Hungarian (Optimal Assignment)",
            assignments=[],
            unassigned_orders=[o.id for o in orders],
        )

    n_drivers = len(active_drivers)
    n_orders = len(orders)

    # Build cost matrix (drivers x orders)
    # Pad to square matrix if needed (Hungarian requires square)
    size = max(n_drivers, n_orders)
    cost_matrix = np.full((size, size), INFEASIBLE_COST)
    breakdown_cache: dict[tuple[int, int], dict[str, float]] = {}

    for i, driver in enumerate(active_drivers):
        for j, order in enumerate(orders):
            cost, breakdown = compute_cost(driver, order, current_time)
            cost_matrix[i][j] = cost
            breakdown_cache[(i, j)] = breakdown

    # Solve assignment problem
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    assignments: list[Assignment] = []
    unassigned: list[str] = []
    assigned_order_ids: set[str] = set()

    for i, j in zip(row_ind, col_ind):
        if i >= n_drivers or j >= n_orders:
            continue
        if cost_matrix[i][j] >= INFEASIBLE_COST:
            continue

        driver = active_drivers[i]
        order = orders[j]
        breakdown = breakdown_cache.get((i, j), {})
        assigned_order_ids.add(order.id)

        # Build optimized route for this assignment
        stops: list[RouteStop] = []

        # Pickup
        stops.append(RouteStop(
            location=order.pickup_location,
            stop_type=StopType.PICKUP,
            order_id=order.id,
            package_ids=[p.id for p in order.packages],
        ))

        # Deliveries
        for dest in order.unique_destinations:
            pkg_ids = [p.id for p in order.packages
                       if p.destination.lat == dest.lat and p.destination.lng == dest.lng]
            stops.append(RouteStop(
                location=dest,
                stop_type=StopType.DELIVERY,
                order_id=order.id,
                package_ids=pkg_ids,
            ))

        # Optimize delivery order using 2-opt
        optimized_stops = optimize_route(stops, driver.current_location)

        # Calculate metrics
        route_locs = [driver.current_location] + [s.location for s in optimized_stops]
        total_dist = sum(
            road_distance_km(route_locs[k], route_locs[k + 1])
            for k in range(len(route_locs) - 1)
        )
        pickup_travel = travel_time_minutes(
            driver.current_location, order.pickup_location, driver.speed_kmh
        )
        total_time = pickup_travel + PICKUP_BUFFER_MINUTES
        for k in range(1, len(route_locs) - 1):
            total_time += travel_time_minutes(route_locs[k], route_locs[k + 1], driver.speed_kmh)
        total_time += DELIVERY_BUFFER_MINUTES * len(order.unique_destinations)

        route = Route(
            driver_id=driver.id,
            stops=optimized_stops,
            total_distance_km=total_dist,
            total_time_minutes=total_time,
        )

        assignments.append(Assignment(
            driver_id=driver.id,
            order_id=order.id,
            route=route,
            estimated_pickup_time_min=pickup_travel,
            estimated_total_time_min=total_time,
            total_distance_km=total_dist,
            cost_score=cost_matrix[i][j],
            cost_breakdown=breakdown,
        ))

    # Find unassigned orders
    for order in orders:
        if order.id not in assigned_order_ids:
            unassigned.append(order.id)

    total_dist = sum(a.total_distance_km for a in assignments)
    total_time = sum(a.estimated_total_time_min for a in assignments)

    return DispatchResult(
        algorithm_name="Hungarian (Optimal Assignment)",
        assignments=assignments,
        unassigned_orders=unassigned,
        total_distance_km=total_dist,
        total_time_minutes=total_time,
    )
