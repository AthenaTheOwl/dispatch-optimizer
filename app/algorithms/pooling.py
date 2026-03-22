"""Order pooling: batch compatible orders by proximity, temp regime, and slack."""

from datetime import datetime, timedelta
from app.models import Order, Package, TempRegime, URGENCY_SLACK_MINUTES
from app.simulation.distance import road_distance_km
from app.constraints import are_temps_compatible


# Default pooling parameters
MAX_POOL_DISTANCE_KM = 2.0   # Max distance between pickup locations for pooling
MAX_POOL_SIZE = 4             # Max orders in a single pool


def can_pool(order_a: Order, order_b: Order, max_distance_km: float = MAX_POOL_DISTANCE_KM) -> bool:
    """Check if two orders can be pooled together."""
    # Proximity check
    dist = road_distance_km(order_a.pickup_location, order_b.pickup_location)
    if dist > max_distance_km:
        return False

    # Temperature compatibility
    all_packages = order_a.packages + order_b.packages
    if not are_temps_compatible(all_packages):
        return False

    return True


def compute_pool_savings(orders: list[Order]) -> float:
    """
    Estimate distance savings from pooling orders vs dispatching individually.

    Returns estimated km saved (positive = savings).
    """
    if len(orders) <= 1:
        return 0.0

    # Individual dispatch: each order gets its own driver from a "central" point
    # Pooled: one driver visits all pickups then all deliveries
    # Simplification: savings ~ sum of inter-pickup distances avoided

    individual_delivery_dist = 0.0
    for order in orders:
        for dest in order.unique_destinations:
            individual_delivery_dist += road_distance_km(order.pickup_location, dest)

    # Pooled: driver visits pickups in sequence, then deliveries
    # Rough estimate of pooled distance
    all_pickups = [o.pickup_location for o in orders]
    pooled_pickup_dist = sum(
        road_distance_km(all_pickups[i], all_pickups[i + 1])
        for i in range(len(all_pickups) - 1)
    )

    # Savings come from shared trips to similar destination areas
    all_dests = []
    for o in orders:
        all_dests.extend(o.unique_destinations)

    # Deduplicate destinations (same facility)
    unique_dests = []
    seen: set[str] = set()
    for d in all_dests:
        key = f"{d.lat:.4f},{d.lng:.4f}"
        if key not in seen:
            seen.add(key)
            unique_dests.append(d)

    # If orders share destinations, savings are significant
    dest_reduction = len(all_dests) - len(unique_dests)
    avg_delivery_dist = individual_delivery_dist / max(len(all_dests), 1)
    savings = dest_reduction * avg_delivery_dist

    return max(0, savings)


def find_pools(
    orders: list[Order],
    current_time: datetime,
    max_distance_km: float = MAX_POOL_DISTANCE_KM,
    max_pool_size: int = MAX_POOL_SIZE,
) -> list[list[Order]]:
    """
    Group compatible orders into pools.

    Uses a greedy clustering approach:
    1. Start with unassigned orders sorted by urgency
    2. For each unpooled order, find compatible neighbors
    3. Build clusters respecting max pool size and constraints
    """
    # Only pool orders that have slack (STAT orders dispatch immediately)
    poolable = [o for o in orders if o.slack_minutes > 0]
    immediate = [o for o in orders if o.slack_minutes == 0]

    # Each immediate order is its own "pool"
    pools: list[list[Order]] = [[o] for o in immediate]

    # Cluster poolable orders
    remaining = list(poolable)
    assigned: set[str] = set()

    # Sort by pickup location to help spatial clustering
    remaining.sort(key=lambda o: (o.pickup_location.lat, o.pickup_location.lng))

    for order in remaining:
        if order.id in assigned:
            continue

        # Start a new pool with this order
        pool = [order]
        assigned.add(order.id)

        # Find compatible neighbors
        for candidate in remaining:
            if candidate.id in assigned:
                continue
            if len(pool) >= max_pool_size:
                break

            # Check compatibility with ALL orders already in the pool
            compatible = all(can_pool(existing, candidate, max_distance_km) for existing in pool)
            if compatible:
                pool.append(candidate)
                assigned.add(candidate.id)

        pools.append(pool)

    return pools


def should_hold_for_pooling(
    order: Order,
    existing_orders: list[Order],
    current_time: datetime,
) -> bool:
    """
    Decide whether to hold an order for potential future pooling.

    Returns True if the order has slack AND there are likely pooling opportunities.
    """
    if order.slack_minutes == 0:
        return False

    # Check how many existing orders are nearby and compatible
    nearby_compatible = 0
    for existing in existing_orders:
        if can_pool(order, existing):
            nearby_compatible += 1

    # Hold if there's at least one nearby compatible order
    # In production this would use historical arrival patterns to predict future orders
    return nearby_compatible > 0
