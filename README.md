# DispatchIQ: Constrained Dispatch Optimizer

An interactive event-driven simulator for constrained logistics dispatch.

This portfolio repo compares:
- a sequential greedy dispatcher
- a Hungarian batch assignment solver

Both operate under the same information constraints: orders arrive over time, drivers become available over time, and route feasibility is checked against the actual stop sequence.

## What It Is

DispatchIQ models a fleet of drivers with heterogeneous capabilities:
- vehicle type
- temperature equipment
- certifications
- shift windows
- current load

Each order contains one or more packages, and each package can have:
- its own destination
- its own handling requirements
- its own deadline

That creates a constrained pickup-and-delivery problem with assignment, routing, and deadline risk all interacting.

## What It Does

- Generates realistic NYC scenarios with timed order arrivals
- Runs a greedy baseline that assigns sequentially
- Runs a Hungarian batch solver each dispatch epoch
- Uses a shared route evaluator for planning, execution, and metrics
- Produces package-level deadline compliance from evaluated routes
- Includes analysis views explaining where the greedy policy falls behind

## What It Does Not Do

These modules or ideas exist, but are not wired into active dispatch:
- pooling
- slack-based holding
- cheapest insertion into active routes

This is still a simplified simulator. It does not include:
- live GPS
- real road-network routing APIs
- OR-Tools or a full VRP solver
- learned travel times or demand models
- production persistence or mobile apps

## Integrity Notes

The repo is intentionally non-clairvoyant:
- orders are dispatched only after `created_at`
- driver state evolves through the simulation
- constraints use the same route evaluator used by execution
- deadline metrics come from evaluated package delivery times, not static proxies

## Architecture

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
    base.py
    constraints.py
    greedy.py
    hungarian.py
    route_optimizers.py
    cost_functions/
  algorithms/
    pooling.py
    insertion.py
    route_optimizer.py

experiments/
tests/
static/
```

`algorithms/pooling.py` and `algorithms/insertion.py` are exploratory helpers, not part of the live dispatch path.

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open `http://localhost:8000`.

## Verification

```bash
python -m pytest -q
```

The current freeze state includes integrity tests for:
- route evaluation
- event-driven dispatch stamping
- compare/analysis parity
- event-driven experiments
- multi-seed no-silent-miss sweeps

## Portfolio Positioning

This repo is a frozen portfolio piece. It is meant to demonstrate:
- modeling depth
- solver architecture
- experiment discipline
- honest handling of operational constraints

It is not presented as a full production dispatch platform.
