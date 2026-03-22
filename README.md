# DispatchIQ: Constrained Vehicle Routing Optimizer

An interactive simulator comparing manual (greedy) vs algorithmic dispatch for logistics operations with complex constraints — temperature requirements, equipment matching, time windows, and multi-destination routing.

Built as a technical prototype demonstrating that batch-optimal assignment dramatically outperforms sequential human decision-making in constrained vehicle routing problems.

## The Problem

A fleet of drivers with heterogeneous capabilities (vehicle types, temperature equipment, certifications) must be assigned to incoming orders. Each order originates at a pickup location and contains one or more packages — each potentially going to a **different destination** with its own handling requirements and deadline.

A human dispatcher processes orders sequentially: for each order, find the nearest eligible driver and assign them. Each individual decision is locally rational — send someone close with the right equipment. But the *sequence* of decisions is globally suboptimal:

- Assigning Driver A to Order X might leave no feasible option for the more urgent Order Y arriving 10 minutes later
- Two facilities 500m apart each get separate drivers when one trip could serve both
- Scarce high-capability equipment gets burned on standard-tier orders
- Multi-stop delivery routes aren't optimized — the driver visits destinations in arbitrary order

**This is a Vehicle Routing Problem with Pickup and Delivery (VRPPD)** — NP-hard in the general case. The optimization has three layers:

1. **Assignment** (who gets what): Match drivers to orders respecting hard constraints, minimizing total cost
2. **Pooling** (can we batch?): Group compatible nearby orders for a single driver
3. **Routing** (in what order): Optimize each driver's multi-stop delivery sequence (per-driver TSP)

### Typical Results

| Metric | Greedy | Optimal | Delta |
|--------|--------|---------|-------|
| Total Distance | 326 km | 246 km | **-24.6%** |
| Deadline Compliance | 58% | 92% | **+34pp** |

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000

## What It Does

1. **Generates realistic scenarios** — Facilities, drivers with varying capabilities, and orders with different urgency levels, temperature requirements, and multi-destination deliveries
2. **Runs a smart greedy baseline** — Models a competent human dispatcher who prioritizes by urgency, checks constraints, and scores by distance + deadline + equipment fit — but decides sequentially
3. **Runs optimal batch assignment** — Hungarian algorithm with constraint-aware cost matrix + per-driver route optimization (nearest-neighbor + 2-opt)
4. **Shows why greedy loses** — Order-by-order analysis with flagged problems: wasted distance, over-qualified equipment, missed deadlines, unassigned orders

## Constraints

Every assignment is checked against hard constraints (binary pass/fail):

| Constraint | Description |
|-----------|-------------|
| **Temperature equipment** | Driver's cold storage must support all cargo temperature regimes in the order |
| **Certifications** | Elevated-hazard items require hazmat certification |
| **Capacity** | Combined packages must fit in the vehicle (4 for bikes, 12 for cars, 20 for vans) |
| **Shift window** | Estimated completion must fall within driver's shift |
| **Deadline feasibility** | Using 80th-percentile pessimistic travel time, can all packages be delivered before their deadlines? |
| **Temperature compatibility** | Items that must not freeze cannot ride with frozen/cryo items |

## Algorithms

### Smart Greedy (Competent Dispatcher)
Not a straw man — represents what a good human does:
- Sorts orders by urgency, then deadline tightness within each tier
- Checks all hard constraints
- Scores drivers by: distance (primary) + deadline feasibility penalty + equipment match penalty
- Picks best driver per order, sequentially — no global view, no pooling, no route optimization

