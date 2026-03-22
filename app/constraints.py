"""Hard constraint checking for driver-order assignment feasibility."""

from datetime import datetime
from app.models import (
    Driver, Order, Package, TempRegime, DriverStatus,
    COLD_STORAGE_CAPABILITIES,
)
from app.simulation.distance import pessimistic_travel_time, PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES


def can_handle_temp(driver: Driver, order: Order) -> bool:
    """Check if driver's cold storage can handle ALL packages in the order."""
    supported = COLD_STORAGE_CAPABILITIES[driver.cold_storage]
    return all(p.temp_regime in supported for p in order.packages)


def can_handle_temp_package(driver: Driver, package: Package) -> bool:
    """Check if driver's cold storage can handle a specific package."""
    return package.temp_regime in COLD_STORAGE_CAPABILITIES[driver.cold_storage]


def has_required_certs(driver: Driver, order: Order) -> bool:
    """Check if driver has certifications required by the order."""
    if order.needs_hazmat_cert and not driver.has_hazmat_cert:
        return False
    return True


def has_capacity(driver: Driver, order: Order) -> bool:
    """Check if driver has enough remaining capacity for all packages."""
    return driver.remaining_capacity >= order.total_packages


def is_available(driver: Driver) -> bool:
    """Check if driver is available for new assignments."""
    return driver.status in (DriverStatus.AVAILABLE, DriverStatus.EN_ROUTE)


def can_meet_shift(driver: Driver, order: Order, current_time: datetime) -> bool:
    """Check if driver can complete the delivery before shift end."""
    # Estimate total time: travel to pickup + buffer + travel to furthest delivery + buffer
    pickup_travel = pessimistic_travel_time(
        driver.current_location, order.pickup_location, driver.speed_kmh
    )

    # Find the furthest delivery destination
    max_delivery_travel = 0.0
    for dest in order.unique_destinations:
        t = pessimistic_travel_time(order.pickup_location, dest, driver.speed_kmh)
        max_delivery_travel = max(max_delivery_travel, t)

    total_minutes = (
        pickup_travel
        + PICKUP_BUFFER_MINUTES
        + max_delivery_travel * len(order.unique_destinations)  # Rough multi-stop estimate
        + DELIVERY_BUFFER_MINUTES * len(order.unique_destinations)
    )

    estimated_end = current_time + __import__("datetime").timedelta(minutes=total_minutes)
    return estimated_end <= driver.shift_end


def can_meet_deadline(driver: Driver, order: Order, current_time: datetime) -> bool:
    """Check if driver can deliver all packages before their deadlines (pessimistic estimate)."""
    pickup_travel = pessimistic_travel_time(
        driver.current_location, order.pickup_location, driver.speed_kmh
    )

    for package in order.packages:
        delivery_travel = pessimistic_travel_time(
            order.pickup_location, package.destination, driver.speed_kmh
        )
        total = pickup_travel + PICKUP_BUFFER_MINUTES + delivery_travel + DELIVERY_BUFFER_MINUTES
        from datetime import timedelta
        estimated_delivery = current_time + timedelta(minutes=total)
        if estimated_delivery > package.deadline:
            return False

    return True


def are_temps_compatible(packages: list[Package]) -> bool:
    """
    Check if packages can coexist in the same vehicle.

    Temperature-sensitive items can't ride with frozen items
    in the same cold compartment.
    """
    has_must_not_freeze = any("must_not_freeze" in p.special_handling for p in packages)
    has_frozen = any(p.temp_regime in (TempRegime.FROZEN, TempRegime.CRYOGENIC) for p in packages)

    if has_must_not_freeze and has_frozen:
        return False

    return True


def check_all_constraints(
    driver: Driver,
    order: Order,
    current_time: datetime,
) -> tuple[bool, list[str]]:
    """
    Run all hard constraint checks. Returns (feasible, list_of_violations).
    Empty violations list means the assignment is feasible.
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
        violations.append(f"Driver lacks hazmat certification")

    if not has_capacity(driver, order):
        violations.append(
            f"Capacity: needs {order.total_packages}, driver has {driver.remaining_capacity} remaining"
        )

    if not can_meet_shift(driver, order, current_time):
        violations.append(f"Delivery would exceed shift end ({driver.shift_end})")

    if not can_meet_deadline(driver, order, current_time):
        violations.append(f"Cannot meet tightest deadline ({order.tightest_deadline})")

    return (len(violations) == 0, violations)
