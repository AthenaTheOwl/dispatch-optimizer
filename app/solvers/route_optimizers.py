"""Concrete route optimizer implementations."""

from app.models import Location, RouteStop
from app.algorithms.route_optimizer import (
    optimize_route, nearest_neighbor_order, two_opt_improve,
)
from app.solvers.base import RouteOptimizer


class NearestNeighborOptimizer(RouteOptimizer):
    """Nearest-neighbor heuristic only — no local search improvement."""

    def optimize(self, stops: list[RouteStop], start: Location) -> list[RouteStop]:
        if len(stops) <= 1:
            return list(stops)
        return nearest_neighbor_order(stops, start)

    @property
    def name(self) -> str:
        return "nearest_neighbor"


class TwoOptOptimizer(RouteOptimizer):
    """Nearest-neighbor construction + 2-opt local search.

    This is the primary route optimizer.
    """

    def __init__(self, max_iterations: int = 100):
        self.max_iterations = max_iterations

    def optimize(self, stops: list[RouteStop], start: Location) -> list[RouteStop]:
        if len(stops) <= 1:
            return list(stops)
        return optimize_route(stops, start)

    @property
    def name(self) -> str:
        return "nn_2opt"
