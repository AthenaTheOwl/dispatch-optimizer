"""Detailed analysis of greedy vs Hungarian batch dispatch — explains WHY greedy is worse.

Uses the solver framework and event-driven simulation. Consumes the same
evaluated execution/metric data that powers compare.
"""

from datetime import datetime
from app.models import (
    Driver, Order, Scenario, ColdStorage, TempRegime,
    COLD_STORAGE_CAPABILITIES,
)
from app.simulation.distance import road_distance_km
from app.simulation.engine import run_simulation, SimulationResult
from app.solvers.base import SolverRegistry
from app.solvers.constraints import DefaultConstraintChecker
from experiments.metrics import compute_experiment_metrics


_constraint_checker = DefaultConstraintChecker()


def _cold_level(cs: ColdStorage) -> int:
    return {ColdStorage.NONE: 0, ColdStorage.COOLER: 1, ColdStorage.ACTIVE_FRIDGE: 2, ColdStorage.CRYO: 3}[cs]


def _max_temp_needed(order: Order) -> int:
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


def _run_both(scenario: Scenario) -> tuple[SimulationResult, SimulationResult]:
    """Run both solvers through event-driven simulation.

    This is shared between analysis and compare to ensure they see
    the same data.
    """
    greedy_solver = SolverRegistry.get_solver(
        "greedy", "greedy_scorer", _constraint_checker, "none",
    )
    hungarian_solver = SolverRegistry.get_solver(
        "hungarian", "composite", _constraint_checker, "nn_2opt",
    )

    g_sim = run_simulation(greedy_solver, scenario)
    h_sim = run_simulation(hungarian_solver, scenario)
    return g_sim, h_sim


