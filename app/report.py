"""Headless scorecard generation for dispatch optimizer cohorts."""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.parent
SCORECARD_PATH = ROOT / "reports" / "scorecard.jsonl"
SCENARIOS_PER_COHORT = 3

METRICS = (
    "assignment_rate",
    "deadline_compliance_rate",
    "driver_utilization_pct",
    "total_distance_km",
)

HIGHER_IS_BETTER = {
    "assignment_rate": True,
    "deadline_compliance_rate": True,
    "driver_utilization_pct": True,
    "total_distance_km": False,
}


@dataclass(frozen=True)
class Cohort:
    name: str
    num_drivers: int
    num_orders: int
    seed: int


COHORTS = (
    Cohort("balanced", num_drivers=12, num_orders=20, seed=100),
    Cohort("understaffed", num_drivers=6, num_orders=20, seed=200),
    Cohort("high-volume", num_drivers=12, num_orders=40, seed=300),
    Cohort("overstaffed", num_drivers=20, num_orders=10, seed=400),
)


def _build_solvers() -> list[Any]:
    import app.solvers  # noqa: F401 - importing registers built-in solvers
    from app.solvers.base import SolverRegistry
    from app.solvers.constraints import DefaultConstraintChecker

    checker = DefaultConstraintChecker()
    return [
        SolverRegistry.get_solver("greedy", "greedy_scorer", checker, "none"),
        SolverRegistry.get_solver("hungarian", "composite", checker, "nn_2opt"),
    ]


def _mean_for(result: Any, solver_name: str, metric: str) -> float:
    runs = result.runs_for_config(solver_name)
    values = [getattr(run.metrics, metric) for run in runs]
    return round(statistics.mean(values), 4) if values else 0.0


def _cohort_rows(cohort: Cohort) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from experiments.runner import run_experiment
    from experiments.scenarios import generate_scenario_bank

    bank = generate_scenario_bank(
        count=SCENARIOS_PER_COHORT,
        num_drivers=cohort.num_drivers,
        num_orders=cohort.num_orders,
        base_seed=cohort.seed,
    )
    result = run_experiment(
        _build_solvers(),
        bank,
        description=f"{cohort.name} scorecard",
        save=False,
    )

    means = {
        solver: {metric: _mean_for(result, solver, metric) for metric in METRICS}
        for solver in ("greedy", "hungarian")
    }
    deltas = {
        metric: round(means["hungarian"][metric] - means["greedy"][metric], 4)
        for metric in METRICS
    }

    rows: list[dict[str, Any]] = []
    for solver in ("greedy", "hungarian"):
        row: dict[str, Any] = {
            "cohort": cohort.name,
            "solver": solver,
        }
        for metric in METRICS:
            row[f"mean_{metric}"] = means[solver][metric]
        for metric in METRICS:
            row[f"delta_{metric}"] = deltas[metric]
        rows.append(row)

    wins = {"greedy": 0, "hungarian": 0}
    for metric in METRICS:
        greedy_value = means["greedy"][metric]
        hungarian_value = means["hungarian"][metric]
        if greedy_value == hungarian_value:
            continue
        if HIGHER_IS_BETTER[metric]:
            winner = "hungarian" if hungarian_value > greedy_value else "greedy"
        else:
            winner = "hungarian" if hungarian_value < greedy_value else "greedy"
        wins[winner] += 1
    return rows, wins


def build_scorecard_rows() -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    overall_wins = {"greedy": 0, "hungarian": 0}

    for cohort in COHORTS:
        cohort_rows, cohort_wins = _cohort_rows(cohort)
        rows.extend(cohort_rows)
        for solver, wins in cohort_wins.items():
            overall_wins[solver] += wins

    if overall_wins["greedy"] == overall_wins["hungarian"]:
        winner = "tie"
    else:
        winner = max(overall_wins, key=overall_wins.__getitem__)
    return rows, winner


def write_scorecard(rows: list[dict[str, Any]], path: Path = SCORECARD_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")
    return path


def main() -> None:
    rows, winner = build_scorecard_rows()
    path = write_scorecard(rows)
    print(f"wrote {len(rows)} rows to {path.relative_to(ROOT)}; overall winner: {winner}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
