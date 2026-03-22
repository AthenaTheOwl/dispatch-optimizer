"""Single source of truth for route evaluation.

Every component that needs to know "how long does this route take?" or
"does this route meet deadlines?" must call this module. No parallel
route-time math anywhere else in the codebase.

Supports three travel-time modes:
- conservative: pessimistic (80th percentile) — used for planning/feasibility
- expected: deterministic mean — used for execution validation
- sampled: stochastic draw — reserved for future Monte Carlo use
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from app.models import (
    Driver, Order, Location, Route, RouteStop, StopType,
    PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES,
)
from app.simulation.distance import (
    road_distance_km, travel_time_minutes, pessimistic_travel_time,
)


class TravelTimeMode(str, Enum):
    CONSERVATIVE = "conservative"  # 80th percentile — planning
    EXPECTED = "expected"          # deterministic mean — execution
    SAMPLED = "sampled"            # stochastic draw — future use


@dataclass
class StopETA:
    """Timing details for a single stop in the route."""
    stop_index: int
    location: Location
    stop_type: StopType
    order_id: str
    package_ids: list[str]
    arrival_time: datetime
    departure_time: datetime
    travel_time_to_stop_min: float
    buffer_min: float


@dataclass
class PackageDeliveryResult:
    """Delivery timing for a single package."""
    package_id: str
    order_id: str
    delivery_time: datetime
    deadline: datetime
    on_time: bool
    slack_min: float  # positive = early, negative = late


@dataclass
class RouteEvaluation:
    """Complete evaluation of a route — the single source of truth.

    Every component that needs route timing, feasibility, or deadline
    compliance should use this result, not compute its own.
    """
    # Route shape
    stops: list[RouteStop]
    stop_etas: list[StopETA]

    # Aggregate metrics
    total_distance_km: float
    total_time_min: float
    route_end_time: datetime
    route_end_location: Location

    # Package-level delivery results
    package_results: list[PackageDeliveryResult]

    # Feasibility flags
    shift_feasible: bool
    all_deadlines_met: bool
    missed_package_ids: list[str] = field(default_factory=list)

    # Mode used
    travel_time_mode: TravelTimeMode = TravelTimeMode.CONSERVATIVE

    @property
    def order_fully_on_time(self) -> bool:
        """True if every package in the route meets its deadline."""
        return len(self.missed_package_ids) == 0

    @property
    def packages_on_time(self) -> int:
        return sum(1 for p in self.package_results if p.on_time)

    @property
    def packages_late(self) -> int:
        return sum(1 for p in self.package_results if not p.on_time)


def _travel_time(
    loc1: Location,
    loc2: Location,
    speed_kmh: float,
    mode: TravelTimeMode,
) -> float:
    """Compute travel time in the given mode."""
    if mode == TravelTimeMode.CONSERVATIVE:
        return pessimistic_travel_time(loc1, loc2, speed_kmh)
    elif mode == TravelTimeMode.EXPECTED:
        return travel_time_minutes(loc1, loc2, speed_kmh)
    else:
        # sampled: fall back to expected for now
        return travel_time_minutes(loc1, loc2, speed_kmh)


def evaluate_route(
    driver: Driver,
    order: Order,
    stops: list[RouteStop],
    dispatch_time: datetime,
    mode: TravelTimeMode = TravelTimeMode.CONSERVATIVE,
) -> RouteEvaluation:
    """Evaluate a route: compute ETAs, distances, and deadline feasibility.

    This is THE function that all components must use. It accepts the exact
    stop sequence being evaluated — no hidden reordering.

    Args:
        driver: The driver executing the route.
        order: The order being served (provides package deadlines).
        stops: The exact stop sequence to evaluate (pickup + deliveries).
        dispatch_time: When the driver starts the route.
        mode: Travel time computation mode.

    Returns:
        RouteEvaluation with full timing, distance, and feasibility data.
    """
    if not stops:
        return RouteEvaluation(
            stops=[],
            stop_etas=[],
            total_distance_km=0.0,
            total_time_min=0.0,
            route_end_time=dispatch_time,
            route_end_location=driver.current_location,
            package_results=[],
            shift_feasible=True,
            all_deadlines_met=True,
            travel_time_mode=mode,
        )

    # Build package deadline lookup
    package_deadlines: dict[str, datetime] = {}
    package_order_ids: dict[str, str] = {}
    for pkg in order.packages:
        package_deadlines[pkg.id] = pkg.deadline
        package_order_ids[pkg.id] = order.id

    # Walk through the stop sequence
    current_loc = driver.current_location
    clock = dispatch_time
    total_distance = 0.0
    stop_etas: list[StopETA] = []
    package_delivery_times: dict[str, datetime] = {}

    for idx, stop in enumerate(stops):
        # Travel to this stop
        leg_distance = road_distance_km(current_loc, stop.location)
        leg_travel = _travel_time(current_loc, stop.location, driver.speed_kmh, mode)
        total_distance += leg_distance

        arrival = clock + timedelta(minutes=leg_travel)

        # Buffer at stop
        if stop.stop_type == StopType.PICKUP:
            buffer = PICKUP_BUFFER_MINUTES
        else:
            buffer = DELIVERY_BUFFER_MINUTES

        departure = arrival + timedelta(minutes=buffer)

        stop_etas.append(StopETA(
            stop_index=idx,
            location=stop.location,
            stop_type=stop.stop_type,
            order_id=stop.order_id,
            package_ids=list(stop.package_ids),
            arrival_time=arrival,
            departure_time=departure,
            travel_time_to_stop_min=leg_travel,
            buffer_min=buffer,
        ))

        # Record delivery times for packages at this stop
        if stop.stop_type == StopType.DELIVERY:
            for pid in stop.package_ids:
                package_delivery_times[pid] = arrival

        clock = departure
        current_loc = stop.location

    # Total route time
    total_time = (clock - dispatch_time).total_seconds() / 60

    # Package-level deadline evaluation
    package_results: list[PackageDeliveryResult] = []
    missed_ids: list[str] = []

    for pkg in order.packages:
        delivery_time = package_delivery_times.get(pkg.id)
        if delivery_time is None:
            # Package not in any delivery stop — treat as missed
            missed_ids.append(pkg.id)
            package_results.append(PackageDeliveryResult(
                package_id=pkg.id,
                order_id=order.id,
                delivery_time=clock,  # worst case: end of route
                deadline=pkg.deadline,
                on_time=False,
                slack_min=-999.0,
            ))
        else:
            slack = (pkg.deadline - delivery_time).total_seconds() / 60
            on_time = delivery_time <= pkg.deadline
            if not on_time:
                missed_ids.append(pkg.id)
            package_results.append(PackageDeliveryResult(
                package_id=pkg.id,
                order_id=order.id,
                delivery_time=delivery_time,
                deadline=pkg.deadline,
                on_time=on_time,
                slack_min=slack,
            ))

    # Shift feasibility
    shift_feasible = clock <= driver.shift_end

    return RouteEvaluation(
        stops=stops,
        stop_etas=stop_etas,
        total_distance_km=total_distance,
        total_time_min=total_time,
        route_end_time=clock,
        route_end_location=current_loc,
        package_results=package_results,
        shift_feasible=shift_feasible,
        all_deadlines_met=len(missed_ids) == 0,
        missed_package_ids=missed_ids,
        travel_time_mode=mode,
    )


def build_stops(order: Order) -> list[RouteStop]:
    """Build the canonical stop list for an order: 1 pickup + N deliveries.

    This is the shared stop-building logic — used by all solvers.
    Returns stops in raw destination order (no optimization applied).
    """
    stops = [
        RouteStop(
            location=order.pickup_location,
            stop_type=StopType.PICKUP,
            order_id=order.id,
            package_ids=[p.id for p in order.packages],
        )
    ]
    for dest in order.unique_destinations:
        pkg_ids = [
            p.id for p in order.packages
            if p.destination.lat == dest.lat and p.destination.lng == dest.lng
        ]
        stops.append(RouteStop(
            location=dest,
            stop_type=StopType.DELIVERY,
            order_id=order.id,
            package_ids=pkg_ids,
        ))
    return stops
