"""Hard constraint checking for driver-order assignment feasibility.

Uses the route evaluator as the single source of truth for route timing
and deadline feasibility. No duplicate route-time logic here.
"""

from datetime import datetime

from app.models import (
    Driver, Order, Package, RouteStop, TempRegime, DriverStatus,
    COLD_STORAGE_CAPABILITIES,
)
from app.simulation.route_evaluator import (
    evaluate_route, build_stops, RouteEvaluation, TravelTimeMode,
)


def can_handle_temp(driver: Driver, order: Order) -> bool:
    """Check if driver's cold storage can handle ALL packages in the order."""
    supported = COLD_STORAGE_CAPABILITIES[driver.cold_storage]
    return all(p.temp_regime in supported for p in order.packages)


def can_handle_temp_package(driver: Driver, package: Package) -> bool:
    """Check if driver's cold storage can handle a specific package."""
    return package.temp_regime in COLD_STORAGE_CAPABILITIES[driver.cold_storage]


def has_required_certs(driver: Driver, order: Order) -> bool:
    """Check if driver has certifications required by the order."""
    if order.needs_dangerous_goods_cert and not driver.has_dangerous_goods_cert:
        return False
    return True


def has_capacity(driver: Driver, order: Order) -> bool:
    """Check if driver has enough remaining capacity for all packages."""
    return driver.remaining_capacity >= order.total_packages


def is_available(driver: Driver) -> bool:
    """Check if driver is available for new assignments."""
    return driver.status in (DriverStatus.AVAILABLE, DriverStatus.EN_ROUTE)


def are_temps_compatible(packages: list[Package]) -> bool:
    """Check if packages can coexist in the same vehicle.

    Key incompatibility: "must_not_freeze" specimens (cultures) can't ride with
    frozen/cryogenic specimens in the same cold compartment.
    """
    has_must_not_freeze = any("must_not_freeze" in p.special_handling for p in packages)
    has_frozen = any(p.temp_regime in (TempRegime.FROZEN, TempRegime.CRYOGENIC) for p in packages)

    if has_must_not_freeze and has_frozen:
        return False

    return True


def evaluate_assignment(
    driver: Driver,
    order: Order,
    stops: list[RouteStop],
    current_time: datetime,
    mode: TravelTimeMode = TravelTimeMode.CONSERVATIVE,
) -> RouteEvaluation:
    """Evaluate a candidate assignment using the route evaluator.

    This is the bridge between constraint checking and route evaluation.
    The caller provides the exact stop sequence the solver intends to execute.
    """
    return evaluate_route(driver, order, stops, current_time, mode)


def check_all_constraints(
    driver: Driver,
    order: Order,
    current_time: datetime,
    stops: list[RouteStop] | None = None,
    mode: TravelTimeMode = TravelTimeMode.CONSERVATIVE,
) -> tuple[bool, list[str]]:
    """Run all hard constraint checks. Returns (feasible, list_of_violations).

    Args:
        driver: Candidate driver.
        order: Order to assign.
        current_time: Dispatch time.
        stops: Optional explicit stop sequence. If None, uses raw destination
               order (pessimistic default for feasibility screening).
        mode: Travel time mode for route evaluation.

    Returns:
        (feasible, violations) — empty violations means feasible.
    """
    violations: list[str] = []

    if not is_available(driver):
        violations.append(f"Driver {driver.id} is not available (status: {driver.status})")

    if not can_handle_temp(driver, order):
        needed = order.required_temp_regimes
        supported = driver.supported_temp_regimes
        violations.append(
            f"Temp mismatch: order needs {needed}, driver supports {supported}"
        )

    if not has_required_certs(driver, order):
        violations.append("Driver lacks dangerous goods certification")

    if not has_capacity(driver, order):
        violations.append(
            f"Capacity: needs {order.total_packages}, driver has {driver.remaining_capacity} remaining"
        )

    # Early exit: if basic constraints fail, skip route evaluation
    if violations:
        return (False, violations)

    # Route-based constraints use the evaluator
    if stops is None:
        stops = build_stops(order)

    evaluation = evaluate_route(driver, order, stops, current_time, mode)

    if not evaluation.shift_feasible:
        violations.append(f"Delivery would exceed shift end ({driver.shift_end})")

    if not evaluation.all_deadlines_met:
        missed = evaluation.missed_package_ids
        violations.append(
            f"Cannot meet deadlines for packages: {missed} "
            f"(tightest: {order.tightest_deadline})"
        )

    return (len(violations) == 0, violations)
