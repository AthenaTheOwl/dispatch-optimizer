"""Comprehensive integrity tests for the dispatch system.

Covers:
- Order generation counts
- Route evaluator correctness
- Simulation stamping (dispatched_at)
- Greedy planning/execution consistency
- Hungarian planning/execution consistency
- Compare and analysis parity
- Experiments use event-driven simulation
- Regression tests on seeds 42, 100, 105
- No silent deadline misses in multi-seed sweep
"""

import pytest
from datetime import datetime, timedelta

from app.models import (
    Location, Driver, Order, Package, RouteStop, Scenario,
    CargoType, TempRegime, Urgency, HazardClass, VehicleType,
    ColdStorage, Certification, DriverStatus, StopType, PickupType,
    FacilityType, PackageDeliveryInfo,
)
from app.simulation.orders import generate_orders
from app.simulation.drivers import generate_drivers
from app.simulation.distance import road_distance_km, travel_time_minutes, pessimistic_travel_time
from app.simulation.route_evaluator import (
    evaluate_route, build_stops, TravelTimeMode, RouteEvaluation,
)
from app.simulation.engine import run_simulation
from app.simulation.city import ALL_FACILITIES
from app.constraints import check_all_constraints
from app.solvers.base import SolverRegistry
from app.solvers.constraints import DefaultConstraintChecker
from experiments.scenarios import generate_scenario, generate_scenario_bank, scenario_hash
from experiments.metrics import compute_experiment_metrics
from experiments.runner import run_experiment


# --- Fixtures ---

@pytest.fixture
def constraint_checker():
    return DefaultConstraintChecker()


@pytest.fixture
def base_time():
    return datetime(2024, 3, 15, 8, 0, 0)


@pytest.fixture
def simple_scenario(base_time):
    """A small, deterministic scenario for unit testing."""
    lab = Location(lat=40.75, lng=-73.98, name="Test Lab", facility_type=FacilityType.DESTINATION)
    clinic = Location(lat=40.76, lng=-73.97, name="Test Clinic", facility_type=FacilityType.BRANCH)
    driver_loc = Location(lat=40.755, lng=-73.975, name="Driver Start")

    package = Package(
        id="PKG-001",
        cargo_type=CargoType.STANDARD,
        temp_regime=TempRegime.AMBIENT,
        destination=lab,
        deadline=base_time + timedelta(hours=4),
    )
    order = Order(
        id="ORD-001",
        pickup_location=clinic,
        packages=[package],
        urgency=Urgency.ROUTINE,
        created_at=base_time,
    )
    driver = Driver(
        id="DRV-001",
        name="Test Driver",
        current_location=driver_loc,
        vehicle_type=VehicleType.CAR,
        cold_storage=ColdStorage.COOLER,
        certifications=[Certification.BASIC],
        shift_start=base_time - timedelta(hours=1),
        shift_end=base_time + timedelta(hours=10),
    )
    return Scenario(
        drivers=[driver],
        orders=[order],
        facilities=[lab, clinic],
        current_time=base_time,
    )


