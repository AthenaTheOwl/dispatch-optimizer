"""API endpoints for the DispatchIQ dispatch system."""

from datetime import datetime, timedelta
from fastapi import APIRouter
from pydantic import BaseModel

from app.models import Scenario
from app.simulation.city import ALL_FACILITIES
from app.simulation.orders import generate_orders, generate_field_collection_orders
from app.simulation.drivers import generate_drivers
from app.algorithms.greedy import greedy_dispatch
from app.algorithms.hungarian import hungarian_dispatch
from app.comparison import compare_algorithms, compute_metrics
from app.analysis import generate_analysis

router = APIRouter()

# In-memory scenario storage (single session, no DB needed)
_current_scenario: Scenario | None = None


class ScenarioConfig(BaseModel):
    num_drivers: int = 12
    num_orders: int = 20
    seed: int | None = 42
    include_field_collection: bool = False
    num_field_visits: int = 3


def _scenario_to_dict(scenario: Scenario) -> dict:
    """Convert scenario to JSON-serializable dict for the frontend."""
    return {
        "facilities": [
            {
                "name": f.name,
                "lat": f.lat,
                "lng": f.lng,
                "type": f.facility_type.value if f.facility_type else "unknown",
            }
            for f in scenario.facilities
        ],
        "drivers": [
            {
                "id": d.id,
                "name": d.name,
                "lat": d.current_location.lat,
                "lng": d.current_location.lng,
                "location_name": d.current_location.name,
                "vehicle_type": d.vehicle_type.value,
                "cold_storage": d.cold_storage.value,
                "certifications": [c.value for c in d.certifications],
                "status": d.status.value,
                "capacity": d.capacity,
                "current_load": d.current_load,
                "shift_start": d.shift_start.isoformat(),
                "shift_end": d.shift_end.isoformat(),
            }
            for d in scenario.drivers
        ],
        "orders": [
            {
                "id": o.id,
                "pickup_lat": o.pickup_location.lat,
                "pickup_lng": o.pickup_location.lng,
                "pickup_name": o.pickup_location.name,
                "pickup_type": o.pickup_type.value,
                "urgency": o.urgency.value,
                "created_at": o.created_at.isoformat(),
                "tracking_required": o.tracking_required,
                "num_packages": o.total_packages,
                "tightest_deadline": o.tightest_deadline.isoformat(),
                "packages": [
                    {
                        "id": p.id,
                        "cargo_type": p.cargo_type.value,
                        "temp_regime": p.temp_regime.value,
                        "destination_name": p.destination.name,
                        "destination_lat": p.destination.lat,
                        "destination_lng": p.destination.lng,
                        "hazard_class": p.hazard_class.value,
                        "deadline": p.deadline.isoformat(),
                        "special_handling": p.special_handling,
                    }
                    for p in o.packages
                ],
            }
            for o in scenario.orders
        ],
        "current_time": scenario.current_time.isoformat(),
    }


def _dispatch_result_to_dict(result, orders, drivers) -> dict:
    """Convert dispatch result + metrics to JSON-serializable dict."""
    metrics = compute_metrics(result, orders, drivers)

    return {
        "algorithm_name": result.algorithm_name,
        "assignments": [
            {
                "driver_id": a.driver_id,
                "order_id": a.order_id,
                "pickup_time_min": round(a.estimated_pickup_time_min, 1),
                "total_time_min": round(a.estimated_total_time_min, 1),
                "distance_km": round(a.total_distance_km, 2),
                "cost_score": round(a.cost_score, 2),
                "cost_breakdown": {k: round(v, 2) if isinstance(v, float) else v
                                   for k, v in a.cost_breakdown.items()},
                "route": {
                    "stops": [
                        {
                            "lat": s.location.lat,
                            "lng": s.location.lng,
                            "name": s.location.name,
                            "type": s.stop_type.value,
                            "order_id": s.order_id,
                            "package_ids": s.package_ids,
                        }
                        for s in a.route.stops
                    ],
                    "total_distance_km": round(a.route.total_distance_km, 2),
                    "total_time_min": round(a.route.total_time_minutes, 1),
                    "num_stops": a.route.num_stops,
                },
            }
            for a in result.assignments
        ],
        "unassigned_orders": result.unassigned_orders,
        "metrics": {k: round(v, 2) if isinstance(v, float) else v for k, v in metrics.items()},
    }


