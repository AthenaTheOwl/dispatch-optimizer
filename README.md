<!-- ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ -->

# N° 01 · dispatchIQ

> *greedy vs. clever, with constraints that bite.*

an event-driven simulator for constrained logistics dispatch. orders show up in time. drivers come and go. routes have to actually work — not just on paper. two policies share the same world, the same information, the same rules; you watch one fall behind the other.

`python` · `fastapi` · `scipy.optimize` · `MIT` · 2024 · **status: running**

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload  # http://localhost:8000
```

<!-- ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ -->

## the two dispatchers

| | **greedy** | **hungarian batch** |
|---|---|---|
| picks            | one order at a time | a whole epoch at once |
| sees             | the next assignable pair | the full bipartite cost matrix |
| solves           | nothing — just sorts | min-cost assignment |
| scales gracefully | up to a point | yes |
| fails honestly   | very | also yes, but later |

both run on the same event clock, the same driver state, and the same route evaluator. neither one cheats.

## the constraints that bite

drivers are not interchangeable. they carry:

- vehicle type
- temperature equipment (frozen / chilled / ambient)
- certifications
- shift windows
- whatever's already in the truck

orders are not single drops. each order is one or more packages, and each package can have its own destination, handling rules, and deadline. that's pickup-and-delivery, with assignment and routing and deadline risk all wired together. the route evaluator is the referee.

## what the repo is honest about

what's wired into live dispatch:
- greedy sequential assignment
- hungarian batch assignment per epoch
- shared route evaluator (used for planning, execution, and metrics)
- package-level deadline compliance from evaluated routes
- analysis views explaining where greedy falls behind

what exists but is **not** in the live path (intentionally):
- pooling
- slack-based holding
- cheapest insertion into active routes

what isn't here at all:
- live GPS or real road-network APIs
- OR-Tools or a full VRP solver
- learned travel times or demand models
- production persistence, mobile apps

## integrity

the repo is intentionally non-clairvoyant.

- orders are dispatched only after `created_at`
- driver state evolves through the simulation, not jumped to
- constraints use the same route evaluator that execution uses
- deadline metrics come from evaluated package delivery times, not static proxies

if a metric looks too good, the test suite breaks first.

<!-- ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ -->

## the floorplan

```
app/
  models.py
  constraints.py
  analysis.py
  api/routes.py
  simulation/
    city.py             # NYC scenarios, timed arrivals
    distance.py
    drivers.py
    orders.py
    route_evaluator.py  # the referee
    engine.py           # the event clock
  solvers/
    greedy.py
    hungarian.py
    route_optimizers.py
    cost_functions/
  algorithms/           # exploratory; not on the live path
    pooling.py
    insertion.py
    route_optimizer.py

experiments/
tests/
static/
```

## verification

```bash
python -m pytest -q
```

the freeze-state tests cover route evaluation, event-driven dispatch stamping, compare/analysis parity, event-driven experiments, and multi-seed no-silent-miss sweeps.

<!-- ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ -->

## live demo

Deploy with Streamlit Cloud using:

```text
streamlit_app.py
```

Local run:

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The Streamlit page runs the same event-driven comparison as the test suite:
greedy sequential assignment and Hungarian batch assignment over identical
scenario seeds.

## connects to

- `world-food-program-robust-simulator` for network allocation before last-mile dispatch.
- `Robust-Facility-Location` for facility placement before route execution.
- `proof-gate-runner` for packaging the simulation checks into reusable CI gates.

## colophon

a frozen portfolio piece. the point: modeling depth, solver architecture, experiment discipline, and honest handling of operational constraints. not a production dispatch platform. not pretending to be.

`MIT` license. *built downstairs.* — [the basement, room 7](https://github.com/AthenaTheOwl)