@pytest.fixture
def multi_stop_scenario(base_time):
    """Scenario with an order that has multiple packages going to different labs."""
    lab_a = Location(lat=40.75, lng=-73.98, name="Lab A", facility_type=FacilityType.DESTINATION)
    lab_b = Location(lat=40.72, lng=-73.99, name="Lab B", facility_type=FacilityType.DESTINATION)
    lab_c = Location(lat=40.78, lng=-73.96, name="Lab C", facility_type=FacilityType.DESTINATION)
    clinic = Location(lat=40.76, lng=-73.97, name="Clinic", facility_type=FacilityType.BRANCH)
    driver_loc = Location(lat=40.755, lng=-73.975, name="Driver Start")

    packages = [
        Package(
            id="PKG-A",
            cargo_type=CargoType.STANDARD,
            temp_regime=TempRegime.AMBIENT,
            destination=lab_a,
            deadline=base_time + timedelta(hours=4),
        ),
        Package(
            id="PKG-B",
            cargo_type=CargoType.BULK,
            temp_regime=TempRegime.AMBIENT,
            destination=lab_b,
            deadline=base_time + timedelta(hours=4),
        ),
        Package(
            id="PKG-C",
            cargo_type=CargoType.FRAGILE,
            temp_regime=TempRegime.REFRIGERATED,
            destination=lab_c,
            deadline=base_time + timedelta(hours=6),
        ),
    ]
    order = Order(
        id="ORD-MULTI",
        pickup_location=clinic,
        packages=packages,
        urgency=Urgency.ROUTINE,
        created_at=base_time,
    )
    driver = Driver(
        id="DRV-001",
        name="Test Driver",
        current_location=driver_loc,
        vehicle_type=VehicleType.CAR,
        cold_storage=ColdStorage.ACTIVE_FRIDGE,
        certifications=[Certification.BASIC, Certification.COLD_CHAIN],
        shift_start=base_time - timedelta(hours=1),
        shift_end=base_time + timedelta(hours=10),
    )
    return Scenario(
        drivers=[driver],
        orders=[order],
        facilities=[lab_a, lab_b, lab_c, clinic],
        current_time=base_time,
    )


# --- Test: Order Generation Counts ---

class TestOrderGeneration:
    """Exact order counts from generate_orders."""

    @pytest.mark.parametrize("count", [6, 10, 15, 20, 25, 30, 35])
    def test_exact_count(self, count, base_time):
        orders = generate_orders(count, base_time, seed=42)
        assert len(orders) == count, f"Expected {count} orders, got {len(orders)}"

    def test_deterministic_seed(self, base_time):
        orders_a = generate_orders(20, base_time, seed=42)
        orders_b = generate_orders(20, base_time, seed=42)
        assert [o.id for o in orders_a] == [o.id for o in orders_b]
        assert [o.urgency for o in orders_a] == [o.urgency for o in orders_b]

    def test_different_seeds_differ(self, base_time):
        orders_a = generate_orders(20, base_time, seed=42)
        orders_b = generate_orders(20, base_time, seed=99)
        # At least some urgencies should differ
        urgencies_a = [o.urgency for o in orders_a]
        urgencies_b = [o.urgency for o in orders_b]
        assert urgencies_a != urgencies_b


# --- Test: Route Evaluator ---

