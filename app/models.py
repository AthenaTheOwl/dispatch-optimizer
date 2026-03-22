"""Domain models for DispatchIQ constrained vehicle routing system."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional


# --- Enums ---

class CargoType(str, Enum):
    STANDARD = "standard"             # Common items, ambient handling
    TIME_CRITICAL = "time_critical"   # Degrades rapidly, ~30 min window
    BULK = "bulk"                     # High volume, flexible deadline
    FRAGILE = "fragile"              # Requires careful handling
    SENSITIVE = "sensitive"          # Temperature-sensitive, must not freeze
    COLD_CHAIN = "cold_chain"        # Requires frozen transport
    ULTRA_COLD = "ultra_cold"        # Requires cryogenic transport


class TempRegime(str, Enum):
    AMBIENT = "ambient"                   # 15-25°C, no special equipment
    REFRIGERATED = "refrigerated"         # 2-8°C, insulated cooler with ice packs
    FROZEN = "frozen"                     # -20°C, frozen gel packs or dry ice
    CRYOGENIC = "cryogenic"               # -80°C or below, dry ice shipper or LN2 dewar


class Urgency(str, Enum):
    STAT = "stat"           # ≤1 hour
    URGENT = "urgent"       # ≤2 hours
    ROUTINE = "routine"     # ≤4 hours
    STANDARD = "standard"   # Next-day


class HazardClass(str, Enum):
    STANDARD = "standard"     # Standard items
    ELEVATED = "elevated"     # Requires hazmat certification


class VehicleType(str, Enum):
    BIKE = "bike"       # Fast in Manhattan, low capacity
    CAR = "car"         # Standard
    VAN = "van"         # High capacity


class ColdStorage(str, Enum):
    NONE = "none"                 # Ambient only
    COOLER = "cooler"             # Insulated cooler — handles refrigerated
    ACTIVE_FRIDGE = "active_fridge"  # Powered fridge — handles refrigerated reliably
    CRYO = "cryo"                 # Dry ice shipper / LN2 — handles frozen + cryogenic


class Certification(str, Enum):
    BASIC = "basic"                       # Standard driver
    HAZMAT = "hazmat"                     # ADR/DOT certified
    COLD_CHAIN = "cold_chain"             # Cold-chain handling trained


class PickupType(str, Enum):
    FACILITY = "facility"       # Fixed location (hub, branch, destination)
    FIELD_VISIT = "field_visit" # Field collection point
    AD_HOC = "ad_hoc"           # Rendezvous point


class DriverStatus(str, Enum):
    AVAILABLE = "available"
    EN_ROUTE = "en_route"
    AT_PICKUP = "at_pickup"
    AT_DELIVERY = "at_delivery"
    OFFLINE = "offline"


class FacilityType(str, Enum):
    HUB = "hub"
    BRANCH = "branch"
    DESTINATION = "destination"
    SATELLITE = "satellite"


class StopType(str, Enum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


# --- Lookup tables ---

# What temp regime each cargo type requires
CARGO_TEMP_REQUIREMENTS: dict[CargoType, TempRegime] = {
    CargoType.STANDARD: TempRegime.AMBIENT,
    CargoType.TIME_CRITICAL: TempRegime.AMBIENT,
    CargoType.BULK: TempRegime.AMBIENT,
    CargoType.FRAGILE: TempRegime.REFRIGERATED,
    CargoType.SENSITIVE: TempRegime.AMBIENT,  # Must NOT freeze
    CargoType.COLD_CHAIN: TempRegime.FROZEN,
    CargoType.ULTRA_COLD: TempRegime.CRYOGENIC,
}

# What cold storage levels can handle what temp regimes
# Higher-level storage can handle lower requirements (with caveats)
COLD_STORAGE_CAPABILITIES: dict[ColdStorage, set[TempRegime]] = {
    ColdStorage.NONE: {TempRegime.AMBIENT},
    ColdStorage.COOLER: {TempRegime.AMBIENT, TempRegime.REFRIGERATED},
    ColdStorage.ACTIVE_FRIDGE: {TempRegime.AMBIENT, TempRegime.REFRIGERATED},
    ColdStorage.CRYO: {TempRegime.AMBIENT, TempRegime.REFRIGERATED, TempRegime.FROZEN, TempRegime.CRYOGENIC},
}

# Deadline in minutes by urgency level
URGENCY_DEADLINE_MINUTES: dict[Urgency, int] = {
    Urgency.STAT: 60,
    Urgency.URGENT: 120,
    Urgency.ROUTINE: 240,
    Urgency.STANDARD: 1440,  # 24 hours
}

# Slack (how long we can hold before dispatching) in minutes
URGENCY_SLACK_MINUTES: dict[Urgency, int] = {
    Urgency.STAT: 0,
    Urgency.URGENT: 5,
    Urgency.ROUTINE: 20,
    Urgency.STANDARD: 60,
}

# Cargo-specific deadline overrides (some cargo types have tighter deadlines than urgency alone)
CARGO_MAX_MINUTES: dict[CargoType, int] = {
    CargoType.STANDARD: 240,
    CargoType.TIME_CRITICAL: 30,       # Degrades rapidly regardless of urgency
    CargoType.BULK: 1440,
    CargoType.FRAGILE: 360,
    CargoType.SENSITIVE: 1440,
    CargoType.COLD_CHAIN: 1440,
    CargoType.ULTRA_COLD: 480,
}

# Speed by vehicle type in km/h (NYC urban)
VEHICLE_SPEED_KMH: dict[VehicleType, float] = {
    VehicleType.BIKE: 18.0,
    VehicleType.CAR: 25.0,
    VehicleType.VAN: 22.0,
}

# Capacity by vehicle type (number of packages)
VEHICLE_CAPACITY: dict[VehicleType, int] = {
    VehicleType.BIKE: 4,
    VehicleType.CAR: 12,
    VehicleType.VAN: 20,
}

# Buffer times at stops (minutes)
PICKUP_BUFFER_MINUTES = 5.0    # Paperwork, scanning, handoff at pickup
DELIVERY_BUFFER_MINUTES = 3.0  # Handoff at delivery


# --- Core Models ---

class Location(BaseModel):
    lat: float
    lng: float
    name: str
    facility_type: Optional[FacilityType] = None

    def to_tuple(self) -> tuple[float, float]:
        return (self.lat, self.lng)


class Package(BaseModel):
    """A single item within an order, going to a specific destination."""
    id: str
    cargo_type: CargoType
    temp_regime: TempRegime
    destination: Location                # The destination this package goes to
    hazard_class: HazardClass = HazardClass.STANDARD
    deadline: datetime                   # Absolute deadline for this package
    special_handling: list[str] = Field(default_factory=list)  # "fragile", "light_sensitive", etc.

    @property
    def specimen_type(self) -> CargoType:
        """Compatibility alias for the medical-courier repo."""
        return self.cargo_type

    @property
    def biohazard_class(self) -> HazardClass:
        """Compatibility alias for the medical-courier repo."""
        return self.hazard_class


class Order(BaseModel):
    """A pickup event at one location, containing 1+ packages going to potentially different destinations."""
    id: str
    pickup_location: Location
    pickup_type: PickupType = PickupType.FACILITY
    packages: list[Package]
    urgency: Urgency
    created_at: datetime
    tracking_required: bool = False
    time_window_start: Optional[datetime] = None
    time_window_end: Optional[datetime] = None

    @property
    def tightest_deadline(self) -> datetime:
        """The earliest deadline across all packages — drives dispatch urgency."""
        return min(p.deadline for p in self.packages)

    @property
    def total_packages(self) -> int:
        return len(self.packages)

    @property
    def required_temp_regimes(self) -> set[TempRegime]:
        """All temp regimes needed across packages in this order."""
        return {p.temp_regime for p in self.packages}

    @property
    def unique_destinations(self) -> list[Location]:
        """Unique delivery destinations for this order's packages."""
        seen: set[str] = set()
        destinations: list[Location] = []
        for p in self.packages:
            key = f"{p.destination.lat},{p.destination.lng}"
            if key not in seen:
                seen.add(key)
                destinations.append(p.destination)
        return destinations

    @property
    def needs_hazmat_cert(self) -> bool:
        return any(p.hazard_class == HazardClass.ELEVATED for p in self.packages)

    @property
    def slack_minutes(self) -> int:
        return URGENCY_SLACK_MINUTES[self.urgency]

    @property
    def needs_dangerous_goods_cert(self) -> bool:
        """Compatibility alias for the medical-courier repo."""
        return self.needs_hazmat_cert

    @property
    def chain_of_custody(self) -> bool:
        """Compatibility alias for older portfolio/frontend code."""
        return self.tracking_required


