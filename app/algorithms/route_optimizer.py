"""Per-driver route optimization: nearest-neighbor construction + 2-opt improvement."""

from app.models import Location, RouteStop, StopType
from app.simulation.distance import road_distance_km


def nearest_neighbor_order(stops: list[RouteStop], start: Location) -> list[RouteStop]:
    """
    Order stops using nearest-neighbor heuristic.

    Constraints:
    - A package's delivery stop must come after its pickup stop
    - Preserves pickup-before-delivery precedence per order
    """
    if len(stops) <= 1:
        return list(stops)

    # Separate pickups and deliveries
    pickups = [s for s in stops if s.stop_type == StopType.PICKUP]
    deliveries = [s for s in stops if s.stop_type == StopType.DELIVERY]

    # Build the route greedily
    ordered: list[RouteStop] = []
    picked_up_orders: set[str] = set()
    remaining_pickups = list(pickups)
    remaining_deliveries = list(deliveries)
    current_loc = start

    while remaining_pickups or remaining_deliveries:
        # Candidates: all remaining pickups + deliveries whose order has been picked up
        candidates: list[RouteStop] = list(remaining_pickups)
        for d in remaining_deliveries:
            if d.order_id in picked_up_orders:
                candidates.append(d)

        if not candidates:
            break

        # Pick the nearest candidate
        best = min(candidates, key=lambda s: road_distance_km(current_loc, s.location))
        ordered.append(best)
        current_loc = best.location

        if best.stop_type == StopType.PICKUP:
            remaining_pickups.remove(best)
            picked_up_orders.add(best.order_id)
        else:
            remaining_deliveries.remove(best)

    return ordered


def _route_distance(locations: list[Location]) -> float:
    """Total distance along a sequence of locations."""
    return sum(
        road_distance_km(locations[i], locations[i + 1])
        for i in range(len(locations) - 1)
    )


def _respects_precedence(stops: list[RouteStop]) -> bool:
    """Check that every delivery comes after its corresponding pickup."""
    picked_up: set[str] = set()
    for s in stops:
        if s.stop_type == StopType.PICKUP:
            picked_up.add(s.order_id)
        elif s.stop_type == StopType.DELIVERY:
            if s.order_id not in picked_up:
                return False
    return True


def two_opt_improve(
    stops: list[RouteStop],
    start: Location,
    max_iterations: int = 100,
) -> list[RouteStop]:
    """
    2-opt local search: iteratively swap pairs of edges to shorten the route.
    Respects pickup-before-delivery precedence constraints.
    """
    if len(stops) <= 2:
        return stops

    best_stops = list(stops)
    best_locs = [start] + [s.location for s in best_stops]
    best_dist = _route_distance(best_locs)

    improved = True
    iteration = 0

    while improved and iteration < max_iterations:
        improved = False
        iteration += 1

        for i in range(len(best_stops) - 1):
            for j in range(i + 1, len(best_stops)):
                # Reverse the segment between i and j
                new_stops = (
                    best_stops[:i]
                    + best_stops[i:j + 1][::-1]
                    + best_stops[j + 1:]
                )

                # Check precedence constraint
                if not _respects_precedence(new_stops):
                    continue

                new_locs = [start] + [s.location for s in new_stops]
                new_dist = _route_distance(new_locs)

                if new_dist < best_dist - 0.001:  # Small epsilon to avoid float issues
                    best_stops = new_stops
                    best_locs = new_locs
                    best_dist = new_dist
                    improved = True
                    break  # Restart inner loops after improvement

            if improved:
                break

    return best_stops


def optimize_route(
    stops: list[RouteStop],
    start: Location,
) -> list[RouteStop]:
    """
    Full route optimization pipeline:
    1. Nearest-neighbor construction for initial ordering
    2. 2-opt improvement to eliminate crossing paths
    """
    if len(stops) <= 1:
        return stops

    # Step 1: Nearest-neighbor
    ordered = nearest_neighbor_order(stops, start)

    # Step 2: 2-opt improvement
    optimized = two_opt_improve(ordered, start)

    return optimized
