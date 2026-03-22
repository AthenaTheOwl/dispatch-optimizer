"""Experiment runner — compare solvers on identical scenarios.

All experiments run through the event-driven simulation engine.
No direct solver.solve() calls — everything goes through run_simulation().
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models import Scenario
from app.simulation.engine import run_simulation
from app.solvers.base import Solver, SolverConfig
from experiments.metrics import ExperimentMetrics, compute_experiment_metrics
from experiments.scenarios import scenario_hash
from experiments.results.store import save_run, save_experiment


@dataclass
class RunResult:
    """Result of a single solver run on a single scenario."""
    solver_config: SolverConfig
    scenario_seed: int
    scenario_hash: str
    metrics: ExperimentMetrics
    elapsed_ms: float
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    # Simulation parameters
    dispatch_interval_minutes: float = 5.0
    dispatch_epochs: int = 0
    validation_rejections: int = 0


@dataclass
class ExperimentResult:
    """Complete result of an experiment (multiple solvers x multiple scenarios)."""
    experiment_id: str
    description: str
    runs: list[RunResult]
    summary: dict[str, Any] = field(default_factory=dict)

    def runs_for_config(self, solver_name: str, cost_function_name: str | None = None) -> list[RunResult]:
        """Filter runs by solver name, optionally by cost function name."""
        results = [r for r in self.runs if r.solver_config.solver_name == solver_name]
        if cost_function_name:
            results = [r for r in results if r.solver_config.cost_function_name == cost_function_name]
        return results

    def mean_metric(self, solver_name: str, metric: str, cost_function_name: str | None = None) -> float:
        runs = self.runs_for_config(solver_name, cost_function_name)
        values = [getattr(r.metrics, metric) for r in runs if hasattr(r.metrics, metric)]
        return statistics.mean(values) if values else 0.0

    def compare_configs(self, metric: str) -> dict[str, float]:
        """Return mean of a metric for each unique solver config."""
        import json
        configs: dict[str, list[float]] = {}
        for r in self.runs:
            key = f"{r.solver_config.solver_name}/{r.solver_config.cost_function_name}"
            configs.setdefault(key, []).append(getattr(r.metrics, metric, 0.0))
        return {k: statistics.mean(v) for k, v in configs.items()}


def run_experiment(
    solvers: list[Solver],
    scenarios: list[tuple[Scenario, int]],
    description: str = "",
    save: bool = True,
    dispatch_interval_minutes: float = 5.0,
    max_pending_wait_minutes: float = 30.0,
) -> ExperimentResult:
    """Run multiple solvers against a bank of scenarios via event-driven simulation.

    Args:
        solvers: Solver instances to compare
        scenarios: List of (Scenario, seed) pairs
        description: Human-readable description for the experiment log
        save: Whether to persist results to disk
        dispatch_interval_minutes: Simulation dispatch interval
        max_pending_wait_minutes: Max time an order can wait before timeout

    Returns:
        ExperimentResult with all runs and summary statistics
    """
    experiment_id = uuid.uuid4().hex[:12]
    runs: list[RunResult] = []

    for scenario, seed in scenarios:
        s_hash = scenario_hash(scenario)

        for solver in solvers:
            start = time.perf_counter()
            sim_result = run_simulation(
                solver, scenario,
                dispatch_interval_minutes=dispatch_interval_minutes,
                max_pending_wait_minutes=max_pending_wait_minutes,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            dispatch_result = sim_result.to_dispatch_result()
            metrics = compute_experiment_metrics(dispatch_result, scenario)

            run = RunResult(
                solver_config=solver.config,
                scenario_seed=seed,
                scenario_hash=s_hash,
                metrics=metrics,
                elapsed_ms=elapsed_ms,
                dispatch_interval_minutes=dispatch_interval_minutes,
                dispatch_epochs=sim_result.dispatch_epochs,
                validation_rejections=sim_result.validation_rejections,
            )
            runs.append(run)

            if save:
                save_run(
                    experiment_id=experiment_id,
                    run_id=run.run_id,
                    solver_config=solver.config,
                    scenario_seed=seed,
                    scenario_hash=s_hash,
                    metrics=metrics,
                    metadata={
                        "elapsed_ms": elapsed_ms,
                        "dispatch_interval_minutes": dispatch_interval_minutes,
                        "dispatch_epochs": sim_result.dispatch_epochs,
                        "validation_rejections": sim_result.validation_rejections,
                    },
                )

    # Build summary keyed by full config, not just solver_name
    def _config_key(config: SolverConfig) -> str:
        import json
        return json.dumps(config.to_dict(), sort_keys=True, default=str)

    config_keys = list(dict.fromkeys(_config_key(r.solver_config) for r in runs))
    key_metrics = [
        "total_distance_km", "deadline_compliance_rate",
        "package_deadline_compliance_rate",
        "avg_pickup_wait_min", "overqualification_rate",
        "drivers_used", "assignment_rate",
        "validation_rejections",
    ]

    summary: dict[str, Any] = {"solvers": {}}
    config_labels: list[str] = []
    for ck in config_keys:
        matching_runs = [r for r in runs if _config_key(r.solver_config) == ck]
        config = matching_runs[0].solver_config
        label = f"{config.solver_name}/{config.cost_function_name}"
        if label in summary["solvers"]:
            import hashlib as _hl
            label = f"{label}_{_hl.sha256(ck.encode()).hexdigest()[:6]}"
        config_labels.append(label)

        solver_summary: dict[str, Any] = {
            "num_runs": len(matching_runs),
            "config": config.to_dict(),
            "avg_elapsed_ms": round(statistics.mean(r.elapsed_ms for r in matching_runs), 2),
            "avg_dispatch_epochs": round(statistics.mean(r.dispatch_epochs for r in matching_runs), 1),
            "total_validation_rejections": sum(r.validation_rejections for r in matching_runs),
        }
        for metric in key_metrics:
            values = [getattr(r.metrics, metric) for r in matching_runs]
            solver_summary[f"mean_{metric}"] = round(statistics.mean(values), 4) if values else 0
            if len(values) > 1:
                solver_summary[f"std_{metric}"] = round(statistics.stdev(values), 4)
        summary["solvers"][label] = solver_summary

    # Compute deltas between solvers (pairwise)
    if len(config_labels) == 2:
        a, b = config_labels
        deltas: dict[str, float] = {}
        for metric in key_metrics:
            a_mean = summary["solvers"][a].get(f"mean_{metric}", 0)
            b_mean = summary["solvers"][b].get(f"mean_{metric}", 0)
            if a_mean != 0:
                deltas[metric] = round((b_mean - a_mean) / abs(a_mean) * 100, 2)
        summary["deltas"] = {f"{a}_vs_{b}": deltas}

    # Simulation parameters
    summary["simulation"] = {
        "dispatch_interval_minutes": dispatch_interval_minutes,
        "max_pending_wait_minutes": max_pending_wait_minutes,
        "engine": "event_driven",
    }

    experiment = ExperimentResult(
        experiment_id=experiment_id,
        description=description,
        runs=runs,
        summary=summary,
    )

    if save:
        run_dicts = [
            {
                "run_id": r.run_id,
                "solver": r.solver_config.to_dict(),
                "scenario_seed": r.scenario_seed,
                "metrics": r.metrics.to_dict(),
                "elapsed_ms": round(r.elapsed_ms, 2),
                "dispatch_epochs": r.dispatch_epochs,
                "validation_rejections": r.validation_rejections,
            }
            for r in runs
        ]
        save_experiment(experiment_id, description, run_dicts, summary)

    return experiment
