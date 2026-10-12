"""Constraint finder: which stage does every order queue for?

Splits each order's life into three stages, counted from the cargo's side:

    queue_for_assignment  created -> dispatched. No driver owns the order yet.
                          The packages sit at the pickup: all wait.
    travel_to_pickup      dispatched -> pickup handled. The packages still sit
                          while the assigned driver drives over (wait), then get
                          scanned and handed off (work).
    transit               pickup handled -> last package delivered. The packages
                          are on board, moving or being handed off: all work.
                          Each route carries one order, so nothing waits here.

The binding stage holds the largest share of all waiting minutes. An order that
times out in the queue counts as queue_for_assignment wait if some driver on
the roster, idle at its start position, could have served it. If none could,
it was turned away rather than queued, and it is reported apart from the stages.

The same scenario then runs again with order arrivals compressed toward the
start of the day by --front-speedup. Deadlines move with their orders, and
driver shifts stay where they were. Both tables print, with the binding stage
before and after.

    python -m experiments.constraint --drivers 10 --orders 80 --seed 7 --front-speedup 2.0 [--json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.constraints import check_all_constraints
from app.models import Assignment, DriverStatus, Order, Scenario, StopType
from app.simulation.engine import SimulationResult, run_simulation
from app.simulation.route_evaluator import TravelTimeMode, evaluate_route
from experiments.scenarios import generate_scenario

STAGES = ("queue_for_assignment", "travel_to_pickup", "transit")

# Mirrors the two configurations the scorecard compares.
POLICIES = {
    "greedy": ("greedy", "greedy_scorer", "none"),
    "hungarian": ("hungarian", "composite", "nn_2opt"),
}


@dataclass(frozen=True)
class OrderStages:
    """One served order's life, split into stage wait and work minutes."""

    order_id: str
    driver_id: str
    created_to_delivered_min: float
    queue_for_assignment_wait: float
    travel_to_pickup_wait: float
    travel_to_pickup_work: float
    transit_work: float

    def stage_minutes(self, stage: str) -> tuple[float, float]:
        """(wait, work) minutes for one stage."""
        if stage == "queue_for_assignment":
            return self.queue_for_assignment_wait, 0.0
        if stage == "travel_to_pickup":
            return self.travel_to_pickup_wait, self.travel_to_pickup_work
        if stage == "transit":
            return 0.0, self.transit_work
        raise ValueError(f"unknown stage: {stage}")