class TestRouteEvaluator:
    """Route evaluator computes correct package ETAs on multi-stop routes."""

    def test_single_stop_route(self, simple_scenario, base_time):
        order = simple_scenario.orders[0]
        driver = simple_scenario.drivers[0]
        stops = build_stops(order)

        evaluation = evaluate_route(
            driver, order, stops, base_time, TravelTimeMode.EXPECTED,
        )

        assert evaluation.total_distance_km > 0
        assert evaluation.total_time_min > 0
        assert len(evaluation.stop_etas) == 2  # 1 pickup + 1 delivery
        assert evaluation.stop_etas[0].stop_type == StopType.PICKUP
        assert evaluation.stop_etas[1].stop_type == StopType.DELIVERY
        assert evaluation.all_deadlines_met
        assert evaluation.shift_feasible
        assert len(evaluation.package_results) == 1
        assert evaluation.package_results[0].on_time

    def test_multi_stop_etas_increase(self, multi_stop_scenario, base_time):
        order = multi_stop_scenario.orders[0]
        driver = multi_stop_scenario.drivers[0]
        stops = build_stops(order)

        evaluation = evaluate_route(
            driver, order, stops, base_time, TravelTimeMode.EXPECTED,
        )

        # 1 pickup + 3 deliveries
        assert len(evaluation.stop_etas) == 4
        # Arrival times should be strictly increasing
        for i in range(1, len(evaluation.stop_etas)):
            assert evaluation.stop_etas[i].arrival_time > evaluation.stop_etas[i-1].arrival_time

        # All packages should have delivery results
        assert len(evaluation.package_results) == 3

    def test_conservative_slower_than_expected(self, simple_scenario, base_time):
        order = simple_scenario.orders[0]
        driver = simple_scenario.drivers[0]
        stops = build_stops(order)

        conservative = evaluate_route(
            driver, order, stops, base_time, TravelTimeMode.CONSERVATIVE,
        )
        expected = evaluate_route(
            driver, order, stops, base_time, TravelTimeMode.EXPECTED,
        )

        assert conservative.total_time_min >= expected.total_time_min

    def test_missed_deadline_detected(self, base_time):
        lab = Location(lat=40.75, lng=-73.98, name="Far Lab")
        clinic = Location(lat=40.90, lng=-73.80, name="Far Clinic")  # Far away
        driver_loc = Location(lat=40.70, lng=-74.00, name="Far Start")

        package = Package(
            id="PKG-TIGHT",
            cargo_type=CargoType.TIME_CRITICAL,
            temp_regime=TempRegime.AMBIENT,
            destination=lab,
            deadline=base_time + timedelta(minutes=10),  # Very tight
        )
        order = Order(
            id="ORD-TIGHT",
            pickup_location=clinic,
            packages=[package],
            urgency=Urgency.STAT,
            created_at=base_time,
        )
        driver = Driver(
            id="DRV-SLOW",
            name="Slow Driver",
            current_location=driver_loc,
            vehicle_type=VehicleType.VAN,
            cold_storage=ColdStorage.NONE,
            certifications=[Certification.BASIC],
            shift_start=base_time - timedelta(hours=1),
            shift_end=base_time + timedelta(hours=10),
        )

        stops = build_stops(order)
        evaluation = evaluate_route(
            driver, order, stops, base_time, TravelTimeMode.EXPECTED,
        )

        assert not evaluation.all_deadlines_met
        assert len(evaluation.missed_package_ids) == 1
        assert evaluation.missed_package_ids[0] == "PKG-TIGHT"


# --- Test: Simulation Stamping ---

class TestSimulationStamping:
    """dispatched_at is stamped by simulation."""

    def test_dispatched_at_stamped(self, constraint_checker):
        scenario, _ = generate_scenario(seed=42)
        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )
        sim = run_simulation(solver, scenario)

        for assignment in sim.assignments:
            assert assignment.dispatched_at is not None, \
                f"Assignment {assignment.order_id} missing dispatched_at"
            assert assignment.dispatched_at >= scenario.current_time

    def test_execution_feasible_set(self, constraint_checker):
        scenario, _ = generate_scenario(seed=42)
        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )
        sim = run_simulation(solver, scenario)

        for assignment in sim.assignments:
            assert assignment.execution_feasible is True, \
                f"Assignment {assignment.order_id} not marked execution_feasible"

    def test_package_deliveries_populated(self, constraint_checker):
        scenario, _ = generate_scenario(seed=42)
        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )
        sim = run_simulation(solver, scenario)

        for assignment in sim.assignments:
            assert len(assignment.package_deliveries) > 0, \
                f"Assignment {assignment.order_id} has no package_deliveries"


# --- Test: Greedy Planning/Execution Consistency ---

class TestGreedyConsistency:
    """Greedy solver uses the same route for planning and execution."""

    @pytest.mark.parametrize("seed", [42, 100, 105])
    def test_greedy_no_deadline_misses(self, seed, constraint_checker):
        scenario, _ = generate_scenario(seed=seed)
        solver = SolverRegistry.get_solver(
            "greedy", "greedy_scorer", constraint_checker, "none",
        )
        sim = run_simulation(solver, scenario)

        for a in sim.assignments:
            for pd in a.package_deliveries:
                assert pd.on_time, \
                    f"Greedy: seed={seed}, order={a.order_id}, pkg={pd.package_id} missed deadline"

    def test_greedy_validation_zero_rejections(self, constraint_checker):
        scenario, _ = generate_scenario(seed=42)
        solver = SolverRegistry.get_solver(
            "greedy", "greedy_scorer", constraint_checker, "none",
        )
        sim = run_simulation(solver, scenario)
        assert sim.validation_rejections == 0


