"""Event-driven simulation engine.

Orders arrive at their created_at time. At each dispatch epoch, the solver
only sees orders that have arrived and drivers whose state reflects their
current position and load. No clairvoyance.

The simulation advances in discrete time steps. At each step:
1. Check which new orders have arrived since last step
2. Update driver states (position, availability) based on active routes
3. Feed only the pending (unassigned) orders + available drivers to the solver
4. Validate assignments with defense-in-depth check (expected-mode evaluation)
5. Record assignments made at this step

Both greedy and algorithmic solvers operate under the same information
constraints — the difference is decision quality, not information advantage.

Driver-state updates and route completion estimates come from the route
evaluator — no parallel route-time math.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.models import (
    Driver, Order, Assignment, PackageDeliveryInfo, DispatchResult, Scenario, Route,
    DriverStatus, Location,
)
from app.simulation.distance import PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES
from app.simulation.route_evaluator import (
    evaluate_route, TravelTimeMode, RouteEvaluation,
)
from app.solvers.base import Solver


@dataclass
class DriverState:
    """Mutable driver state that evolves through the simulation."""
    driver: Driver
    current_location: Location
    status: DriverStatus
    current_load: int
    available_at: datetime  # When driver finishes current task
    active_route: Route | None = None
    assigned_order_ids: list[str] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return self.status == DriverStatus.AVAILABLE

    def to_driver(self, at_time: datetime) -> Driver:
        """Snapshot the current state as a Driver model for the solver."""
        return self.driver.model_copy(update={
            "current_location": self.current_location,
            "status": self.status,
            "current_load": self.current_load,
        })


@dataclass
class SimulationEvent:
    """An event that occurred during the simulation."""
    time: datetime
    event_type: str  # "order_arrived", "dispatched", "pickup", "delivery", "driver_freed", "validation_rejected"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResult:
    """Complete result of an event-driven simulation run."""
    algorithm_name: str
    assignments: list[Assignment]
    unassigned_orders: list[str]
    events: list[SimulationEvent]
    total_distance_km: float = 0.0
    total_time_minutes: float = 0.0
    dispatch_epochs: int = 0
    validation_rejections: int = 0

    def to_dispatch_result(self) -> DispatchResult:
        """Convert to DispatchResult for compatibility with metrics."""
        return DispatchResult(
            algorithm_name=self.algorithm_name,
            assignments=self.assignments,
            unassigned_orders=self.unassigned_orders,
            total_distance_km=self.total_distance_km,
            total_time_minutes=self.total_time_minutes,
            validation_rejections=self.validation_rejections,
        )


def run_simulation(
    solver: Solver,
    scenario: Scenario,
    dispatch_interval_minutes: float = 5.0,
    max_pending_wait_minutes: float = 30.0,
) -> SimulationResult:
    """Run an event-driven simulation.

    Args:
        solver: The solver to use for dispatch decisions.
        scenario: The scenario (orders have created_at times spread across the day).
        dispatch_interval_minutes: How often to run the solver (dispatch epoch).
        max_pending_wait_minutes: After this long pending, force-dispatch even if
            solver can't find a good assignment (order gets marked unassigned).

    Returns:
        SimulationResult with assignments made at each epoch.
    """
    # Initialize driver states
    driver_states: dict[str, DriverState] = {}
    for d in scenario.drivers:
        driver_states[d.id] = DriverState(
            driver=d,
            current_location=d.current_location,
            status=d.status,
            current_load=d.current_load,
            available_at=scenario.current_time,
        )

    # Sort orders by arrival time
    sorted_orders = sorted(scenario.orders, key=lambda o: o.created_at)
    order_queue = list(sorted_orders)  # Orders not yet arrived
    orders_by_id = {o.id: o for o in scenario.orders}
    pending_orders: list[Order] = []   # Arrived but not yet dispatched
    pending_since: dict[str, datetime] = {}  # When each order entered pending

    all_assignments: list[Assignment] = []
    unassigned: list[str] = []
    events: list[SimulationEvent] = []
    validation_rejections = 0

    # Determine simulation time range
    earliest_arrival = sorted_orders[0].created_at if sorted_orders else scenario.current_time
    latest_arrival = sorted_orders[-1].created_at if sorted_orders else scenario.current_time
    # Run until all orders are dispatched or max time exceeded
    sim_end = latest_arrival + timedelta(minutes=max_pending_wait_minutes + 60)

    current_time = earliest_arrival
    epoch_count = 0

    while current_time <= sim_end:
        # 1. Arrive new orders
        newly_arrived: list[Order] = []
        while order_queue and order_queue[0].created_at <= current_time:
            order = order_queue.pop(0)
            newly_arrived.append(order)
            pending_orders.append(order)
            pending_since[order.id] = current_time
            events.append(SimulationEvent(
                time=current_time,
                event_type="order_arrived",
                details={"order_id": order.id, "urgency": order.urgency.value},
            ))

        # 2. Update driver states — free up drivers whose routes are complete
        for ds in driver_states.values():
            if ds.status != DriverStatus.AVAILABLE and ds.available_at <= current_time:
                ds.status = DriverStatus.AVAILABLE
                ds.current_load = 0
                ds.active_route = None
                events.append(SimulationEvent(
                    time=current_time,
                    event_type="driver_freed",
                    details={"driver_id": ds.driver.id},
                ))

        # 3. Check for orders that have been pending too long
        timed_out: list[str] = []
        for oid, since in list(pending_since.items()):
            if (current_time - since).total_seconds() / 60 > max_pending_wait_minutes:
                # Check if still in pending (not yet dispatched)
                if any(o.id == oid for o in pending_orders):
                    timed_out.append(oid)
                    pending_orders = [o for o in pending_orders if o.id != oid]
                    del pending_since[oid]
                    unassigned.append(oid)
                    events.append(SimulationEvent(
                        time=current_time,
                        event_type="order_timed_out",
                        details={"order_id": oid},
                    ))

        # 4. If there are pending orders, run the solver
        if pending_orders:
            # Build solver inputs: only available drivers + pending orders
            available_drivers = [
                ds.to_driver(current_time)
                for ds in driver_states.values()
                if ds.is_available
                and ds.driver.shift_start <= current_time <= ds.driver.shift_end
            ]

            if available_drivers:
                # Create a mini-scenario for this epoch
                epoch_scenario = Scenario(
                    drivers=available_drivers,
                    orders=list(pending_orders),
                    facilities=scenario.facilities,
                    current_time=current_time,
                )

                result = solver.solve(epoch_scenario)
                epoch_count += 1

                # Process assignments with defense-in-depth validation
                for assignment in result.assignments:
                    order = orders_by_id.get(assignment.order_id)
                    if order is None:
                        continue

                    # Defense-in-depth: validate the assigned route in expected mode
                    ds = driver_states.get(assignment.driver_id)
                    if ds is None:
                        continue

                    # Build a driver snapshot at current state for validation
                    validation_driver = ds.to_driver(current_time)
                    exec_eval = evaluate_route(
                        validation_driver, order, assignment.route.stops,
                        current_time, TravelTimeMode.EXPECTED,
                    )

                    if not exec_eval.shift_feasible or not exec_eval.all_deadlines_met:
                        # Reject: leave order pending for next epoch
                        validation_rejections += 1
                        assignment.execution_feasible = False
                        events.append(SimulationEvent(
                            time=current_time,
                            event_type="validation_rejected",
                            details={
                                "order_id": assignment.order_id,
                                "driver_id": assignment.driver_id,
                                "shift_feasible": exec_eval.shift_feasible,
                                "deadlines_met": exec_eval.all_deadlines_met,
                                "missed_packages": exec_eval.missed_package_ids,
                            },
                        ))
                        continue

                    assignment.execution_feasible = True
                    assignment.dispatched_at = current_time

                    # Populate package deliveries from execution evaluation
                    assignment.package_deliveries = [
                        PackageDeliveryInfo(
                            package_id=pr.package_id,
                            delivery_time=pr.delivery_time,
                            deadline=pr.deadline,
                            on_time=pr.on_time,
                            slack_min=pr.slack_min,
                        )
                        for pr in exec_eval.package_results
                    ]

                    all_assignments.append(assignment)

                    # Update driver state using evaluator results
                    ds.status = DriverStatus.EN_ROUTE
                    ds.current_load = next(
                        (o.total_packages for o in pending_orders if o.id == assignment.order_id),
                        0,
                    )
                    ds.active_route = assignment.route
                    ds.available_at = exec_eval.route_end_time
                    ds.current_location = exec_eval.route_end_location
                    ds.assigned_order_ids.append(assignment.order_id)

                    events.append(SimulationEvent(
                        time=current_time,
                        event_type="dispatched",
                        details={
                            "order_id": assignment.order_id,
                            "driver_id": assignment.driver_id,
                            "distance_km": round(assignment.total_distance_km, 2),
                            "est_time_min": round(assignment.estimated_total_time_min, 2),
                        },
                    ))

                # Remove dispatched orders from pending
                dispatched_ids = {a.order_id for a in all_assignments}
                pending_orders = [o for o in pending_orders if o.id not in dispatched_ids]
                for oid in list(pending_since.keys()):
                    if oid in dispatched_ids:
                        del pending_since[oid]

        # 5. If no pending orders and no more arriving, we're done
        if not pending_orders and not order_queue:
            break

        # Advance time
        current_time += timedelta(minutes=dispatch_interval_minutes)

    # Any remaining pending orders are unassigned
    for o in pending_orders:
        unassigned.append(o.id)

    total_dist = sum(a.total_distance_km for a in all_assignments)
    total_time = sum(a.estimated_total_time_min for a in all_assignments)

    return SimulationResult(
        algorithm_name=f"EventSim({solver.name})",
        assignments=all_assignments,
        unassigned_orders=unassigned,
        events=events,
        total_distance_km=total_dist,
        total_time_minutes=total_time,
        dispatch_epochs=epoch_count,
        validation_rejections=validation_rejections,
    )
