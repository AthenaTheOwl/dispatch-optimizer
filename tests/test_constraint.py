"""Constraint finder: stage accounting, determinism, and the binding-stage call."""

import json
import re
from datetime import timedelta

import pytest

from app.models import ColdStorage, TempRegime
from app.simulation.engine import run_simulation
from experiments.constraint import (
    STAGES,
    _build_solver,
    any_driver_could_take,
    compress_arrivals,
    main,
    order_stages,
    run,
)
from experiments.scenarios import generate_scenario

ACCEPTANCE_ARGS = ["--drivers", "6", "--orders", "40", "--seed", "7", "--front-speedup", "2.0"]


@pytest.mark.parametrize("speedup", [1.0, 2.0])
@pytest.mark.parametrize("policy", ["greedy", "hungarian"])
def test_stage_durations_sum_to_created_to_delivered(policy, speedup):
    scenario, _ = generate_scenario(num_drivers=6, num_orders=40, seed=7)
    if speedup != 1.0:
        scenario = compress_arrivals(scenario, speedup)
    result = run_simulation(_build_solver(policy), scenario)
    rows = {r.order_id: r for r in order_stages(scenario, result)}

    assert len(rows) == len(result.assignments) > 0
    created = {o.id: o.created_at for o in scenario.orders}
    for assignment in result.assignments:
        # Lifetime from the engine's own record, not from the finder's rebuild.
        delivered = max(pd.delivery_time for pd in assignment.package_deliveries)
        lifetime_s = (delivered - created[assignment.order_id]).total_seconds()

        row = rows[assignment.order_id]
        stage_s = sum(sum(row.stage_minutes(stage)) for stage in STAGES) * 60
        assert abs(stage_s - lifetime_s) < 1.0
        assert all(minutes >= 0 for stage in STAGES for minutes in row.stage_minutes(stage))


def test_same_seed_same_json(capsys):
    assert main(ACCEPTANCE_ARGS + ["--json"]) == 0
    first = capsys.readouterr().out
    assert main(ACCEPTANCE_ARGS + ["--json"]) == 0
    second = capsys.readouterr().out

    assert first == second
    report = json.loads(first)
    assert [r["label"] for r in report["runs"]] == ["baseline", "front_compressed"]


def test_cli_names_one_binding_stage_per_run(capsys):
    assert main(ACCEPTANCE_ARGS) == 0
    out = capsys.readouterr().out

    named = re.findall(r"^binding stage: (\S+)$", out, flags=re.MULTILINE)
    assert len(named) == 2
    assert all(stage in STAGES for stage in named)


def test_compress_arrivals_keeps_each_orders_lead_time():
    scenario, _ = generate_scenario(num_drivers=6, num_orders=40, seed=7)
    compressed = compress_arrivals(scenario, 2.0)
    start = scenario.current_time

    for before, after in zip(scenario.orders, compressed.orders):
        offset_error = (after.created_at - start) * 2 - (before.created_at - start)
        assert abs(offset_error) <= timedelta(microseconds=1)  # timedelta rounds to 1 us
        for p_before, p_after in zip(before.packages, after.packages):
            assert p_after.deadline - after.created_at == p_before.deadline - before.created_at
    assert compressed.drivers == scenario.drivers


def test_order_no_driver_can_carry_is_not_counted_as_queueing():
    scenario, _ = generate_scenario(num_drivers=6, num_orders=40, seed=7)
    order = next(
        o for o in scenario.orders
        if any(p.temp_regime in (TempRegime.FROZEN, TempRegime.CRYOGENIC) for p in o.packages)
    )
    ambient_only = scenario.model_copy(update={
        "drivers": [d.model_copy(update={"cold_storage": ColdStorage.NONE}) for d in scenario.drivers],
    })

    assert not any_driver_could_take(ambient_only, order)


def test_front_compression_moves_the_readme_example():
    report = run(drivers=10, orders=80, seed=7, front_speedup=2.0)

    before, after = (r["binding_stage"] for r in report["runs"])
    assert (before, after) == ("travel_to_pickup", "queue_for_assignment")
    assert report["bottleneck_moved"]