# --- Test: Hungarian Planning/Execution Consistency ---

class TestHungarianConsistency:
    """Hungarian solver uses the same route for planning and execution."""

    @pytest.mark.parametrize("seed", [42, 100, 105])
    def test_hungarian_no_deadline_misses(self, seed, constraint_checker):
        scenario, _ = generate_scenario(seed=seed)
        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )
        sim = run_simulation(solver, scenario)

        for a in sim.assignments:
            for pd in a.package_deliveries:
                assert pd.on_time, \
                    f"Hungarian: seed={seed}, order={a.order_id}, pkg={pd.package_id} missed deadline"

    def test_hungarian_validation_zero_rejections(self, constraint_checker):
        scenario, _ = generate_scenario(seed=42)
        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )
        sim = run_simulation(solver, scenario)
        assert sim.validation_rejections == 0


# --- Test: Compare and Analysis Parity ---

class TestCompareAnalysisParity:
    """Compare and analysis must agree on the same scenario."""

    def test_compare_analysis_same_seed(self, constraint_checker):
        """Both endpoints produce consistent metrics for the same scenario."""
        from app.analysis import generate_analysis

        scenario, _ = generate_scenario(seed=42)

        # Run compare path
        g_solver = SolverRegistry.get_solver(
            "greedy", "greedy_scorer", constraint_checker, "none",
        )
        h_solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )
        g_sim = run_simulation(g_solver, scenario)
        h_sim = run_simulation(h_solver, scenario)
        g_metrics = compute_experiment_metrics(g_sim.to_dispatch_result(), scenario)
        h_metrics = compute_experiment_metrics(h_sim.to_dispatch_result(), scenario)

        # Run analysis path
        analysis = generate_analysis(scenario)

        # Distance delta should match
        compare_delta = (1 - h_metrics.total_distance_km / g_metrics.total_distance_km) * 100
        analysis_delta = analysis["summary"]["distance_pct_improvement"]
        assert abs(compare_delta - analysis_delta) < 0.2, \
            f"Compare delta {compare_delta:.1f}% != analysis delta {analysis_delta:.1f}%"

        # Assignment counts should match exactly
        assert analysis["summary"]["greedy_unassigned"] == len(g_sim.unassigned_orders), \
            f"Greedy unassigned: analysis={analysis['summary']['greedy_unassigned']} != compare={len(g_sim.unassigned_orders)}"
        assert analysis["summary"]["optimal_unassigned"] == len(h_sim.unassigned_orders), \
            f"Optimal unassigned: analysis={analysis['summary']['optimal_unassigned']} != compare={len(h_sim.unassigned_orders)}"

        # Assignment rates should match
        compare_g_rate = g_metrics.assignment_rate
        analysis_g_rate = analysis["summary"]["greedy_assignment_rate"]
        assert abs(compare_g_rate - analysis_g_rate) < 0.1, \
            f"Greedy assignment rate: compare={compare_g_rate:.1f}% != analysis={analysis_g_rate:.1f}%"

        compare_h_rate = h_metrics.assignment_rate
        analysis_h_rate = analysis["summary"]["optimal_assignment_rate"]
        assert abs(compare_h_rate - analysis_h_rate) < 0.1, \
            f"Hungarian assignment rate: compare={compare_h_rate:.1f}% != analysis={analysis_h_rate:.1f}%"

        # Deadline compliance should match — both solvers
        compare_g_deadline = g_metrics.deadline_compliance_rate
        analysis_g_deadline = analysis["summary"]["greedy_deadline_pct"]
        assert abs(compare_g_deadline - analysis_g_deadline) < 0.1, \
            f"Greedy deadline: compare={compare_g_deadline:.1f}% != analysis={analysis_g_deadline:.1f}%"

        compare_h_deadline = h_metrics.deadline_compliance_rate
        analysis_h_deadline = analysis["summary"]["optimal_deadline_pct"]
        assert abs(compare_h_deadline - analysis_h_deadline) < 0.1, \
            f"Hungarian deadline: compare={compare_h_deadline:.1f}% != analysis={analysis_h_deadline:.1f}%"

        # Package-level deadline compliance should match — both solvers
        compare_g_pkg = g_metrics.package_deadline_compliance_rate
        analysis_g_pkg = analysis["summary"]["greedy_pkg_deadline_pct"]
        assert abs(compare_g_pkg - analysis_g_pkg) < 0.1, \
            f"Greedy pkg deadline: compare={compare_g_pkg:.1f}% != analysis={analysis_g_pkg:.1f}%"

        compare_h_pkg = h_metrics.package_deadline_compliance_rate
        analysis_h_pkg = analysis["summary"]["optimal_pkg_deadline_pct"]
        assert abs(compare_h_pkg - analysis_h_pkg) < 0.1, \
            f"Hungarian pkg deadline: compare={compare_h_pkg:.1f}% != analysis={analysis_h_pkg:.1f}%"


