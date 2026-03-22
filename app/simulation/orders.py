"""Generate realistic dispatch orders with multi-destination packages."""

import random
from datetime import datetime, timedelta
from app.models import (
    Order, Package, Location, CargoType, TempRegime, Urgency,
    HazardClass, PickupType,
    CARGO_TEMP_REQUIREMENTS, URGENCY_DEADLINE_MINUTES, CARGO_MAX_MINUTES,
)
from app.simulation.city import PICKUP_LOCATIONS, DESTINATIONS


# Cargo type distribution weights
CARGO_WEIGHTS: dict[CargoType, float] = {
    CargoType.STANDARD: 0.40,
    CargoType.TIME_CRITICAL: 0.05,
    CargoType.BULK: 0.20,
    CargoType.FRAGILE: 0.10,
    CargoType.SENSITIVE: 0.10,
    CargoType.COLD_CHAIN: 0.10,
    CargoType.ULTRA_COLD: 0.05,
}

# Urgency distribution weights
URGENCY_WEIGHTS: dict[Urgency, float] = {
    Urgency.STAT: 0.15,
    Urgency.URGENT: 0.25,
    Urgency.ROUTINE: 0.45,
    Urgency.STANDARD: 0.15,
}

# How many packages per order (weighted distribution)
# Most orders have 1-2 packages, some have 3, rarely 4+
PACKAGES_PER_ORDER_WEIGHTS = {1: 0.40, 2: 0.30, 3: 0.20, 4: 0.10}


def _weighted_choice(weights: dict) -> object:
    """Pick a random key from a dict of {key: weight}."""
    items = list(weights.keys())
    probs = list(weights.values())
    total = sum(probs)
    probs = [p / total for p in probs]
    return random.choices(items, weights=probs, k=1)[0]


def _random_cargo() -> CargoType:
    return _weighted_choice(CARGO_WEIGHTS)


def _random_urgency() -> Urgency:
    return _weighted_choice(URGENCY_WEIGHTS)


def _pick_destination(exclude: set[str] | None = None) -> Location:
    """Pick a random destination, avoiding duplicates within the same order if possible."""
    available = [dest for dest in DESTINATIONS if exclude is None or dest.name not in exclude]
    if not available:
        available = DESTINATIONS
    return random.choice(available)


def generate_package(
    package_id: str,
    urgency: Urgency,
    created_at: datetime,
    used_destinations: set[str],
) -> Package:
    """Generate a single package with realistic cargo type and destination."""
    cargo = _random_cargo()
    temp_regime = CARGO_TEMP_REQUIREMENTS[cargo]

    # Deadline = min(urgency deadline, cargo-specific max)
    urgency_deadline = created_at + timedelta(minutes=URGENCY_DEADLINE_MINUTES[urgency])
    cargo_deadline = created_at + timedelta(minutes=CARGO_MAX_MINUTES[cargo])
    deadline = min(urgency_deadline, cargo_deadline)

    # Elevated hazard is rare (~5% of items)
    hazard = HazardClass.ELEVATED if random.random() < 0.05 else HazardClass.STANDARD

    destination = _pick_destination(exclude=used_destinations)
    used_destinations.add(destination.name)

    special = []
    if cargo == CargoType.FRAGILE:
        if random.random() < 0.3:
            special.append("fragile")
    if cargo == CargoType.SENSITIVE:
        special.append("must_not_freeze")

    return Package(
        id=package_id,
        cargo_type=cargo,
        temp_regime=temp_regime,
        destination=destination,
        hazard_class=hazard,
        deadline=deadline,
        special_handling=special,
    )


