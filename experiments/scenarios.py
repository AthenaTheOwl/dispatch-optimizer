"""Scenario bank — deterministic scenario generation, save, and replay.

Every experiment must be reproducible. Same seed → same scenario → same result.
Scenarios can be saved to disk and replayed against different solver configurations.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime
from pathlib import Path

from app.models import Scenario
from app.simulation.drivers import generate_drivers
from app.simulation.orders import generate_orders
from app.simulation.city import HOSPITALS, CLINICS, LABS, NURSING_HOMES

SCENARIO_DIR = Path(__file__).parent / "results" / "scenarios"


def generate_scenario(
    num_drivers: int = 12,
    num_orders: int = 20,
    seed: int | None = None,
) -> tuple[Scenario, int]:
    """Generate a scenario with a fixed seed for reproducibility.

    Returns (scenario, seed_used). If seed is None, a random seed is picked
    and returned so it can be recorded.
    """
    if seed is None:
        seed = random.randint(0, 2**31 - 1)

    random.seed(seed)

    facilities = HOSPITALS + CLINICS + LABS + NURSING_HOMES
    current_time = datetime(2024, 3, 15, 8, 0, 0)

    drivers = generate_drivers(num_drivers, current_time)
    orders = generate_orders(num_orders, current_time, seed=seed)

    scenario = Scenario(
        drivers=drivers,
        orders=orders,
        facilities=facilities,
        current_time=current_time,
    )

    return scenario, seed


def scenario_hash(scenario: Scenario) -> str:
    """Deterministic hash of a scenario including actual contents.

    Includes driver locations/capabilities and order locations/urgency/packages
    so that two scenarios with the same IDs but different content hash differently.
    """
    driver_data = tuple(
        (d.id, d.current_location.lat, d.current_location.lng,
         d.vehicle_type.value, d.cold_storage.value,
         tuple(c.value for c in d.certifications),
         d.shift_start.isoformat(), d.shift_end.isoformat())
        for d in scenario.drivers
    )
    order_data = tuple(
        (o.id, o.pickup_location.lat, o.pickup_location.lng,
         o.urgency.value, o.created_at.isoformat(),
         tuple(
             (p.id, p.specimen_type.value, p.temp_regime.value,
              p.destination.lat, p.destination.lng, p.deadline.isoformat())
             for p in o.packages
         ))
        for o in scenario.orders
    )
    key = (
        scenario.current_time.isoformat(),
        driver_data,
        order_data,
    )
    return hashlib.sha256(str(key).encode()).hexdigest()[:12]


def save_scenario(scenario: Scenario, seed: int, name: str | None = None) -> Path:
    """Save a scenario to disk for replay."""
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)

    sid = scenario_hash(scenario)
    label = name or f"scenario_{sid}"
    path = SCENARIO_DIR / f"{label}.json"

    data = {
        "seed": seed,
        "hash": sid,
        "num_drivers": len(scenario.drivers),
        "num_orders": len(scenario.orders),
        "current_time": scenario.current_time.isoformat(),
        "scenario": scenario.model_dump(mode="json"),
    }

    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def load_scenario(path: Path) -> tuple[Scenario, int]:
    """Load a saved scenario from disk."""
    data = json.loads(path.read_text())
    scenario = Scenario.model_validate(data["scenario"])
    return scenario, data["seed"]


def list_saved_scenarios() -> list[dict]:
    """List all saved scenarios with metadata."""
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for f in sorted(SCENARIO_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            results.append({
                "name": f.stem,
                "path": str(f),
                "seed": data.get("seed"),
                "hash": data.get("hash"),
                "num_drivers": data.get("num_drivers"),
                "num_orders": data.get("num_orders"),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def generate_scenario_bank(
    count: int = 10,
    num_drivers: int = 12,
    num_orders: int = 20,
    base_seed: int = 42,
) -> list[tuple[Scenario, int]]:
    """Generate a bank of scenarios with sequential seeds.

    For parameter sweeps: run every configuration against the same bank
    of scenarios for fair comparison.
    """
    bank = []
    for i in range(count):
        scenario, seed = generate_scenario(
            num_drivers=num_drivers,
            num_orders=num_orders,
            seed=base_seed + i,
        )
        bank.append((scenario, seed))
    return bank
