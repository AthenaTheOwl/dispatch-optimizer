"""Experiment results persistence — every run is logged for reproducibility.

Results are stored as JSON files. Each experiment gets a unique ID.
This is the experiment journal that an acquirer's due diligence team reviews.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.solvers.base import SolverConfig
from experiments.metrics import ExperimentMetrics

RESULTS_DIR = Path(__file__).parent


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def save_run(
    experiment_id: str,
    run_id: str,
    solver_config: SolverConfig,
    scenario_seed: int,
    scenario_hash: str,
    metrics: ExperimentMetrics,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a single solver run result."""
    runs_dir = RESULTS_DIR / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "experiment_id": experiment_id,
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "solver_config": solver_config.to_dict(),
        "scenario_seed": scenario_seed,
        "scenario_hash": scenario_hash,
        "metrics": metrics.to_dict(),
        "metadata": metadata or {},
    }

    path = runs_dir / f"{run_id}.json"
    path.write_text(json.dumps(record, indent=2, default=_json_default))
    return path


def save_experiment(
    experiment_id: str,
    description: str,
    runs: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
) -> Path:
    """Save a complete experiment (multiple runs) as a single summary file."""
    experiments_dir = RESULTS_DIR / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "experiment_id": experiment_id,
        "timestamp": datetime.now().isoformat(),
        "description": description,
        "num_runs": len(runs),
        "runs": runs,
        "summary": summary or {},
    }

    path = experiments_dir / f"{experiment_id}.json"
    path.write_text(json.dumps(record, indent=2, default=_json_default))
    return path


def load_experiment(experiment_id: str) -> dict:
    """Load a saved experiment by ID."""
    path = RESULTS_DIR / "experiments" / f"{experiment_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Experiment not found: {experiment_id}")
    return json.loads(path.read_text())


def list_experiments() -> list[dict]:
    """List all saved experiments with metadata."""
    experiments_dir = RESULTS_DIR / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in sorted(experiments_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            results.append({
                "experiment_id": data["experiment_id"],
                "timestamp": data["timestamp"],
                "description": data["description"],
                "num_runs": data["num_runs"],
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results