def generate_order(
    order_id: str,
    created_at: datetime,
    pickup_location: Location | None = None,
    pickup_type: PickupType = PickupType.FACILITY,
) -> Order:
    """Generate a complete order with 1+ packages going to potentially different destinations."""
    if pickup_location is None:
        pickup_location = random.choice(PICKUP_LOCATIONS)

    urgency = _random_urgency()
    num_packages = _weighted_choice(PACKAGES_PER_ORDER_WEIGHTS)

    used_destinations: set[str] = set()
    packages = [
        generate_package(
            package_id=f"{order_id}_pkg{i+1}",
            urgency=urgency,
            created_at=created_at,
            used_destinations=used_destinations,
        )
        for i in range(num_packages)
    ]

    tracking_required = random.random() < 0.2

    return Order(
        id=order_id,
        pickup_location=pickup_location,
        pickup_type=pickup_type,
        packages=packages,
        urgency=urgency,
        created_at=created_at,
        tracking_required=tracking_required,
    )


def generate_orders(
    count: int,
    base_time: datetime,
    seed: int | None = None,
) -> list[Order]:
    """
    Generate a batch of orders with arrival times spread over a simulated period.

    Orders arrive in waves:
    - Morning rush (7-9am): ~40% of orders
    - Midday (10am-1pm): ~25%
    - Afternoon spike (2-4pm): ~25%
    - Late afternoon (4-6pm): ~10%
    """
    if seed is not None:
        random.seed(seed)

    # Build arrival times using wave pattern
    wave_weights = [
        (0, 120, 0.40),     # 7am-9am: 40%
        (180, 360, 0.25),   # 10am-1pm: 25%
        (420, 540, 0.25),   # 2pm-4pm: 25%
        (540, 660, 0.10),   # 4pm-6pm: 10%
    ]

    # Allocate orders to waves, ensuring exact count
    wave_counts = []
    remaining = count
    for i, (_, _, weight) in enumerate(wave_weights):
        if i == len(wave_weights) - 1:
            n = remaining
        else:
            n = round(count * weight)
            n = min(n, remaining)
        wave_counts.append(n)
        remaining -= n

    arrival_offsets: list[float] = []
    for (start_min, end_min, _), n in zip(wave_weights, wave_counts):
        for _ in range(n):
            offset = random.uniform(start_min, end_min)
            arrival_offsets.append(offset)

    arrival_offsets.sort()

    orders = []
    for i, offset in enumerate(arrival_offsets):
        created_at = base_time + timedelta(minutes=offset)
        order = generate_order(
            order_id=f"ORD-{i+1:03d}",
            created_at=created_at,
        )
        orders.append(order)

    return orders


def generate_field_collection_orders(
    count: int,
    base_time: datetime,
    agent_location: Location | None = None,
    seed: int | None = None,
) -> list[Order]:
    """Generate orders from field collection / traveling agent visits."""
    if seed is not None:
        random.seed(seed)

    if agent_location is None:
        # Random location in the city
        agent_location = Location(
            lat=40.7580 + random.uniform(-0.03, 0.03),
            lng=-73.9855 + random.uniform(-0.02, 0.02),
            name="Field Agent - Unit Alpha",
        )

    orders = []
    for i in range(count):
        # Each field visit is nearby the agent's general area
        client_loc = Location(
            lat=agent_location.lat + random.uniform(-0.01, 0.01),
            lng=agent_location.lng + random.uniform(-0.008, 0.008),
            name=f"Field Visit - Client {i+1}",
        )
        created_at = base_time + timedelta(minutes=i * 25)  # ~25 min per field visit
        order = generate_order(
            order_id=f"FIELD-{i+1:03d}",
            created_at=created_at,
            pickup_location=client_loc,
            pickup_type=PickupType.FIELD_VISIT,
        )
        orders.append(order)

    return orders


def generate_home_healthcare_orders(
    count: int,
    base_time: datetime,
    nurse_location: Location | None = None,
    seed: int | None = None,
) -> list[Order]:
    """Compatibility alias for the medical-courier repo."""
    return generate_field_collection_orders(
        count=count,
        base_time=base_time,
        agent_location=nurse_location,
        seed=seed,
    )
