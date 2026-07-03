import json
from pathlib import Path

from app.report import main


SCORECARD = Path(__file__).parent.parent / "reports" / "scorecard.jsonl"
EXPECTED_COLUMNS = {
    "cohort",
    "solver",
    "mean_assignment_rate",
    "mean_deadline_compliance_rate",
    "mean_driver_utilization_pct",
    "mean_total_distance_km",
    "delta_assignment_rate",
    "delta_deadline_compliance_rate",
    "delta_driver_utilization_pct",
    "delta_total_distance_km",
}


def _load_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in SCORECARD.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_scorecard_shape() -> None:
    main()

    assert len(_load_rows()) == 8


def test_scorecard_columns() -> None:
    main()

    for row in _load_rows():
        assert EXPECTED_COLUMNS <= set(row)


def test_scorecard_deltas_are_numbers() -> None:
    main()

    for row in _load_rows():
        for key, value in row.items():
            if key.startswith("delta_"):
                assert isinstance(value, (int, float))
                assert not isinstance(value, bool)


def test_scorecard_is_deterministic() -> None:
    main()
    first_rows = _load_rows()

    main()
    second_rows = _load_rows()

    assert first_rows == second_rows
