"""Generate realistic driver fleet with varied capabilities."""

import random
from datetime import datetime, timedelta
from app.models import (
    Driver, Location, VehicleType, ColdStorage, Certification, DriverStatus,
)

# Driver name pool
FIRST_NAMES = [
    "Marcus", "Elena", "James", "Priya", "Carlos", "Sarah", "David",
    "Fatima", "Alex", "Maria", "Kevin", "Aisha", "Tommy", "Lin",
    "Roberto", "Jessica", "Andre", "Yuki", "Omar", "Rachel",
]

# Starting positions scattered across the city
STARTING_POSITIONS = [
    Location(lat=40.7580, lng=-73.9855, name="Central District"),
    Location(lat=40.7282, lng=-73.7942, name="South Central"),
    Location(lat=40.7831, lng=-73.9712, name="Northwest Sector"),
    Location(lat=40.7736, lng=-73.9566, name="Northeast Sector"),
    Location(lat=40.7128, lng=-74.0060, name="South Financial"),
    Location(lat=40.6892, lng=-73.9857, name="East Borough Central"),
    Location(lat=40.6782, lng=-73.9442, name="East Borough North"),
    Location(lat=40.7614, lng=-73.9244, name="North Borough"),
    Location(lat=40.7434, lng=-73.9518, name="North Borough South"),
    Location(lat=40.8100, lng=-73.9530, name="North District"),
    Location(lat=40.6950, lng=-73.9840, name="East Borough West"),
    Location(lat=40.7505, lng=-73.9934, name="West Central"),
    Location(lat=40.7185, lng=-73.9890, name="Southeast District"),
    Location(lat=40.8300, lng=-73.9410, name="Far North"),
    Location(lat=40.7490, lng=-73.8735, name="East Borough Far"),
]


def generate_drivers(
    count: int,
    base_time: datetime,
    seed: int | None = None,
) -> list[Driver]:
    """
    Generate a fleet of drivers with realistic capability distribution.

    Distribution:
    - Vehicle: 60% car, 25% van, 15% bike
    - Cold storage: 40% none, 30% cooler, 20% active fridge, 10% cryo
    - Certifications: all have basic, ~30% have hazmat, ~40% have cold chain
    - Shifts: mix of morning (7am-3pm), full day (7am-7pm), afternoon (11am-7pm)
    """
    if seed is not None:
        random.seed(seed)

    vehicle_weights = {VehicleType.CAR: 0.60, VehicleType.VAN: 0.25, VehicleType.BIKE: 0.15}
    cold_weights = {
        ColdStorage.NONE: 0.40,
        ColdStorage.COOLER: 0.30,
        ColdStorage.ACTIVE_FRIDGE: 0.20,
        ColdStorage.CRYO: 0.10,
    }

    shift_patterns = [
        (0, 8),    # 7am-3pm (morning)
        (0, 12),   # 7am-7pm (full day)
        (4, 12),   # 11am-7pm (afternoon)
    ]
    shift_weights = [0.40, 0.35, 0.25]

    drivers = []
    positions = random.sample(STARTING_POSITIONS, min(count, len(STARTING_POSITIONS)))
    if count > len(STARTING_POSITIONS):
        positions += random.choices(STARTING_POSITIONS, k=count - len(STARTING_POSITIONS))

    names = random.sample(FIRST_NAMES, min(count, len(FIRST_NAMES)))
    if count > len(FIRST_NAMES):
        names += [f"Driver-{i}" for i in range(len(FIRST_NAMES), count)]

    for i in range(count):
        vehicle = random.choices(list(vehicle_weights.keys()), weights=list(vehicle_weights.values()))[0]
        cold = random.choices(list(cold_weights.keys()), weights=list(cold_weights.values()))[0]

        # Bikes can't have cryo or active fridge
        if vehicle == VehicleType.BIKE and cold in (ColdStorage.CRYO, ColdStorage.ACTIVE_FRIDGE):
            cold = ColdStorage.COOLER

        certs = [Certification.BASIC]
        if random.random() < 0.30:
            certs.append(Certification.HAZMAT)
        if cold != ColdStorage.NONE and random.random() < 0.60:
            certs.append(Certification.COLD_CHAIN)

        shift_start_offset, shift_end_offset = random.choices(shift_patterns, weights=shift_weights)[0]
        shift_start = base_time + timedelta(hours=shift_start_offset)
        shift_end = base_time + timedelta(hours=shift_end_offset)

        # Some drivers start already en route (simulates mid-day scenario)
        status = DriverStatus.AVAILABLE
        current_load = 0
        if random.random() < 0.15:
            status = DriverStatus.EN_ROUTE
            current_load = random.randint(1, 3)

        # Add small random offset to starting position for realism
        pos = positions[i]
        jittered_pos = Location(
            lat=pos.lat + random.uniform(-0.003, 0.003),
            lng=pos.lng + random.uniform(-0.003, 0.003),
            name=pos.name,
        )

        drivers.append(Driver(
            id=f"DRV-{i+1:03d}",
            name=names[i],
            current_location=jittered_pos,
            vehicle_type=vehicle,
            cold_storage=cold,
            certifications=certs,
            status=status,
            shift_start=shift_start,
            shift_end=shift_end,
            current_load=current_load,
        ))

    return drivers
