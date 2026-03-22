"""Distance and travel time calculations with stochastic variability."""

import math
import numpy as np
from app.models import Location, PICKUP_BUFFER_MINUTES, DELIVERY_BUFFER_MINUTES

# Earth radius in km
EARTH_RADIUS_KM = 6371.0

# Road factor: straight-line distance x this = approximate road distance
# NYC is a grid, so this is relatively low compared to suburban areas
ROAD_FACTOR = 1.4


def haversine_km(loc1: Location, loc2: Location) -> float:
    """Great-circle distance between two locations in km."""
    lat1, lng1 = math.radians(loc1.lat), math.radians(loc1.lng)
    lat2, lng2 = math.radians(loc2.lat), math.radians(loc2.lng)

    dlat = lat2 - lat1
    dlng = lng2 - lng1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_KM * c


def road_distance_km(loc1: Location, loc2: Location) -> float:
    """Estimated road distance (haversine x road factor)."""
    return haversine_km(loc1, loc2) * ROAD_FACTOR


def travel_time_minutes(loc1: Location, loc2: Location, speed_kmh: float = 25.0) -> float:
    """Deterministic travel time in minutes."""
    dist = road_distance_km(loc1, loc2)
    if speed_kmh <= 0:
        return float("inf")
    return (dist / speed_kmh) * 60


def stochastic_travel_time(
    loc1: Location,
    loc2: Location,
    speed_kmh: float = 25.0,
    cv: float = 0.2,
    rng: np.random.Generator | None = None,
) -> float:
    """
    Sample a travel time from a log-normal distribution.

    cv=0.2 for normal conditions, cv=0.35 for rush hour.
    Returns time in minutes.
    """
    base = travel_time_minutes(loc1, loc2, speed_kmh)
    if base <= 0:
        return 0.0
    if rng is None:
        rng = np.random.default_rng()

    # Log-normal parameters from mean and CV
    sigma2 = math.log(1 + cv ** 2)
    mu = math.log(base) - sigma2 / 2

    return float(rng.lognormal(mu, math.sqrt(sigma2)))


def pessimistic_travel_time(
    loc1: Location,
    loc2: Location,
    speed_kmh: float = 25.0,
    cv: float = 0.2,
    percentile: float = 0.80,
) -> float:
    """
    Travel time at the given percentile (default 80th) of the log-normal distribution.
    Used for deadline feasibility checks — conservative estimate.
    """
    base = travel_time_minutes(loc1, loc2, speed_kmh)
    if base <= 0:
        return 0.0

    sigma2 = math.log(1 + cv ** 2)
    mu = math.log(base) - sigma2 / 2
    sigma = math.sqrt(sigma2)

    from scipy.stats import norm
    z = norm.ppf(percentile)
    return math.exp(mu + sigma * z)


def route_distance_km(locations: list[Location]) -> float:
    """Total road distance along a sequence of locations."""
    total = 0.0
    for i in range(len(locations) - 1):
        total += road_distance_km(locations[i], locations[i + 1])
    return total


def route_time_minutes(
    locations: list[Location],
    speed_kmh: float = 25.0,
    pickup_count: int = 0,
    delivery_count: int = 0,
) -> float:
    """Total travel time along a route, including buffer times at stops."""
    travel = sum(
        travel_time_minutes(locations[i], locations[i + 1], speed_kmh)
        for i in range(len(locations) - 1)
    )
    buffers = pickup_count * PICKUP_BUFFER_MINUTES + delivery_count * DELIVERY_BUFFER_MINUTES
    return travel + buffers


def distance_matrix(locations: list[Location]) -> np.ndarray:
    """NxN road distance matrix between all locations."""
    n = len(locations)
    matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = road_distance_km(locations[i], locations[j])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix
