"""
Smart greedy dispatch — represents a competent human dispatcher.

This isn't a straw man. A good dispatcher:
- Prioritizes by urgency AND deadline tightness
- Checks constraints (won't send the wrong equipment)
- Prefers drivers whose equipment matches the order (avoids obvious waste)
- Considers deadline feasibility (skips drivers who'd miss the deadline)
- Has some awareness of equipment scarcity
- But still processes orders one at a time, sequentially
- Doesn't globally optimize across all orders simultaneously
- Doesn't pool orders or optimize multi-stop delivery routes
"""

from datetime import datetime, timedelta
from app.models import (
    Driver, Order, Route, RouteStop, Assignment, DispatchResult,
    StopType, DriverStatus, ColdStorage, TempRegime,
    COLD_STORAGE_CAPABILITIES,
)
from app.constraints import check_all_constraints
from app.simulation.distance import (
    road_distance_km, travel_time_minutes, route_distance_km,
    pessimistic_travel_time,
    PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES,
)


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


def _greedy_score(driver: Driver, order: Order, current_time: datetime) -> float:
    """
    A competent dispatcher's mental scoring model.

    Considers distance (primary), equipment fit, and deadline pressure.
    Not as sophisticated as the full Hungarian cost matrix, but not naive either.
    """
    dist = road_distance_km(driver.current_location, order.pickup_location)

    # A good dispatcher eyeballs deadline feasibility
    pickup_time = travel_time_minutes(driver.current_location, order.pickup_location, driver.speed_kmh)
    tightest_pkg = min(order.packages, key=lambda p: p.deadline)
    delivery_time = travel_time_minutes(order.pickup_location, tightest_pkg.destination, driver.speed_kmh)
    total_est = pickup_time + PICKUP_BUFFER_MINUTES + delivery_time + DELIVERY_BUFFER_MINUTES
    deadline_min = (order.tightest_deadline - current_time).total_seconds() / 60
    slack = deadline_min - total_est

    # If this driver would likely miss the deadline, heavily penalize
    # (a real dispatcher would skip them, but might misjudge edge cases)
    deadline_penalty = 0.0
    if slack < 0:
        deadline_penalty = 50.0  # Strong penalty but not infinite — dispatcher might misjudge
    elif slack < 15:
        deadline_penalty = 5.0   # Dispatcher gets nervous about tight ones

    # Equipment awareness: a good dispatcher tries not to waste the high-spec vehicle
    # on a standard-tier order, but they're not perfect at this.
    # They'll prefer matching equipment level but won't always succeed,
    # especially under time pressure.
    driver_level = _cold_level(driver.cold_storage)
    needed_level = _min_cold_needed(order)
    overqual = max(0, driver_level - needed_level)

    # Dispatcher penalizes obvious waste (cryo on ambient) but is lenient
    # about small mismatches (cooler on ambient is fine)
    equipment_penalty = 0.0
    if overqual >= 2:
        equipment_penalty = 3.0  # "I shouldn't send the high-spec vehicle for this, but..."
    elif overqual == 1:
        equipment_penalty = 0.5  # Minor preference, often ignored

    # Distance is still the primary factor — a dispatcher looks at the map
    # and picks someone "close enough" with the right gear
    return dist + deadline_penalty + equipment_penalty