# --- Test: Experiments Use Event-Driven Simulation ---

class TestExperimentsEventDriven:
    """Experiments run through run_simulation, not solver.solve() directly."""

    def test_experiment_uses_simulation(self, constraint_checker):
        g = SolverRegistry.get_solver(
            "greedy", "greedy_scorer", constraint_checker, "none",
        )
        h = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )

        bank = generate_scenario_bank(count=2, base_seed=42)
        result = run_experiment([g, h], bank, save=False)

        # All runs should have dispatch_epochs > 0 (proof of event-driven)
        for run in result.runs:
            assert run.dispatch_epochs > 0, \
                f"Run {run.run_id} has 0 dispatch_epochs — not event-driven"

        # Summary should record simulation engine
        assert result.summary["simulation"]["engine"] == "event_driven"

    def test_experiment_separates_configs(self, constraint_checker):
        """Different solver configs don't merge in summary."""
        g = SolverRegistry.get_solver(
            "greedy", "greedy_scorer", constraint_checker, "none",
        )
        h = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )

        bank = generate_scenario_bank(count=2, base_seed=42)
        result = run_experiment([g, h], bank, save=False)

        assert len(result.summary["solvers"]) == 2
        labels = list(result.summary["solvers"].keys())
        assert "greedy" in labels[0]
        assert "hungarian" in labels[1]


# --- Test: Regression on Known Seeds ---

class TestSeedRegression:
    """Regression tests around seeds 42, 100, and 105."""

    @pytest.mark.parametrize("seed", [42, 100, 105])
    def test_deterministic_results(self, seed, constraint_checker):
        """Same seed produces same distance and assignment count."""
        scenario_a, _ = generate_scenario(seed=seed)
        scenario_b, _ = generate_scenario(seed=seed)

        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )

        sim_a = run_simulation(solver, scenario_a)
        sim_b = run_simulation(solver, scenario_b)

        assert abs(sim_a.total_distance_km - sim_b.total_distance_km) < 0.01, \
            f"seed={seed}: non-deterministic distance {sim_a.total_distance_km} vs {sim_b.total_distance_km}"
        assert len(sim_a.assignments) == len(sim_b.assignments)

    @pytest.mark.parametrize("seed", [42, 100, 105])
    def test_hungarian_beats_greedy(self, seed, constraint_checker):
        """Hungarian should have <= distance than greedy on known seeds."""
        scenario, _ = generate_scenario(seed=seed)

        g = SolverRegistry.get_solver(
            "greedy", "greedy_scorer", constraint_checker, "none",
        )
        h = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )

        g_sim = run_simulation(g, scenario)
        h_sim = run_simulation(h, scenario)

        assert h_sim.total_distance_km <= g_sim.total_distance_km * 1.01, \
            f"seed={seed}: Hungarian ({h_sim.total_distance_km:.1f}) worse than greedy ({g_sim.total_distance_km:.1f})"


