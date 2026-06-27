# Dispatch optimizer

Orders arrive in time. Drivers go on shift. Frozen packages do not ride in ambient vans because the spreadsheet felt optimistic. Two dispatchers see the same city and one bleeds delay faster.

## What it does

This repo is an event-driven simulator for constrained logistics dispatch. Orders appear over time, drivers carry different capabilities, and the route evaluator decides whether an assignment can actually be served.

Two policies run on the same world:

| Policy | What it sees | What it solves |
|---|---|---|
| Greedy | The next assignable pair | Sorts feasible options |
| Hungarian batch | A full epoch's cost matrix | Minimum-cost assignment |

Neither policy sees future orders. The useful question is where the simple dispatcher starts losing ground.

## Run it

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload  # http://localhost:8000
```

## Constraints that matter

Drivers carry:

- vehicle type
- temperature equipment
- certifications
- shift windows
- current load

Orders can contain multiple packages, each with destination, handling rules, and deadline. The same route evaluator is used for planning, execution, and metrics, so a pretty assignment cannot hide a broken route.

## Verification

```bash
python -m pytest -q
```

The tests cover route evaluation, event-driven dispatch stamping, comparison parity, experiment sweeps, and multi-seed no-silent-miss cases.

## Live demo

Deploy with Streamlit Cloud using:

```text
streamlit_app.py
```

Local run:

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The Streamlit page runs the same event-driven comparison as the test suite: greedy sequential assignment and Hungarian batch assignment over identical scenario seeds.

## Floorplan

```text
app/
  models.py
  constraints.py
  analysis.py
  api/routes.py
  simulation/
    city.py
    distance.py
    drivers.py
    orders.py
    route_evaluator.py
    engine.py
  solvers/
    greedy.py
    hungarian.py
    route_optimizers.py
    cost_functions/
  algorithms/
experiments/
tests/
static/
```

## Connects to

- `world-food-program-robust-simulator` for network allocation before last-mile dispatch.
- `Robust-Facility-Location` for facility placement before route execution.
- `proof-gate-runner` for packaging simulation checks into reusable CI gates.

## What's intentionally absent

- Live GPS or road-network APIs.
- OR-Tools or a full VRP solver.
- Learned travel times or demand models.
- Production persistence or mobile apps.

## License

MIT.
