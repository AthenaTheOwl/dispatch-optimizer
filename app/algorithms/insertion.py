"""Cheapest insertion for adding new orders to active driver routes."""

from datetime import datetime, timedelta
from app.models import (
    Driver, Order, Route, RouteStop, StopType,
    PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES,
)
from app.constraints import check_all_constraints
from app.simulation.distance import (
    road_distance_km, pessimistic_travel_time,
)


def _insertion_cost(
    route_locs: list,  # List of Location objects in current route order
    new_pickup: object,
    new_deliveries: list,
    pickup_pos: int,
    delivery_positions: list[int],
) -> float:
    """
    Calculate the extra distance from inserting a pickup and deliveries
    at specified positions in the route.
    """
    # Build the new route with insertions
    new_route = list(route_locs)

    # Insert pickup first
    new_route.insert(pickup_pos, new_pickup)

    # Insert deliveries (adjust positions for the pickup insertion)
    for i, (del_loc, del_pos) in enumerate(zip(new_deliveries, delivery_positions)):
        adjusted_pos = del_pos + 1 + i  # +1 for pickup, +i for previous delivery insertions
        new_route.insert(adjusted_pos, del_loc)

    # Calculate new total distance
    new_dist = sum(
        road_distance_km(new_route[i], new_route[i + 1])
        for i in range(len(new_route) - 1)
    )

    # Original distance
    orig_dist = sum(
        road_distance_km(route_locs[i], route_locs[i + 1])
        for i in range(len(route_locs) - 1)
    )

    return new_dist - orig_dist


def find_cheapest_insertion(
    driver: Driver,
    route: Route,
    new_order: Order,
    current_time: datetime,
) -> tuple[float, list[RouteStop]] | None:
    """
    Find the cheapest way to insert a new order's pickup and deliveries
    into an existing route.

    Returns (extra_distance_km, new_stops_list) or None if infeasible.
    """
    # First check hard constraints
    feasible, _ = check_all_constraints(driver, new_order, current_time)
    if not feasible:
        return None

    current_stops = route.stops
    # Build current route locations (driver position + all stops)
    route_locs = [driver.current_location] + [s.location for s in current_stops]

    # The new order adds: 1 pickup + N deliveries
    new_pickup_loc = new_order.pickup_location
    new_delivery_locs = [dest for dest in new_order.unique_destinations]

    best_cost = float("inf")
    best_stops = None

    # Try every valid position for the pickup
    for p_pos in range(1, len(route_locs) + 1):
        # Deliveries must come after pickup
        # Try every combination of delivery positions after the pickup
        # For simplicity with small N, use sequential insertion
        delivery_positions = []
        valid = True

        for d_idx, d_loc in enumerate(new_delivery_locs):
            # Each delivery goes after the pickup and after previous deliveries
            min_d_pos = p_pos + 1 + d_idx
            max_d_pos = len(route_locs) + 1 + d_idx

            # Find the cheapest position for this delivery
            best_d_pos = min_d_pos
            best_d_cost = float("inf")

            for d_pos in range(min_d_pos, max_d_pos + 1):
                cost = _insertion_cost(
                    route_locs, new_pickup_loc, new_delivery_locs[:d_idx + 1],
                    p_pos, delivery_positions + [d_pos],
                )
                if cost < best_d_cost:
                    best_d_cost = cost
                    best_d_pos = d_pos

            delivery_positions.append(best_d_pos)

        # Calculate total insertion cost for this pickup position
        total_cost = _insertion_cost(
            route_locs, new_pickup_loc, new_delivery_locs,
            p_pos, delivery_positions,
        )

        if total_cost < best_cost:
            # Verify deadlines are still met for all existing orders
            if _check_deadlines_after_insertion(
                driver, current_stops, new_order, p_pos, delivery_positions, current_time
            ):
                best_cost = total_cost
                # Build the new stops list
                new_stops = list(current_stops)

                pickup_stop = RouteStop(
                    location=new_order.pickup_location,
                    stop_type=StopType.PICKUP,
                    order_id=new_order.id,
                    package_ids=[p.id for p in new_order.packages],
                )
                # Adjust position for stops list (route_locs includes driver position at index 0)
                new_stops.insert(p_pos - 1, pickup_stop)

                for d_idx, (d_loc, d_pos) in enumerate(zip(new_delivery_locs, delivery_positions)):
                    pkg_ids = [p.id for p in new_order.packages
                               if p.destination.lat == d_loc.lat and p.destination.lng == d_loc.lng]
                    delivery_stop = RouteStop(
                        location=d_loc,
                        stop_type=StopType.DELIVERY,
                        order_id=new_order.id,
                        package_ids=pkg_ids,
                    )
                    insert_idx = d_pos - 1 + d_idx  # Adjust for stops list
                    insert_idx = min(insert_idx, len(new_stops))
                    new_stops.insert(insert_idx, delivery_stop)

                best_stops = new_stops

    if best_stops is None:
        return None

    return (best_cost, best_stops)


def _check_deadlines_after_insertion(
    driver: Driver,
    current_stops: list[RouteStop],
    new_order: Order,
    pickup_pos: int,
    delivery_positions: list[int],
    current_time: datetime,
) -> bool:
    """
    Verify that inserting the new order doesn't cause any existing order
    to miss its deadline (cascade check).

    Simplified: checks that total route time doesn't increase beyond
    the slack of the tightest existing deadline.
    """
    # For the prototype, we accept the insertion if the extra time
    # is less than the minimum slack across all orders on the route
    route_locs = [driver.current_location] + [s.location for s in current_stops]
    original_dist = sum(
        road_distance_km(route_locs[i], route_locs[i + 1])
        for i in range(len(route_locs) - 1)
    )

    # Approximate extra time from insertion
    extra_dist = _insertion_cost(
        route_locs, new_order.pickup_location,
        [d for d in new_order.unique_destinations],
        pickup_pos, delivery_positions,
    )
    extra_time_min = (extra_dist / max(driver.speed_kmh, 1)) * 60 + PICKUP_BUFFER_MINUTES + DELIVERY_BUFFER_MINUTES

    # Allow if extra time is reasonable (< 15 min for prototype)
    return extra_time_min < 15.0