# --- Test: No Silent Deadline Misses in Multi-Seed Sweep ---

class TestNoSilentDeadlineMisses:
    """In a deterministic multi-seed sweep, no assignments silently miss deadlines."""

    def test_100_seed_sweep_no_silent_misses(self, constraint_checker):
        """Run 100 seeds, check every package delivery for deadline compliance."""
        solver = SolverRegistry.get_solver(
            "hungarian", "composite", constraint_checker, "nn_2opt",
        )

        total_assignments = 0
        total_packages = 0
        missed = []

        for seed in range(42, 142):
            scenario, _ = generate_scenario(seed=seed)
            sim = run_simulation(solver, scenario)

            for a in sim.assignments:
                total_assignments += 1
                for pd in a.package_deliveries:
                    total_packages += 1
                    if not pd.on_time:
                        missed.append({
                            "seed": seed,
                            "order": a.order_id,
                            "package": pd.package_id,
                            "slack_min": pd.slack_min,
                        })

        assert len(missed) == 0, \
            f"Found {len(missed)} silent deadline misses in 100-seed sweep:\n" + \
            "\n".join(f"  seed={m['seed']} order={m['order']} pkg={m['package']} slack={m['slack_min']:.1f}min" for m in missed[:10])

        # Sanity: we actually checked a meaningful number
        assert total_assignments > 500, f"Only {total_assignments} assignments in 100 seeds — too few"
        assert total_packages > 800, f"Only {total_packages} packages in 100 seeds — too few"


# --- Test: Constraint Checker Uses Route Evaluator ---

class TestConstraintCheckerIntegration:
    """Constraint checker delegates to route evaluator."""

    def test_feasible_with_explicit_stops(self, simple_scenario, base_time):
        order = simple_scenario.orders[0]
        driver = simple_scenario.drivers[0]
        stops = build_stops(order)

        feasible, violations = check_all_constraints(
            driver, order, base_time, stops=stops,
        )
        assert feasible
        assert len(violations) == 0

    def test_infeasible_deadline(self, base_time):
        lab = Location(lat=40.90, lng=-73.80, name="Far Lab")
        clinic = Location(lat=40.70, lng=-74.00, name="Far Clinic")
        driver_loc = Location(lat=40.60, lng=-74.10, name="Very Far")

        package = Package(
            id="PKG-X",
            cargo_type=CargoType.TIME_CRITICAL,
            temp_regime=TempRegime.AMBIENT,
            destination=lab,
            deadline=base_time + timedelta(minutes=5),  # Impossible
        )
        order = Order(
            id="ORD-X",
            pickup_location=clinic,
            packages=[package],
            urgency=Urgency.STAT,
            created_at=base_time,
        )
        driver = Driver(
            id="DRV-X",
            name="Test",
            current_location=driver_loc,
            vehicle_type=VehicleType.VAN,
            cold_storage=ColdStorage.NONE,
            certifications=[Certification.BASIC],
            shift_start=base_time - timedelta(hours=1),
            shift_end=base_time + timedelta(hours=10),
        )

        feasible, violations = check_all_constraints(driver, order, base_time)
        assert not feasible
        assert any("deadline" in v.lower() for v in violations)


# --- Test: Scenario Hash ---

class TestScenarioHash:
    """Scenario hashing includes content, not just IDs."""

    def test_same_seed_same_hash(self):
        s1, _ = generate_scenario(seed=42)
        s2, _ = generate_scenario(seed=42)
        assert scenario_hash(s1) == scenario_hash(s2)

    def test_different_seed_different_hash(self):
        s1, _ = generate_scenario(seed=42)
        s2, _ = generate_scenario(seed=99)
        assert scenario_hash(s1) != scenario_hash(s2)