class Driver(BaseModel):
    """A driver with vehicle, equipment, and certifications."""
    id: str
    name: str
    current_location: Location
    vehicle_type: VehicleType
    cold_storage: ColdStorage
    certifications: list[Certification]
    status: DriverStatus = DriverStatus.AVAILABLE
    shift_start: datetime
    shift_end: datetime
    current_load: int = 0           # Packages currently carrying

    @property
    def capacity(self) -> int:
        return VEHICLE_CAPACITY[self.vehicle_type]

    @property
    def remaining_capacity(self) -> int:
        return self.capacity - self.current_load

    @property
    def speed_kmh(self) -> float:
        return VEHICLE_SPEED_KMH[self.vehicle_type]

    @property
    def supported_temp_regimes(self) -> set[TempRegime]:
        return COLD_STORAGE_CAPABILITIES[self.cold_storage]

    @property
    def has_hazmat_cert(self) -> bool:
        return Certification.HAZMAT in self.certifications

    @property
    def has_dangerous_goods_cert(self) -> bool:
        """Compatibility alias for the medical-courier repo."""
        return self.has_hazmat_cert


class RouteStop(BaseModel):
    """A single stop in a driver's route."""
    location: Location
    stop_type: StopType
    order_id: str
    package_ids: list[str] = Field(default_factory=list)  # Which packages are picked up or delivered here
    arrival_time: Optional[datetime] = None
    departure_time: Optional[datetime] = None