### Hungarian Optimal Assignment
Uses `scipy.optimize.linear_sum_assignment` with a multi-factor cost matrix:
- Distance to pickup x urgency multiplier (STAT=3x, Urgent=2x)
- Infinity for constraint violations
- Deadline risk penalty (proportional to negative slack)
- Over-qualification penalty (don't waste high-capability drivers on standard orders)
- Multi-destination delivery distance
- Per-driver route optimization via nearest-neighbor + 2-opt

### Pooling & Insertion
- **Order pooling**: Groups nearby (<2km) compatible orders by temperature and deadline slack
- **Slack-based holding**: STAT dispatches immediately; Routine can wait 15-30 min for a pooling match
- **Cheapest insertion**: Inserts new orders into active routes when detour cost is small

## Simulation Design

- **City**: Real NYC coordinates — 8 hubs, 12 branches, 6 destinations, 3 satellites
- **Distance**: Haversine x 1.4 road factor (~15% accuracy for NYC grid)
- **Travel time**: Stochastic — log-normal distribution (CV=0.20 normal, 0.35 rush hour). Deadline feasibility uses 80th percentile (conservative)
- **Stop buffers**: 5 min pickup, 3 min delivery
- **Order arrival**: Wave pattern — 40% morning rush, 25% midday, 25% afternoon, 10% late
- **Cargo mix**: 7 types across 4 temperature regimes (ambient through cryogenic)
- **Urgency**: STAT 15%, Urgent 25%, Routine 45%, Standard 15%
- **Drivers**: 60% car / 25% van / 15% bike; 40% ambient-only / 30% cooler / 20% fridge / 10% cryo

## Assumptions & Simplifications

| Assumption | What it means | Production upgrade |
|-----------|---------------|-------------------|
| Haversine x 1.4 distance | No real routing API; ~15% accuracy for NYC grid | OSRM / Google Maps / Mapbox |
| Constant base speed | 25 km/h cars, 22 vans, 18 bikes; no traffic model | Time-of-day speed profiles, live traffic |
| Stochastic variability | Log-normal travel time captures uncertainty | Learned distributions from historical GPS |
| Static scenario | All orders known at generation time | Dynamic arrival with insertion algorithm |
| No central depot | Drivers start at scattered positions | Return-to-base costing if applicable |
| Hard constraints only | No soft constraint relaxation | Weighted penalties for marginal violations |
| No driver breaks | Continuous availability within shift | Break scheduling, refueling |
| Generated data | Probability distributions, not historical | Learn from real operational data |

**Not included**: Real-time GPS, ML demand prediction, driver mobile app, multi-period planning, OR-Tools VRP solver.

## Architecture

```
app/
├── models.py                 # Pydantic domain models (Order → Package two-level structure)
├── constraints.py            # Hard constraint checker (6 constraint types)
├── comparison.py             # Side-by-side algorithm metrics
├── analysis.py               # Detailed greedy-vs-optimal breakdown
├── simulation/
│   ├── city.py               # NYC facility coordinates
│   ├── distance.py           # Haversine + stochastic travel time
│   ├── orders.py             # Order/package generation with cargo type distributions
│   └── drivers.py            # Driver fleet generation with capability distributions
├── algorithms/
│   ├── greedy.py             # Smart greedy baseline (sequential assignment)
│   ├── hungarian.py          # Optimal batch assignment (scipy linear_sum_assignment)
│   ├── pooling.py            # Order batching by proximity + temp compatibility + slack
│   ├── insertion.py          # Cheapest insertion into active routes
│   └── route_optimizer.py    # Per-driver TSP: nearest-neighbor + 2-opt
└── api/
    └── routes.py             # FastAPI REST endpoints

static/                       # Dashboard (Leaflet.js + Chart.js via CDN, zero build step)
```

## Technical Stack

| Component | Technology | Role |
|-----------|-----------|------|
| Backend | Python 3.11+ / FastAPI | REST API, scenario generation, algorithm execution |
| Solver | `scipy.optimize.linear_sum_assignment` | Hungarian algorithm for optimal bipartite matching |
| Numerics | NumPy | Distance matrices, cost computation, stochastic sampling |
| Data models | Pydantic v2 | Typed domain models with validation |
| Map | Leaflet.js (CDN) | Interactive route visualization, facility/driver markers |
| Charts | Chart.js (CDN) | Metric comparison bar/doughnut charts |
| Distance | Haversine formula × 1.4 road factor | Great-circle distance approximation for NYC grid |
| Route optimization | Nearest-neighbor + 2-opt | Per-driver TSP heuristic for multi-stop sequences |

**No external mapping API** (Google Maps, Mapbox, OSRM) — distance is computed geometrically. No commercial VRP solver (OR-Tools, OptaPlanner). No database — all state is in-memory per request. Zero frontend build step — HTML + JS served as static files via FastAPI.

## Dependencies

FastAPI, Uvicorn, scipy, numpy, pydantic. Frontend via CDN (Leaflet.js, Chart.js).

## Dashboard

Three tabs:
- **Map** — Interactive Leaflet map with facility markers, driver icons, route visualization (toggle greedy/optimal/both), click-for-detail popups
- **Analysis** — Order-by-order comparison with flagged problems, driver utilization table, summary metrics, root cause explanations
- **How It Works** — Plain-language walkthrough of the problem, domain model, algorithms, and assumptions