def generate_analysis(scenario: Scenario) -> dict:
    """Generate detailed analysis logs comparing greedy vs Hungarian batch assignment.

    Runs both algorithms through the event-driven simulation engine
    so they operate under the same information constraints.
    """
    g_sim, h_sim = _run_both(scenario)

    greedy_result = g_sim.to_dispatch_result()
    hungarian_result = h_sim.to_dispatch_result()

    greedy_metrics = compute_experiment_metrics(greedy_result, scenario)
    hungarian_metrics = compute_experiment_metrics(hungarian_result, scenario)

    orders_by_id = {o.id: o for o in scenario.orders}
    drivers_by_id = {d.id: d for d in scenario.drivers}

    greedy_map = {a.order_id: a for a in greedy_result.assignments}
    hungarian_map = {a.order_id: a for a in hungarian_result.assignments}

    # Per-assignment analysis
    assignment_logs = []
    for order in scenario.orders:
        g = greedy_map.get(order.id)
        h = hungarian_map.get(order.id)

        entry = {
            "order_id": order.id,
            "urgency": order.urgency.value,
            "pickup": order.pickup_location.name,
            "packages": [
                {
                    "cargo_type": p.cargo_type.value,
                    "temp": p.temp_regime.value,
                    "destination": p.destination.name,
                }
                for p in order.packages
            ],
            "created_at": order.created_at.isoformat(),
            "greedy": None,
            "optimal": None,
            "problems": [],
        }

        if g:
            g_ref = g.dispatched_at if g.dispatched_at else order.created_at
            g_deadline_budget = (order.tightest_deadline - g_ref).total_seconds() / 60
            gd = drivers_by_id.get(g.driver_id)
            if gd:
                g_dist_pickup = road_distance_km(gd.current_location, order.pickup_location)
                g_entry = {
                    "driver_id": gd.id,
                    "driver_name": gd.name,
                    "vehicle": gd.vehicle_type.value,
                    "cold_storage": gd.cold_storage.value,
                    "dist_to_pickup_km": round(g_dist_pickup, 1),
                    "total_distance_km": round(g.total_distance_km, 1),
                    "total_time_min": round(g.estimated_total_time_min, 1),
                    "dispatched_at": g.dispatched_at.isoformat() if g.dispatched_at else None,
                    "deadline_budget_min": round(g_deadline_budget, 1),
                }
                # Add package-level delivery timing
                if g.package_deliveries:
                    g_entry["package_deliveries"] = [
                        {
                            "package_id": pd.package_id,
                            "on_time": pd.on_time,
                            "slack_min": round(pd.slack_min, 1) if pd.slack_min is not None else None,
                        }
                        for pd in g.package_deliveries
                    ]
                entry["greedy"] = g_entry

        if h:
            h_ref = h.dispatched_at if h.dispatched_at else order.created_at
            h_deadline_budget = (order.tightest_deadline - h_ref).total_seconds() / 60
            hd = drivers_by_id.get(h.driver_id)
            if hd:
                h_dist_pickup = road_distance_km(hd.current_location, order.pickup_location)
                h_entry = {
                    "driver_id": hd.id,
                    "driver_name": hd.name,
                    "vehicle": hd.vehicle_type.value,
                    "cold_storage": hd.cold_storage.value,
                    "dist_to_pickup_km": round(h_dist_pickup, 1),
                    "total_distance_km": round(h.total_distance_km, 1),
                    "total_time_min": round(h.estimated_total_time_min, 1),
                    "dispatched_at": h.dispatched_at.isoformat() if h.dispatched_at else None,
                    "deadline_budget_min": round(h_deadline_budget, 1),
                    "cost_breakdown": {k: round(v, 2) if isinstance(v, float) else v
                                       for k, v in h.cost_breakdown.items()},
                }
                if h.package_deliveries:
                    h_entry["package_deliveries"] = [
                        {
                            "package_id": pd.package_id,
                            "on_time": pd.on_time,
                            "slack_min": round(pd.slack_min, 1) if pd.slack_min is not None else None,
                        }
                        for pd in h.package_deliveries
                    ]
                entry["optimal"] = h_entry

        # Detect problems
        if g and h:
            dist_diff = g.total_distance_km - h.total_distance_km
            if dist_diff > 2:
                entry["problems"].append({
                    "type": "wasted_distance",
                    "severity": "warning",
                    "message": f"Greedy wastes {dist_diff:.1f} km extra distance",
                })

            # Over-qualification
            gd = drivers_by_id.get(g.driver_id)
            if gd:
                needed = _max_temp_needed(order)
                g_lvl = _cold_level(gd.cold_storage)
                if g_lvl > needed + 1:
                    entry["problems"].append({
                        "type": "overqualified",
                        "severity": "error",
                        "message": f"Sends {gd.cold_storage.value} driver for {list(order.required_temp_regimes)[0].value} order -- wastes scarce resource",
                    })

            # Deadline miss — use package_deliveries when available
            g_any_late = any(not pd.on_time for pd in g.package_deliveries) if g.package_deliveries else False
            h_any_late = any(not pd.on_time for pd in h.package_deliveries) if h.package_deliveries else False

            if g_any_late and not h_any_late:
                entry["problems"].append({
                    "type": "deadline_miss",
                    "severity": "error",
                    "message": f"Greedy misses deadlines. Hungarian makes all deadlines.",
                })
            elif not g.package_deliveries and g.dispatched_at and h.dispatched_at:
                # Fallback proxy for legacy data
                g_budget = (order.tightest_deadline - g.dispatched_at).total_seconds() / 60
                h_budget = (order.tightest_deadline - h.dispatched_at).total_seconds() / 60
                if g.estimated_total_time_min > g_budget and h.estimated_total_time_min <= h_budget:
                    entry["problems"].append({
                        "type": "deadline_miss",
                        "severity": "error",
                        "message": f"Greedy takes {g.estimated_total_time_min:.0f} min but only {g_budget:.0f} min remain. Hungarian makes it in {h.estimated_total_time_min:.0f} min with {h_budget:.0f} min budget.",
                    })

        elif not g and h:
            entry["problems"].append({
                "type": "unassigned",
                "severity": "error",
                "message": "Greedy left this order unserved -- exhausted better-suited drivers on worse matches earlier",
            })
        elif g and not h:
            entry["problems"].append({
                "type": "optimal_skipped",
                "severity": "info",
                "message": "Hungarian chose not to assign this order (infeasible or low priority)",
            })
        elif not g and not h:
            entry["problems"].append({
                "type": "both_unassigned",
                "severity": "info",
                "message": "Neither algorithm could assign this order (no feasible driver available at dispatch time)",
            })

        assignment_logs.append(entry)

    # Driver utilization analysis
    g_driver_orders: dict[str, list[str]] = {}
    h_driver_orders: dict[str, list[str]] = {}
    g_driver_dist: dict[str, float] = {}
    h_driver_dist: dict[str, float] = {}

    for a in greedy_result.assignments:
        g_driver_orders.setdefault(a.driver_id, []).append(a.order_id)
        g_driver_dist[a.driver_id] = g_driver_dist.get(a.driver_id, 0) + a.total_distance_km

    for a in hungarian_result.assignments:
        h_driver_orders.setdefault(a.driver_id, []).append(a.order_id)
        h_driver_dist[a.driver_id] = h_driver_dist.get(a.driver_id, 0) + a.total_distance_km

    driver_logs = []
    for d in scenario.drivers:
        g_o = g_driver_orders.get(d.id, [])
        h_o = h_driver_orders.get(d.id, [])
        g_d = g_driver_dist.get(d.id, 0)
        h_d = h_driver_dist.get(d.id, 0)

        notes = []
        if d.cold_storage in (ColdStorage.CRYO, ColdStorage.ACTIVE_FRIDGE) and g_o:
            for oid in g_o:
                o = orders_by_id.get(oid)
                if o and all(p.temp_regime == TempRegime.AMBIENT for p in o.packages):
                    notes.append(f"Greedy wasted {d.cold_storage.value} driver on ambient-only order!")
                    break

        if not g_o and h_o:
            notes.append("Hungarian found useful work for this idle driver")
        elif g_o and not h_o:
            notes.append("Hungarian freed this driver (reassigned their order to someone better)")

        driver_logs.append({
            "driver_id": d.id,
            "name": d.name,
            "cold_storage": d.cold_storage.value,
            "vehicle": d.vehicle_type.value,
            "certs": [c.value for c in d.certifications],
            "greedy_orders": len(g_o),
            "greedy_distance_km": round(g_d, 1),
            "optimal_orders": len(h_o),
            "optimal_distance_km": round(h_d, 1),
            "notes": notes,
        })

    # Summary stats from real metrics
    g_total_dist = greedy_metrics.total_distance_km
    h_total_dist = hungarian_metrics.total_distance_km

    summary = {
        "distance_wasted_km": round(g_total_dist - h_total_dist, 1),
        "distance_pct_improvement": round((1 - h_total_dist / g_total_dist) * 100, 1) if g_total_dist > 0 else 0,
        "wait_time_saved_min": round(greedy_metrics.avg_pickup_wait_min - hungarian_metrics.avg_pickup_wait_min, 1),
        "greedy_deadline_pct": round(greedy_metrics.deadline_compliance_rate, 0),
        "optimal_deadline_pct": round(hungarian_metrics.deadline_compliance_rate, 0),
        "greedy_pkg_deadline_pct": round(greedy_metrics.package_deadline_compliance_rate, 0),
        "optimal_pkg_deadline_pct": round(hungarian_metrics.package_deadline_compliance_rate, 0),
        "greedy_overqualified": greedy_metrics.overqualified_assignments,
        "optimal_overqualified": hungarian_metrics.overqualified_assignments,
        "greedy_unassigned": len(greedy_result.unassigned_orders),
        "optimal_unassigned": len(hungarian_result.unassigned_orders),
        "greedy_cost_per_pkg": round(greedy_metrics.cost_per_package_km, 2),
        "optimal_cost_per_pkg": round(hungarian_metrics.cost_per_package_km, 2),
        "greedy_assignment_rate": round(greedy_metrics.assignment_rate, 1),
        "optimal_assignment_rate": round(hungarian_metrics.assignment_rate, 1),
        "greedy_dispatch_epochs": g_sim.dispatch_epochs,
        "optimal_dispatch_epochs": h_sim.dispatch_epochs,
        "greedy_validation_rejections": g_sim.validation_rejections,
        "optimal_validation_rejections": h_sim.validation_rejections,
        "problems_found": sum(1 for a in assignment_logs if a["problems"]),
        "root_causes": [
            "Sequential decision-making: the greedy dispatcher assigns one order at a time. Each decision is locally rational but globally suboptimal. Assigning Driver A here might leave no feasible option for Order Y arriving 10 minutes later.",
            "No delivery route optimization: when an order has multiple packages going to different destinations, greedy delivers in the order destinations appear. The Hungarian path uses nearest-neighbor + 2-opt to find a shorter delivery sequence.",
            "Equipment preference vs. optimization: the dispatcher tries not to waste high-capability drivers on standard orders, but under load still makes suboptimal equipment allocations. The Hungarian algorithm finds the lowest-cost 1:1 assignment within each dispatch epoch under the modeled cost function.",
            "No pooling: every order gets a dedicated driver dispatch. Two facilities 500m apart each get separate drivers, even though one trip could serve both. (Note: pooling is not yet integrated into either solver -- this is a known limitation.)",
        ],
    }

    return {
        "assignments": assignment_logs,
        "drivers": driver_logs,
        "summary": summary,
    }