class Route(BaseModel):
    """A driver's complete route: sequence of pickup and delivery stops."""
    driver_id: str
    stops: list[RouteStop] = Field(default_factory=list)
    total_distance_km: float = 0.0
    total_time_minutes: float = 0.0

    @property
    def num_stops(self) -> int:
        return len(self.stops)

    @property
    def order_ids(self) -> list[str]:
        return list(dict.fromkeys(s.order_id for s in self.stops))


class Assignment(BaseModel):
    """The result of assigning an order to a driver, with the planned route."""
    driver_id: str
    order_id: str
    route: Route
    estimated_pickup_time_min: float
    estimated_total_time_min: float
    total_distance_km: float
    cost_score: float               # Composite optimization score (lower = better)
    cost_breakdown: dict[str, float] = Field(default_factory=dict)  # For "why this driver?" detail
    dispatched_at: Optional[datetime] = None  # When the assignment was made (event-driven sim)
    package_deliveries: list["PackageDeliveryInfo"] = Field(default_factory=list)
    execution_feasible: Optional[bool] = None


class PackageDeliveryInfo(BaseModel):
    """Delivery timing for a single package within an assignment."""
    package_id: str
    delivery_time: Optional[datetime] = None
    deadline: Optional[datetime] = None
    on_time: Optional[bool] = None
    slack_min: Optional[float] = None


class DispatchResult(BaseModel):
    """Complete result of running a dispatch algorithm on a scenario."""
    algorithm_name: str
    assignments: list[Assignment]
    unassigned_orders: list[str] = Field(default_factory=list)  # Order IDs that couldn't be assigned
    total_distance_km: float = 0.0
    total_time_minutes: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)
    validation_rejections: int = 0


class Scenario(BaseModel):
    """A complete simulation scenario: facilities, drivers, orders."""
    drivers: list[Driver]
    orders: list[Order]
    facilities: list[Location]
    current_time: datetime
