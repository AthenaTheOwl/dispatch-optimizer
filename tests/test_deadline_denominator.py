"""On-time rates must count unassigned orders in the denominator."""

import json
from pathlib import Path

import pytest

from app.report import _build_solvers, main
from experiments.runner import run_experiment
from experiments.scenarios import generate_scenario_bank


SCORECARD = Path(__file__).parent.parent / "reports" / "scorecard.jsonl"


def _understaffed_runs():
    # 6 drivers for 20 orders leaves work unassigned on every solver.
    bank = generate_scenario_bank(count=1, num_drivers=6, num_orders=20, base_seed=200)
    return run_experiment(_build_solvers(), bank, save=False).runs


def test_on_time_rate_divides_by_all_orders() -> None:
    for run in _understaffed_runs():
        m = run.metrics
        assert m.orders_unassigned > 0
        assert m.total_orders == m.orders_assigned + m.orders_unassigned
        assert m.deadline_compliance_rate == pytest.approx(
            m.orders_meeting_deadline / m.total_orders * 100
        )
        assert m.orders_fully_on_time_rate == m.deadline_compliance_rate
        # an unassigned order cannot be on time
        assert m.deadline_compliance_rate <= m.assignment_rate
        assert m.deadline_compliance_rate < 100.0


def test_assigned_only_rate_is_kept_and_labelled() -> None:
    for run in _understaffed_runs():
        m = run.metrics
        assert m.assigned_deadline_compliance_rate == pytest.approx(
            m.orders_meeting_deadline / m.orders_assigned * 100
        )
        assert m.assigned_deadline_compliance_rate > m.deadline_compliance_rate


def test_package_rate_divides_by_all_packages() -> None:
    for run in _understaffed_runs():
        m = run.metrics
        assert m.total_packages > m.total_packages_checked
        assert m.package_deadline_compliance_rate == pytest.approx(
            m.packages_on_time / m.total_packages * 100
        )
        assert m.assigned_package_deadline_compliance_rate == pytest.approx(
            m.packages_on_time / m.total_packages_checked * 100
        )


def test_scorecard_on_time_rate_never_exceeds_assignment_rate() -> None:
    main()
    rows = [
        json.loads(line)
        for line in SCORECARD.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    for row in rows:
        assert row["mean_deadline_compliance_rate"] <= row["mean_assignment_rate"]
        assert "mean_assigned_deadline_compliance_rate" in row
    understaffed = [row for row in rows if row["cohort"] == "understaffed"]
    assert all(row["mean_deadline_compliance_rate"] < 100.0 for row in understaffed)
