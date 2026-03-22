"""Side-by-side comparison of dispatch algorithms with detailed metrics."""

from datetime import datetime
import statistics
from app.models import (
    Driver, Order, DispatchResult, Scenario,
    ColdStorage, TempRegime, COLD_STORAGE_CAPABILITIES,
)
from app.algorithms.greedy import greedy_dispatch
from app.algorithms.hungarian import hungarian_dispatch
from app.algorithms.pooling import find_pools
from app.simulation.distance import travel_time_minutes


def compute_metrics(result: DispatchResult, orders: list[Order], drivers: list[Driver]) -> dict[str, float]:
    """Compute detailed comparison metrics for a dispatch result."""
    metrics: dict[str, float] = {}

    # Basic totals
    metrics["total_distance_km"] = result.total_distance_km
    metrics["total_time_minutes"] = result.total_time_minutes
    metrics["orders_assigned"] = len(result.assignments)
    metrics["orders_unassigned"] = len(result.unassigned_orders)
    metrics["total_orders"] = len(orders)

    # Average distance per assignment
    if result.assignments:
        metrics["avg_distance_per_order_km"] = result.total_distance_km / len(result.assignments)
        metrics["avg_time_per_order_min"] = result.total_time_minutes / len(result.assignments)
    else:
        metrics["avg_distance_per_order_km"] = 0
        metrics["avg_time_per_order_min"] = 0

    # Pickup wait times by urgency
    pickup_times_by_urgency: dict[str, list[float]] = {}
    for assignment in result.assignments:
        order = next((o for o in orders if o.id == assignment.order_id), None)
        if order:
            urgency = order.urgency.value
            pickup_times_by_urgency.setdefault(urgency, []).append(
                assignment.estimated_pickup_time_min
            )

    for urgency, times in pickup_times_by_urgency.items():
        metrics[f"avg_pickup_wait_{urgency}_min"] = statistics.mean(times) if times else 0

    all_pickup_times = [a.estimated_pickup_time_min for a in result.assignments]
    metrics["avg_pickup_wait_min"] = statistics.mean(all_pickup_times) if all_pickup_times else 0
    metrics["max_pickup_wait_min"] = max(all_pickup_times) if all_pickup_times else 0

    # Deadline compliance
    orders_by_id = {o.id: o for o in orders}
    meeting_deadline = 0
    total_checked = 0
    for assignment in result.assignments:
        order = orders_by_id.get(assignment.order_id)
        if order:
            total_checked += 1
            deadline_minutes = (order.tightest_deadline - order.created_at).total_seconds() / 60
            if assignment.estimated_total_time_min <= deadline_minutes:
                meeting_deadline += 1

    metrics["deadline_compliance_rate"] = (meeting_deadline / total_checked * 100) if total_checked else 0
    metrics["orders_meeting_deadline"] = meeting_deadline
    metrics["orders_missing_deadline"] = total_checked - meeting_deadline

    # Driver utilization
    drivers_used = set(a.driver_id for a in result.assignments)
    metrics["drivers_used"] = len(drivers_used)
    metrics["drivers_total"] = len(drivers)
    metrics["driver_utilization_pct"] = len(drivers_used) / len(drivers) * 100 if drivers else 0

    # Distance balance across drivers
    driver_distances: dict[str, float] = {}
    for a in result.assignments:
        driver_distances[a.driver_id] = driver_distances.get(a.driver_id, 0) + a.total_distance_km
    if len(driver_distances) > 1:
        metrics["driver_distance_std_dev"] = statistics.stdev(driver_distances.values())
    else:
        metrics["driver_distance_std_dev"] = 0

    # Equipment utilization — check for over-qualification
    overqualified_count = 0
    for assignment in result.assignments:
        order = orders_by_id.get(assignment.order_id)
        driver = next((d for d in drivers if d.id == assignment.driver_id), None)
        if order and driver:
            needed_regimes = order.required_temp_regimes
            max_needed = max(
                (0 if r == TempRegime.AMBIENT else 1 if r == TempRegime.REFRIGERATED else 3)
                for r in needed_regimes
            ) if needed_regimes else 0
            driver_level = {ColdStorage.NONE: 0, ColdStorage.COOLER: 1,
                           ColdStorage.ACTIVE_FRIDGE: 2, ColdStorage.CRYO: 3}[driver.cold_storage]
            if driver_level > max_needed + 1:  # Significantly overqualified
                overqualified_count += 1

    metrics["overqualified_assignments"] = overqualified_count

    # Multi-stop routes (pooling indicator)
    multi_stop = sum(1 for a in result.assignments if a.route.num_stops > 2)
    metrics["multi_stop_routes"] = multi_stop

    # Total packages delivered
    total_packages = sum(
        orders_by_id[a.order_id].total_packages
        for a in result.assignments
        if a.order_id in orders_by_id
    )
    metrics["total_packages_delivered"] = total_packages
    metrics["cost_per_package_km"] = (
        result.total_distance_km / total_packages if total_packages else 0
    )

    return metrics


def compare_algorithms(
    scenario: Scenario,
) -> dict[str, dict]:
    """
    Run both greedy and Hungarian algorithms on the same scenario.
    Returns comparison data with metrics and delta calculations.
    """
    greedy_result = greedy_dispatch(
        scenario.drivers, scenario.orders, scenario.current_time
    )
    hungarian_result = hungarian_dispatch(
        scenario.drivers, scenario.orders, scenario.current_time
    )

    greedy_metrics = compute_metrics(greedy_result, scenario.orders, scenario.drivers)
    hungarian_metrics = compute_metrics(hungarian_result, scenario.orders, scenario.drivers)

    # Calculate deltas (negative = improvement for hungarian)
    deltas: dict[str, float] = {}
    all_keys = set(greedy_metrics.keys()) | set(hungarian_metrics.keys())
    for key in all_keys:
        g_val = greedy_metrics.get(key, 0)
        h_val = hungarian_metrics.get(key, 0)
        if isinstance(g_val, (int, float)) and isinstance(h_val, (int, float)) and g_val != 0:
            deltas[key] = ((h_val - g_val) / abs(g_val)) * 100  # % change
        else:
            deltas[key] = 0

    # Pooling analysis
    pools = find_pools(scenario.orders, scenario.current_time)
    pooling_stats = {
        "total_pools": len(pools),
        "single_order_pools": sum(1 for p in pools if len(p) == 1),
        "multi_order_pools": sum(1 for p in pools if len(p) > 1),
        "avg_pool_size": statistics.mean(len(p) for p in pools) if pools else 0,
        "max_pool_size": max(len(p) for p in pools) if pools else 0,
        "orders_poolable": sum(len(p) for p in pools if len(p) > 1),
    }

    return {
        "greedy": {
            "result": greedy_result,
            "metrics": greedy_metrics,
        },
        "hungarian": {
            "result": hungarian_result,
            "metrics": hungarian_metrics,
        },
        "deltas": deltas,
        "pooling": pooling_stats,
    }
