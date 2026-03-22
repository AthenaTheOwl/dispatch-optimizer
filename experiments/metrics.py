"""Standardized metrics collection for experiment evaluation.

Deadline metrics come from evaluated execution results (package_deliveries
on each Assignment), not from proxy calculations. This ensures metrics
match what the route evaluator computes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from app.models import (
    Driver, Order, DispatchResult, Scenario,
    ColdStorage, TempRegime,
)


@dataclass
class ExperimentMetrics:
    """Complete metrics for a single solver run on a single scenario."""

    # Distance
    total_distance_km: float = 0.0
    avg_distance_per_order_km: float = 0.0

    # Time
    total_time_minutes: float = 0.0
    avg_time_per_order_min: float = 0.0
    avg_pickup_wait_min: float = 0.0
    max_pickup_wait_min: float = 0.0

    # Assignment quality
    orders_assigned: int = 0
    orders_unassigned: int = 0
    total_orders: int = 0
    assignment_rate: float = 0.0

    # Deadline compliance (order-level)
    deadline_compliance_rate: float = 0.0
    orders_meeting_deadline: int = 0
    orders_missing_deadline: int = 0
    orders_fully_on_time_rate: float = 0.0

    # Deadline compliance (package-level)
    package_deadline_compliance_rate: float = 0.0
    packages_on_time: int = 0
    packages_missing_deadline: int = 0
    total_packages_checked: int = 0

    # Validation
    validation_rejections: int = 0
    planned_feasible_but_rejected_count: int = 0

    # Driver utilization
    drivers_used: int = 0
    drivers_total: int = 0
    driver_utilization_pct: float = 0.0
    driver_distance_std_dev: float = 0.0

    # Equipment efficiency
    overqualified_assignments: int = 0
    overqualification_rate: float = 0.0

    # Route quality
    multi_stop_routes: int = 0
    total_packages_delivered: int = 0
    cost_per_package_km: float = 0.0

    # Pickup wait by urgency
    pickup_wait_by_urgency: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
            else:
                d[k] = v
        return d


def compute_experiment_metrics(
    result: DispatchResult,
    scenario: Scenario,
) -> ExperimentMetrics:
    """Compute standardized metrics from a DispatchResult.

    Deadline compliance is computed from package_deliveries (route evaluator
    results) when available, falling back to the dispatched_at proxy only
    for legacy DispatchResults that lack package-level data.
    """
    orders = scenario.orders
    drivers = scenario.drivers
    orders_by_id = {o.id: o for o in orders}
    m = ExperimentMetrics()

    # Basic totals
    m.total_distance_km = result.total_distance_km
    m.total_time_minutes = result.total_time_minutes
    m.orders_assigned = len(result.assignments)
    m.orders_unassigned = len(result.unassigned_orders)
    m.total_orders = len(orders)
    m.assignment_rate = m.orders_assigned / m.total_orders * 100 if m.total_orders else 0

    # Averages
    if result.assignments:
        m.avg_distance_per_order_km = m.total_distance_km / len(result.assignments)
        m.avg_time_per_order_min = m.total_time_minutes / len(result.assignments)

    # Pickup wait times
    pickup_times_by_urgency: dict[str, list[float]] = {}
    all_pickup_times: list[float] = []
    for a in result.assignments:
        all_pickup_times.append(a.estimated_pickup_time_min)
        order = orders_by_id.get(a.order_id)
        if order:
            urgency = order.urgency.value
            pickup_times_by_urgency.setdefault(urgency, []).append(
                a.estimated_pickup_time_min,
            )

    m.avg_pickup_wait_min = statistics.mean(all_pickup_times) if all_pickup_times else 0
    m.max_pickup_wait_min = max(all_pickup_times) if all_pickup_times else 0
    m.pickup_wait_by_urgency = {
        u: round(statistics.mean(times), 2)
        for u, times in pickup_times_by_urgency.items()
    }

    # Deadline compliance — use package_deliveries when available
    has_package_deliveries = any(
        len(a.package_deliveries) > 0 for a in result.assignments
    )

    if has_package_deliveries:
        # Real execution metrics from route evaluator
        orders_fully_on_time = 0
        orders_checked = 0
        total_pkgs_on_time = 0
        total_pkgs_late = 0
        total_pkgs_checked = 0

        for a in result.assignments:
            order = orders_by_id.get(a.order_id)
            if not order or not a.package_deliveries:
                continue

            orders_checked += 1
            all_on_time = True
            for pd in a.package_deliveries:
                total_pkgs_checked += 1
                if pd.on_time:
                    total_pkgs_on_time += 1
                else:
                    total_pkgs_late += 1
                    all_on_time = False

            if all_on_time:
                orders_fully_on_time += 1

        m.orders_meeting_deadline = orders_fully_on_time
        m.orders_missing_deadline = orders_checked - orders_fully_on_time
        m.deadline_compliance_rate = (
            orders_fully_on_time / orders_checked * 100
        ) if orders_checked else 0
        m.orders_fully_on_time_rate = m.deadline_compliance_rate

        m.packages_on_time = total_pkgs_on_time
        m.packages_missing_deadline = total_pkgs_late
        m.total_packages_checked = total_pkgs_checked
        m.package_deadline_compliance_rate = (
            total_pkgs_on_time / total_pkgs_checked * 100
        ) if total_pkgs_checked else 0

    else:
        # Fallback: proxy using dispatched_at (for legacy DispatchResults)
        meeting_deadline = 0
        total_checked = 0
        for a in result.assignments:
            order = orders_by_id.get(a.order_id)
            if order:
                total_checked += 1
                reference_time = a.dispatched_at if a.dispatched_at else order.created_at
                remaining_minutes = (order.tightest_deadline - reference_time).total_seconds() / 60
                if a.estimated_total_time_min <= remaining_minutes:
                    meeting_deadline += 1
        m.orders_meeting_deadline = meeting_deadline
        m.orders_missing_deadline = total_checked - meeting_deadline
        m.deadline_compliance_rate = (meeting_deadline / total_checked * 100) if total_checked else 0
        m.orders_fully_on_time_rate = m.deadline_compliance_rate

    # Validation rejections
    m.validation_rejections = result.validation_rejections
    m.planned_feasible_but_rejected_count = sum(
        1 for a in result.assignments if a.execution_feasible is False
    )

    # Driver utilization
    drivers_used = set(a.driver_id for a in result.assignments)
    m.drivers_used = len(drivers_used)
    m.drivers_total = len(drivers)
    m.driver_utilization_pct = len(drivers_used) / len(drivers) * 100 if drivers else 0

    driver_distances: dict[str, float] = {}
    for a in result.assignments:
        driver_distances[a.driver_id] = driver_distances.get(a.driver_id, 0) + a.total_distance_km
    if len(driver_distances) > 1:
        m.driver_distance_std_dev = statistics.stdev(driver_distances.values())

    # Equipment efficiency
    overqualified_count = 0
    for a in result.assignments:
        order = orders_by_id.get(a.order_id)
        driver = next((d for d in drivers if d.id == a.driver_id), None)
        if order and driver:
            needed_regimes = order.required_temp_regimes
            max_needed = max(
                (0 if r == TempRegime.AMBIENT else 1 if r == TempRegime.REFRIGERATED else 3)
                for r in needed_regimes
            ) if needed_regimes else 0
            driver_level = {
                ColdStorage.NONE: 0, ColdStorage.COOLER: 1,
                ColdStorage.ACTIVE_FRIDGE: 2, ColdStorage.CRYO: 3,
            }[driver.cold_storage]
            if driver_level > max_needed + 1:
                overqualified_count += 1
    m.overqualified_assignments = overqualified_count
    m.overqualification_rate = (
        overqualified_count / len(result.assignments) * 100
        if result.assignments else 0
    )

    # Route quality
    m.multi_stop_routes = sum(1 for a in result.assignments if a.route.num_stops > 2)
    m.total_packages_delivered = sum(
        orders_by_id[a.order_id].total_packages
        for a in result.assignments
        if a.order_id in orders_by_id
    )
    m.cost_per_package_km = (
        m.total_distance_km / m.total_packages_delivered
        if m.total_packages_delivered else 0
    )

    return m
