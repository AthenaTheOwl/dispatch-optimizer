"""Detailed analysis of greedy vs optimal dispatch — explains WHY greedy is worse."""

from datetime import datetime
from app.models import (
    Driver, Order, Scenario, ColdStorage, TempRegime,
    COLD_STORAGE_CAPABILITIES,
)
from app.simulation.distance import road_distance_km
from app.algorithms.greedy import greedy_dispatch
from app.algorithms.hungarian import hungarian_dispatch
from app.comparison import compute_metrics


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


def generate_analysis(scenario: Scenario) -> dict:
    """
    Generate detailed analysis logs comparing greedy vs optimal.
    Returns structured data suitable for both console and web display.
    """
    greedy_result = greedy_dispatch(scenario.drivers, scenario.orders, scenario.current_time)
    hungarian_result = hungarian_dispatch(scenario.drivers, scenario.orders, scenario.current_time)

    greedy_metrics = compute_metrics(greedy_result, scenario.orders, scenario.drivers)
    hungarian_metrics = compute_metrics(hungarian_result, scenario.orders, scenario.drivers)

    orders_by_id = {o.id: o for o in scenario.orders}
    drivers_by_id = {d.id: d for d in scenario.drivers}
    base_time = scenario.current_time

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
            "deadline_min": (order.tightest_deadline - base_time).total_seconds() / 60,
            "greedy": None,
            "optimal": None,
            "problems": [],
        }

        if g:
            gd = drivers_by_id[g.driver_id]
            g_dist_pickup = road_distance_km(gd.current_location, order.pickup_location)
            entry["greedy"] = {
                "driver_id": gd.id,
                "driver_name": gd.name,
                "vehicle": gd.vehicle_type.value,
                "cold_storage": gd.cold_storage.value,
                "dist_to_pickup_km": round(g_dist_pickup, 1),
                "total_distance_km": round(g.total_distance_km, 1),
                "total_time_min": round(g.estimated_total_time_min, 1),
            }

        if h:
            hd = drivers_by_id[h.driver_id]
            h_dist_pickup = road_distance_km(hd.current_location, order.pickup_location)
            entry["optimal"] = {
                "driver_id": hd.id,
                "driver_name": hd.name,
                "vehicle": hd.vehicle_type.value,
                "cold_storage": hd.cold_storage.value,
                "dist_to_pickup_km": round(h_dist_pickup, 1),
                "total_distance_km": round(h.total_distance_km, 1),
                "total_time_min": round(h.estimated_total_time_min, 1),
                "cost_breakdown": {k: round(v, 2) if isinstance(v, float) else v
                                   for k, v in h.cost_breakdown.items()},
            }

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
            gd = drivers_by_id[g.driver_id]
            needed = _max_temp_needed(order)
            g_lvl = _cold_level(gd.cold_storage)
            if g_lvl > needed + 1:
                entry["problems"].append({
                    "type": "overqualified",
                    "severity": "error",
                    "message": f"Sends {gd.cold_storage.value} driver for {list(order.required_temp_regimes)[0].value} order -- wastes scarce resource",
                })

            # Deadline miss
            deadline_min = entry["deadline_min"]
            if g.estimated_total_time_min > deadline_min and h.estimated_total_time_min <= deadline_min:
                entry["problems"].append({
                    "type": "deadline_miss",
                    "severity": "error",
                    "message": f"Greedy takes {g.estimated_total_time_min:.0f} min but deadline is {deadline_min:.0f} min. Optimal makes it in {h.estimated_total_time_min:.0f} min.",
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
                "message": "Optimal chose not to assign this order (infeasible or low priority)",
            })

        assignment_logs.append(entry)

    # Driver utilization analysis
    g_driver_orders = {}
    h_driver_orders = {}
    g_driver_dist = {}
    h_driver_dist = {}

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
            notes.append("Optimal found useful work for this idle driver")
        elif g_o and not h_o:
            notes.append("Optimal freed this driver (reassigned their order to someone better)")

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

    # Summary stats
    summary = {
        "distance_wasted_km": round(greedy_metrics["total_distance_km"] - hungarian_metrics["total_distance_km"], 1),
        "distance_pct_improvement": round((1 - hungarian_metrics["total_distance_km"] / greedy_metrics["total_distance_km"]) * 100, 1) if greedy_metrics["total_distance_km"] > 0 else 0,
        "wait_time_saved_min": round(greedy_metrics["avg_pickup_wait_min"] - hungarian_metrics["avg_pickup_wait_min"], 1),
        "greedy_deadline_pct": round(greedy_metrics["deadline_compliance_rate"], 0),
        "optimal_deadline_pct": round(hungarian_metrics["deadline_compliance_rate"], 0),
        "greedy_overqualified": int(greedy_metrics["overqualified_assignments"]),
        "optimal_overqualified": int(hungarian_metrics["overqualified_assignments"]),
        "greedy_unassigned": len(greedy_result.unassigned_orders),
        "optimal_unassigned": len(hungarian_result.unassigned_orders),
        "greedy_cost_per_pkg": round(greedy_metrics["cost_per_package_km"], 2),
        "optimal_cost_per_pkg": round(hungarian_metrics["cost_per_package_km"], 2),
        "problems_found": sum(1 for a in assignment_logs if a["problems"]),
        "root_causes": [
            "Sequential decision-making: even a smart greedy dispatcher assigns one order at a time. Each decision is locally rational -- good distance, equipment-aware, deadline-conscious -- but globally suboptimal. Assigning Driver A here might leave no feasible option for Order Y arriving 10 minutes later.",
            "Equipment preference vs. optimization: the dispatcher tries not to waste high-capability equipment on standard-tier orders (penalizes obvious mismatches), but under load they still make suboptimal equipment allocations. The Hungarian algorithm guarantees globally optimal equipment matching across ALL orders simultaneously.",
            "Deadline awareness without global slack management: greedy penalizes tight deadlines and sorts by deadline within urgency tiers, but doesn't weigh one order's slack against another's. It won't think 'this Routine has 3 hours of slack, I should save this driver for the critical order coming in 20 minutes.'",
            "No delivery route optimization: when an order has 3 packages going to 3 different destinations, greedy delivers in the order destinations appear. The optimal algorithm uses nearest-neighbor + 2-opt to find the shortest delivery sequence, saving km on every multi-stop route.",
            "No pooling: every order gets a dedicated driver dispatch. Two facilities 500m apart each get separate drivers, even though one trip could serve both. This is the biggest structural inefficiency.",
        ],
    }

    return {
        "assignments": assignment_logs,
        "drivers": driver_logs,
        "summary": summary,
    }