def greedy_dispatch(
    drivers: list[Driver],
    orders: list[Order],
    current_time: datetime,
) -> DispatchResult:
    """
    Smart greedy dispatch — represents a competent human dispatcher.

    Process:
    1. Sort orders by urgency, then by deadline tightness (not just creation time)
    2. For each order, score all eligible drivers considering distance,
       deadline feasibility, and equipment fit
    3. Pick the best-scoring driver
    4. One order per driver, no pooling, no delivery route optimization

    What it DOES well (like a good human):
    - Processes urgent orders first
    - Checks all hard constraints (equipment, certs, capacity, shift)
    - Prefers drivers whose equipment level matches (avoids obvious waste)
    - Has some deadline awareness (penalizes tight/infeasible timelines)

    What it DOESN'T do (limitations of sequential human decision-making):
    - Can't see that assigning Driver A here leaves no good option for Order Y later
    - Doesn't optimize delivery stop order (goes to destinations in arbitrary sequence)
    - Doesn't pool nearby orders onto one driver
    - Equipment preference is heuristic, not globally optimized
    - Deadline slack isn't weighed against other orders' needs
    """
    # Sort: urgency first, then by deadline tightness (earliest deadline first within tier)
    urgency_priority = {"stat": 0, "urgent": 1, "routine": 2, "standard": 3}
    sorted_orders = sorted(
        orders,
        key=lambda o: (
            urgency_priority.get(o.urgency.value, 99),
            o.tightest_deadline,  # Within same urgency, tightest deadline first
        ),
    )

    available_drivers = {d.id: d for d in drivers if d.status != DriverStatus.OFFLINE}
    assigned_driver_ids: set[str] = set()

    assignments: list[Assignment] = []
    unassigned: list[str] = []

    for order in sorted_orders:
        best_driver = None
        best_score = float("inf")
        best_breakdown: dict[str, float] = {}

        for driver_id, driver in available_drivers.items():
            if driver_id in assigned_driver_ids:
                continue

            feasible, violations = check_all_constraints(driver, order, current_time)
            if not feasible:
                continue

            score = _greedy_score(driver, order, current_time)

            if score < best_score:
                best_score = score
                best_driver = driver
                dist = road_distance_km(driver.current_location, order.pickup_location)
                best_breakdown = {
                    "distance_to_pickup_km": round(dist, 2),
                    "equipment_level": _cold_level(driver.cold_storage),
                    "needed_level": _min_cold_needed(order),
                    "score": round(score, 2),
                }

        if best_driver is None:
            unassigned.append(order.id)
            continue

        # Build route: driver -> pickup -> deliveries (in order they appear, no optimization)
        stops: list[RouteStop] = []

        stops.append(RouteStop(
            location=order.pickup_location,
            stop_type=StopType.PICKUP,
            order_id=order.id,
            package_ids=[p.id for p in order.packages],
        ))

        # Delivery stops in arbitrary order (dispatcher just goes to each destination)
        for dest in order.unique_destinations:
            pkg_ids = [p.id for p in order.packages if
                       p.destination.lat == dest.lat and p.destination.lng == dest.lng]
            stops.append(RouteStop(
                location=dest,
                stop_type=StopType.DELIVERY,
                order_id=order.id,
                package_ids=pkg_ids,
            ))

        # Calculate route metrics
        route_locs = [best_driver.current_location] + [s.location for s in stops]
        total_dist = route_distance_km(route_locs)
        pickup_travel = travel_time_minutes(
            best_driver.current_location, order.pickup_location, best_driver.speed_kmh
        )
        total_time = (
            pickup_travel
            + PICKUP_BUFFER_MINUTES
            + sum(
                travel_time_minutes(route_locs[i], route_locs[i + 1], best_driver.speed_kmh)
                for i in range(1, len(route_locs) - 1)
            )
            + DELIVERY_BUFFER_MINUTES * len(order.unique_destinations)
        )

        route = Route(
            driver_id=best_driver.id,
            stops=stops,
            total_distance_km=total_dist,
            total_time_minutes=total_time,
        )

        assignments.append(Assignment(
            driver_id=best_driver.id,
            order_id=order.id,
            route=route,
            estimated_pickup_time_min=pickup_travel,
            estimated_total_time_min=total_time,
            total_distance_km=total_dist,
            cost_score=best_score,
            cost_breakdown=best_breakdown,
        ))

        assigned_driver_ids.add(best_driver.id)

    total_dist = sum(a.total_distance_km for a in assignments)
    total_time = sum(a.estimated_total_time_min for a in assignments)

    return DispatchResult(
        algorithm_name="Smart Greedy (Competent Dispatcher)",
        assignments=assignments,
        unassigned_orders=unassigned,
        total_distance_km=total_dist,
        total_time_minutes=total_time,
    )
