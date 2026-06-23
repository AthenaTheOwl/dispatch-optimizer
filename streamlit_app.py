"""Streamlit viewer for the dispatch optimizer."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.solvers.base import SolverRegistry
from app.solvers.constraints import DefaultConstraintChecker
from experiments.runner import run_experiment
from experiments.scenarios import generate_scenario_bank


def run_small_comparison(seed: int, scenario_count: int) -> pd.DataFrame:
    checker = DefaultConstraintChecker()
    greedy = SolverRegistry.get_solver("greedy", "greedy_scorer", checker, "none")
    hungarian = SolverRegistry.get_solver("hungarian", "composite", checker, "nn_2opt")
    bank = generate_scenario_bank(count=scenario_count, base_seed=seed)
    result = run_experiment([greedy, hungarian], bank, save=False)
    rows = []
    for run in result.runs:
        rows.append(
            {
                "solver": run.solver_config.solver_name,
                "seed": run.scenario_seed,
                "assignment_rate": run.metrics.assignment_rate,
                "package_deadline_rate": run.metrics.package_deadline_compliance_rate,
                "distance_km": run.metrics.total_distance_km,
                "drivers_used": run.metrics.drivers_used,
                "dispatch_epochs": run.dispatch_epochs,
                "elapsed_ms": round(run.elapsed_ms, 1),
            }
        )
    return pd.DataFrame(rows)


st.set_page_config(page_title="Dispatch optimizer", layout="wide")
st.title("Dispatch optimizer")
st.caption("Greedy and Hungarian dispatch policies on the same event-driven scenarios.")

with st.sidebar:
    seed = st.number_input("base seed", value=42, step=1)
    scenario_count = st.slider("scenario count", min_value=1, max_value=5, value=2)

df = run_small_comparison(int(seed), int(scenario_count))

summary = (
    df.groupby("solver", as_index=False)
    .agg(
        assignment_rate=("assignment_rate", "mean"),
        package_deadline_rate=("package_deadline_rate", "mean"),
        distance_km=("distance_km", "mean"),
        drivers_used=("drivers_used", "mean"),
    )
    .round(3)
)

st.subheader("Policy summary")
st.dataframe(summary, width="stretch", hide_index=True)

st.subheader("Run detail")
st.dataframe(df, width="stretch", hide_index=True)

st.subheader("Run locally")
st.code(
    "python -m pip install -r requirements.txt\n"
    "python -m pytest -q\n"
    "python -m streamlit run streamlit_app.py",
    language="bash",
)