def _minutes(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 60


def _build_solver(policy: str) -> Any:
    import app.solvers  # noqa: F401 - importing registers built-in solvers
    from app.solvers.base import SolverRegistry
    from app.solvers.constraints import DefaultConstraintChecker

    solver_name, cost_function, route_optimizer = POLICIES[policy]
    return SolverRegistry.get_solver(
        solver_name, cost_function, DefaultConstraintChecker(), route_optimizer,
    )


def compress_arrivals(scenario: Scenario, speedup: float) -> Scenario:
    """Pull every order's arrival toward the scenario start by `speedup`.

    Each order keeps its own lead time: package deadlines and time windows
    shift by the same amount as created_at. Drivers are untouched.
    """
    if speedup <= 0:
        raise ValueError("speedup must be positive")
    start = scenario.current_time
    orders = []
    for order in scenario.orders:
        new_created = start + (order.created_at - start) / speedup
        shift = new_created - order.created_at
        packages = [
            p.model_copy(update={"deadline": p.deadline + shift})
            for p in order.packages
        ]
        update: dict[str, Any] = {"created_at": new_created, "packages": packages}
        if order.time_window_start is not None:
            update["time_window_start"] = order.time_window_start + shift
        if order.time_window_end is not None:
            update["time_window_end"] = order.time_window_end + shift
        orders.append(order.model_copy(update=update))
    return scenario.model_copy(update={"orders": orders})


def order_stages(scenario: Scenario, result: SimulationResult) -> list[OrderStages]:
    """Split each served order into stage wait and work minutes.

    The engine keeps dispatch and delivery times but not the pickup time, so
    each route is re-run through the route evaluator in execution mode from
    the driver's position at dispatch: the same call the engine makes.
    """
    orders_by_id = {o.id: o for o in scenario.orders}
    driver_location = {d.id: d.current_location for d in scenario.drivers}
    drivers_by_id = {d.id: d for d in scenario.drivers}

    rows: list[OrderStages] = []
    # Assignments are appended in dispatch order, which is the order each
    # driver ran its routes, so each route starts where the previous one ended.
    for assignment in result.assignments:
        order = orders_by_id[assignment.order_id]
        driver = drivers_by_id[assignment.driver_id].model_copy(
            update={"current_location": driver_location[assignment.driver_id]},
        )
        evaluation = evaluate_route(
            driver, order, assignment.route.stops,
            assignment.dispatched_at, TravelTimeMode.EXPECTED,
        )
        driver_location[assignment.driver_id] = evaluation.route_end_location
        _check_matches_engine(assignment, evaluation.package_results)

        pickup = next(e for e in evaluation.stop_etas if e.stop_type == StopType.PICKUP)
        delivered = max(pd.delivery_time for pd in assignment.package_deliveries)
        rows.append(OrderStages(
            order_id=order.id,
            driver_id=assignment.driver_id,
            created_to_delivered_min=_minutes(delivered, order.created_at),
            queue_for_assignment_wait=_minutes(assignment.dispatched_at, order.created_at),
            travel_to_pickup_wait=_minutes(pickup.arrival_time, assignment.dispatched_at),
            travel_to_pickup_work=_minutes(pickup.departure_time, pickup.arrival_time),
            transit_work=_minutes(delivered, pickup.departure_time),
        ))
    return rows


def _check_matches_engine(assignment: Assignment, package_results: list[Any]) -> None:
    """Fail loudly if the rebuilt route disagrees with what the engine recorded."""
    rebuilt = {r.package_id: r.delivery_time for r in package_results}
    recorded = {pd.package_id: pd.delivery_time for pd in assignment.package_deliveries}
    if rebuilt != recorded:
        raise RuntimeError(
            f"rebuilt delivery times for {assignment.order_id} do not match the engine"
        )


def any_driver_could_take(scenario: Scenario, order: Order) -> bool:
    """True if some rostered driver, idle at its start position when the order
    arrives, passes every hard constraint for it (cargo, certs, capacity,
    shift, deadlines). False means no amount of queueing would have helped."""
    for driver in scenario.drivers:
        if not driver.shift_start <= order.created_at <= driver.shift_end:
            continue
        idle = driver.model_copy(update={"status": DriverStatus.AVAILABLE, "current_load": 0})
        feasible, _ = check_all_constraints(idle, order, order.created_at)
        if feasible:
            return True
    return False


def timed_out_orders(
    scenario: Scenario, result: SimulationResult,
) -> tuple[dict[str, float], list[str]]:
    """Split orders that timed out before any driver took them.

    Returns queue minutes for the ones some idle driver could have served
    (they waited for a driver and gave up) and the ids of the ones no driver
    on the roster could serve (turned away, not queued).
    """
    orders_by_id = {o.id: o for o in scenario.orders}
    waited: dict[str, float] = {}
    no_capable_driver: list[str] = []
    for event in result.events:
        if event.event_type != "order_timed_out":
            continue
        order = orders_by_id[event.details["order_id"]]
        if any_driver_could_take(scenario, order):
            waited[order.id] = _minutes(event.time, order.created_at)
        else:
            no_capable_driver.append(order.id)
    return waited, no_capable_driver


def stage_table(
    rows: list[OrderStages], gave_up: dict[str, float],
) -> list[dict[str, Any]]:
    """Mean wait and work minutes per stage, plus each stage's share of waiting.

    Orders that gave up waiting count toward queue_for_assignment only.
    """
    table = []
    for stage in STAGES:
        waits = [r.stage_minutes(stage)[0] for r in rows]
        works = [r.stage_minutes(stage)[1] for r in rows]
        if stage == "queue_for_assignment":
            waits += [gave_up[oid] for oid in sorted(gave_up)]
            works += [0.0] * len(gave_up)
        table.append({
            "stage": stage,
            "orders": len(waits),
            "mean_wait_min": round(statistics.mean(waits), 2) if waits else 0.0,
            "mean_work_min": round(statistics.mean(works), 2) if works else 0.0,
            "total_wait_min": round(sum(waits), 2),
        })
    total_wait = sum(row["total_wait_min"] for row in table)
    for row in table:
        share = row["total_wait_min"] / total_wait if total_wait else 0.0
        row["wait_share"] = round(share, 3)
    return table


def binding_stage(table: list[dict[str, Any]]) -> str:
    """The stage holding the most waiting minutes; ties go to the earlier stage."""
    best = max(table, key=lambda row: (row["total_wait_min"], -STAGES.index(row["stage"])))
    return best["stage"] if best["total_wait_min"] > 0 else "none"


def analyse(scenario: Scenario, policy: str) -> dict[str, Any]:
    """Run one policy on one scenario and report where its orders spend time."""
    result = run_simulation(_build_solver(policy), scenario)
    rows = order_stages(scenario, result)
    waited, no_capable_driver = timed_out_orders(scenario, result)
    table = stage_table(rows, waited)
    return {
        "orders_total": len(scenario.orders),
        "orders_served": len(rows),
        "orders_gave_up_waiting": len(waited),
        "orders_no_capable_driver": len(no_capable_driver),
        "orders_unassigned": len(result.unassigned_orders),
        "stages": table,
        "binding_stage": binding_stage(table),
        "orders": [
            {key: (round(v, 3) if isinstance(v, float) else v) for key, v in asdict(r).items()}
            for r in rows
        ],
    }


def run(
    drivers: int, orders: int, seed: int, front_speedup: float, policy: str = "greedy",
) -> dict[str, Any]:
    """Baseline run and front-compressed run on the same seed."""
    baseline, _ = generate_scenario(num_drivers=drivers, num_orders=orders, seed=seed)
    compressed = compress_arrivals(baseline, front_speedup)
    before = analyse(baseline, policy)
    after = analyse(compressed, policy)
    return {
        "policy": policy,
        "drivers": drivers,
        "orders": orders,
        "seed": seed,
        "front_speedup": front_speedup,
        "runs": [
            {"label": "baseline", "speedup": 1.0, **before},
            {"label": "front_compressed", "speedup": front_speedup, **after},
        ],
        "bottleneck_moved": before["binding_stage"] != after["binding_stage"],
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"constraint finder  policy={report['policy']}  drivers={report['drivers']}"
        f"  orders={report['orders']}  seed={report['seed']}",
    ]
    for run_ in report["runs"]:
        lines += [
            "",
            f"{run_['label']} (arrivals {run_['speedup']:g}x faster)"
            if run_["speedup"] != 1.0 else f"{run_['label']} (arrivals as generated)",
            f"{'stage':<22}{'orders':>8}{'wait_min':>10}{'work_min':>10}{'wait_share':>12}",
        ]
        for row in run_["stages"]:
            lines.append(
                f"{row['stage']:<22}{row['orders']:>8}{row['mean_wait_min']:>10.2f}"
                f"{row['mean_work_min']:>10.2f}{row['wait_share']:>12.3f}"
            )
        lines.append(
            f"served {run_['orders_served']}/{run_['orders_total']}, "
            f"gave up waiting {run_['orders_gave_up_waiting']}, "
            f"no capable driver {run_['orders_no_capable_driver']}"
        )
        lines.append(f"binding stage: {run_['binding_stage']}")
    before, after = (r["binding_stage"] for r in report["runs"])
    lines += [
        "",
        f"bottleneck moved: {before} -> {after}" if report["bottleneck_moved"]
        else f"bottleneck held: {before}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.constraint",
        description="Name the stage every order queues for, before and after arrivals speed up.",
    )
    parser.add_argument("--drivers", type=int, default=10)
    parser.add_argument("--orders", type=int, default=80)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--front-speedup", type=float, default=2.0)
    parser.add_argument("--policy", choices=sorted(POLICIES), default="greedy")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    if args.front_speedup <= 0:
        parser.error("--front-speedup must be positive")

    report = run(args.drivers, args.orders, args.seed, args.front_speedup, args.policy)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