@router.post("/api/scenario/generate")
def generate_scenario(config: ScenarioConfig):
    """Generate a new simulation scenario."""
    global _current_scenario

    base_time = datetime(2026, 3, 21, 7, 0)  # 7am start

    drivers = generate_drivers(config.num_drivers, base_time, seed=config.seed)
    orders = generate_orders(config.num_orders, base_time, seed=config.seed)

    if config.include_field_collection:
        field_orders = generate_field_collection_orders(
            config.num_field_visits, base_time, seed=config.seed
        )
        orders.extend(field_orders)

    _current_scenario = Scenario(
        drivers=drivers,
        orders=orders,
        facilities=ALL_FACILITIES,
        current_time=base_time,
    )

    return _scenario_to_dict(_current_scenario)


@router.post("/api/dispatch/greedy")
def run_greedy():
    """Run greedy nearest-driver algorithm on current scenario."""
    if _current_scenario is None:
        return {"error": "No scenario generated. Call /api/scenario/generate first."}

    result = greedy_dispatch(
        _current_scenario.drivers,
        _current_scenario.orders,
        _current_scenario.current_time,
    )

    return _dispatch_result_to_dict(result, _current_scenario.orders, _current_scenario.drivers)


@router.post("/api/dispatch/hungarian")
def run_hungarian():
    """Run Hungarian optimal assignment on current scenario."""
    if _current_scenario is None:
        return {"error": "No scenario generated. Call /api/scenario/generate first."}

    result = hungarian_dispatch(
        _current_scenario.drivers,
        _current_scenario.orders,
        _current_scenario.current_time,
    )

    return _dispatch_result_to_dict(result, _current_scenario.orders, _current_scenario.drivers)


@router.post("/api/dispatch/compare")
def run_comparison():
    """Run both algorithms and return side-by-side comparison."""
    if _current_scenario is None:
        return {"error": "No scenario generated. Call /api/scenario/generate first."}

    comparison = compare_algorithms(_current_scenario)

    greedy_dict = _dispatch_result_to_dict(
        comparison["greedy"]["result"],
        _current_scenario.orders,
        _current_scenario.drivers,
    )
    hungarian_dict = _dispatch_result_to_dict(
        comparison["hungarian"]["result"],
        _current_scenario.orders,
        _current_scenario.drivers,
    )

    return {
        "greedy": greedy_dict,
        "hungarian": hungarian_dict,
        "deltas": {k: round(v, 1) if isinstance(v, float) else v
                   for k, v in comparison["deltas"].items()},
        "pooling": comparison["pooling"],
    }


@router.get("/api/scenario/presets")
def get_presets():
    """Return available preset scenarios."""
    return {
        "presets": [
            {
                "name": "Peak Load",
                "description": "Heavy demand: 15 drivers, 30 orders",
                "config": {"num_drivers": 15, "num_orders": 30, "seed": 42},
            },
            {
                "name": "Emergency Surge",
                "description": "High-urgency scenario: 10 drivers, 20 critical orders",
                "config": {"num_drivers": 10, "num_orders": 20, "seed": 99},
            },
            {
                "name": "Field Collection Mix",
                "description": "Standard orders + field collection visits",
                "config": {
                    "num_drivers": 12, "num_orders": 15, "seed": 77,
                    "include_field_collection": True, "num_field_visits": 5,
                },
            },
            {
                "name": "High Volume",
                "description": "Stress test: 12 drivers handling 35 orders",
                "config": {"num_drivers": 12, "num_orders": 35, "seed": 123},
            },
        ]
    }


@router.post("/api/dispatch/analysis")
def run_analysis():
    """Generate detailed analysis of why greedy is worse than optimal."""
    if _current_scenario is None:
        return {"error": "No scenario generated. Call /api/scenario/generate first."}

    return generate_analysis(_current_scenario)
