"""Parameter sweep — grid search over cost function weights.

Finds optimal cost function parameters by running every combination
against a bank of scenarios through the event-driven simulation engine.
"""

from __future__ import annotations

import itertools
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models import Scenario
from app.simulation.engine import run_simulation
from app.solvers.base import Solver, ConstraintChecker, RouteOptimizer, SolverConfig
from experiments.metrics import ExperimentMetrics, compute_experiment_metrics
from experiments.results.store import save_experiment


@dataclass
class SweepConfig:
    """Configuration for a parameter sweep."""
    solver_class: type[Solver]
    cost_function_class: type
    constraint_checker: ConstraintChecker
    route_optimizer: RouteOptimizer
    param_grid: dict[str, list[Any]]
    target_metric: str = "total_distance_km"
    minimize: bool = True

    @property
    def num_combinations(self) -> int:
        counts = [len(v) for v in self.param_grid.values()]
        result = 1
        for c in counts:
            result *= c
        return result


@dataclass
class SweepResult:
    """Result of a parameter sweep."""
    experiment_id: str
    target_metric: str
    minimize: bool
    results: list[dict[str, Any]]  # sorted by target metric

    @property
    def best(self) -> dict[str, Any]:
        return self.results[0] if self.results else {}

    @property
    def best_params(self) -> dict[str, Any]:
        return self.best.get("params", {})

    @property
    def best_score(self) -> float:
        return self.best.get(f"mean_{self.target_metric}", float("inf"))


def run_sweep(
    config: SweepConfig,
    scenarios: list[tuple[Scenario, int]],
    description: str = "",
    save: bool = True,
    dispatch_interval_minutes: float = 5.0,
    max_pending_wait_minutes: float = 30.0,
) -> SweepResult:
    """Run a grid search over cost function parameters via event-driven simulation.

    For each parameter combination:
    1. Build the solver with those parameters
    2. Run on all scenarios through the simulation engine
    3. Compute mean metrics across scenarios
    4. Rank by target metric

    Returns results sorted by target metric (best first).
    """
    experiment_id = uuid.uuid4().hex[:12]

    # Generate all parameter combinations
    param_names = list(config.param_grid.keys())
    param_values = list(config.param_grid.values())
    combinations = list(itertools.product(*param_values))

    results: list[dict[str, Any]] = []

    for combo in combinations:
        params = dict(zip(param_names, combo))

        # Build solver with these params
        cost_fn = config.cost_function_class(**params)
        solver = config.solver_class(
            cost_function=cost_fn,
            constraint_checker=config.constraint_checker,
            route_optimizer=config.route_optimizer,
        )

        # Run on all scenarios through event-driven simulation
        all_metrics: list[ExperimentMetrics] = []
        total_rejections = 0
        for scenario, seed in scenarios:
            sim_result = run_simulation(
                solver, scenario,
                dispatch_interval_minutes=dispatch_interval_minutes,
                max_pending_wait_minutes=max_pending_wait_minutes,
            )
            dispatch_result = sim_result.to_dispatch_result()
            metrics = compute_experiment_metrics(dispatch_result, scenario)
            all_metrics.append(metrics)
            total_rejections += sim_result.validation_rejections

        # Aggregate
        entry: dict[str, Any] = {
            "params": params,
            "total_validation_rejections": total_rejections,
        }
        key_metrics = [
            "total_distance_km", "deadline_compliance_rate",
            "package_deadline_compliance_rate",
            "avg_pickup_wait_min", "overqualification_rate",
            "assignment_rate", "drivers_used",
        ]
        for metric in key_metrics:
            values = [getattr(m, metric) for m in all_metrics]
            entry[f"mean_{metric}"] = round(statistics.mean(values), 4)
            if len(values) > 1:
                entry[f"std_{metric}"] = round(statistics.stdev(values), 4)

        results.append(entry)

    # Sort by target metric
    results.sort(
        key=lambda r: r.get(f"mean_{config.target_metric}", float("inf")),
        reverse=not config.minimize,
    )

    # Add rank
    for i, r in enumerate(results):
        r["rank"] = i + 1

    sweep_result = SweepResult(
        experiment_id=experiment_id,
        target_metric=config.target_metric,
        minimize=config.minimize,
        results=results,
    )

    if save:
        save_experiment(
            experiment_id=experiment_id,
            description=f"Parameter sweep: {description}",
            runs=results,
            summary={
                "target_metric": config.target_metric,
                "minimize": config.minimize,
                "num_combinations": len(combinations),
                "param_grid": {k: [str(v) for v in vals] for k, vals in config.param_grid.items()},
                "best_params": sweep_result.best_params,
                "best_score": sweep_result.best_score,
                "simulation": {
                    "dispatch_interval_minutes": dispatch_interval_minutes,
                    "max_pending_wait_minutes": max_pending_wait_minutes,
                    "engine": "event_driven",
                },
            },
        )

    return sweep_result
