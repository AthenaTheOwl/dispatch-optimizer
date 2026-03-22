"""Abstract interfaces for the solver framework.

Every solver, cost function, constraint checker, and route optimizer
implements these interfaces. This is the plug point — swap any component
without touching the rest of the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models import (
    Driver, Order, Location, Route, RouteStop, Assignment, DispatchResult, Scenario,
)


# --- Constraint Checker ---

class ConstraintChecker(ABC):
    """Hard constraint validation for driver-order feasibility."""

    @abstractmethod
    def is_feasible(
        self,
        driver: Driver,
        order: Order,
        current_time: datetime,
        stops: list[RouteStop] | None = None,
    ) -> tuple[bool, list[str]]:
        """Returns (feasible, list_of_violation_descriptions).

        Args:
            driver: Candidate driver.
            order: Order to assign.
            current_time: Dispatch time.
            stops: Optional explicit stop sequence for route-based checks.
                   If None, uses default raw destination order.
        """

    def filter_feasible_drivers(
        self,
        drivers: list[Driver],
        order: Order,
        current_time: datetime,
    ) -> list[Driver]:
        """Convenience: return only drivers feasible for this order."""
        return [d for d in drivers if self.is_feasible(d, order, current_time)[0]]


# --- Cost Function ---

@dataclass
class CostResult:
    """Result of computing assignment cost for a driver-order pair."""
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)
    feasible: bool = True


class CostFunction(ABC):
    """Computes the assignment cost for a driver-order pair.

    Cost functions are the primary IP differentiator. The cost matrix
    drives the Hungarian solver and the greedy scorer — different cost
    functions produce different assignments from the same algorithm.
    """

    @abstractmethod
    def compute(
        self,
        driver: Driver,
        order: Order,
        current_time: datetime,
        stops: list[RouteStop] | None = None,
    ) -> CostResult:
        """Compute cost. Return CostResult with feasible=False for violations.

        Args:
            driver: Candidate driver.
            order: Order to assign.
            current_time: Dispatch time.
            stops: Optional explicit stop sequence. When provided, the cost
                   function evaluates against this exact route (e.g. the
                   optimized stop order the solver intends to execute).
                   If None, uses raw build_stops(order).
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for experiment logging."""

    @property
    def parameters(self) -> dict[str, Any]:
        """Return current parameter values for experiment logging."""
        return {}


# --- Route Optimizer ---

class RouteOptimizer(ABC):
    """Optimizes the stop sequence for a single driver's route."""

    @abstractmethod
    def optimize(
        self, stops: list[RouteStop], start: Location,
    ) -> list[RouteStop]:
        """Return reordered stops minimizing total distance."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for experiment logging."""


class NoOpRouteOptimizer(RouteOptimizer):
    """Passes stops through unchanged. Used by greedy baseline."""

    def optimize(self, stops: list[RouteStop], start: Location) -> list[RouteStop]:
        return list(stops)

    @property
    def name(self) -> str:
        return "none"


# --- Solver ---

@dataclass
class SolverConfig:
    """Serializable configuration snapshot for experiment reproducibility."""
    solver_name: str
    cost_function_name: str
    cost_function_params: dict[str, Any] = field(default_factory=dict)
    route_optimizer_name: str = "none"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "solver": self.solver_name,
            "cost_function": self.cost_function_name,
            "cost_function_params": self.cost_function_params,
            "route_optimizer": self.route_optimizer_name,
            "extra": self.extra,
        }


class Solver(ABC):
    """Abstract solver interface.

    A solver takes a Scenario and returns a DispatchResult.
    Internally it uses a CostFunction, ConstraintChecker, and RouteOptimizer.
    """

    def __init__(
        self,
        cost_function: CostFunction,
        constraint_checker: ConstraintChecker,
        route_optimizer: RouteOptimizer | None = None,
    ):
        self.cost_function = cost_function
        self.constraint_checker = constraint_checker
        self.route_optimizer = route_optimizer or NoOpRouteOptimizer()

    @abstractmethod
    def solve(self, scenario: Scenario) -> DispatchResult:
        """Run the solver on a scenario. Returns assignments + metrics."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable solver name for logging."""

    @property
    def config(self) -> SolverConfig:
        """Snapshot of current configuration for experiment logging."""
        return SolverConfig(
            solver_name=self.name,
            cost_function_name=self.cost_function.name,
            cost_function_params=self.cost_function.parameters,
            route_optimizer_name=self.route_optimizer.name,
        )


# --- Solver Registry ---

class SolverRegistry:
    """Registry of available solvers. Factory pattern for solver construction."""

    _solvers: dict[str, type[Solver]] = {}
    _cost_functions: dict[str, type[CostFunction]] = {}
    _route_optimizers: dict[str, type[RouteOptimizer]] = {}

    @classmethod
    def register_solver(cls, name: str, solver_cls: type[Solver]) -> None:
        cls._solvers[name] = solver_cls

    @classmethod
    def register_cost_function(cls, name: str, cf_cls: type[CostFunction]) -> None:
        cls._cost_functions[name] = cf_cls

    @classmethod
    def register_route_optimizer(cls, name: str, ro_cls: type[RouteOptimizer]) -> None:
        cls._route_optimizers[name] = ro_cls

    @classmethod
    def _ensure_registered(cls) -> None:
        """Lazy-bootstrap: register all built-in components if empty."""
        if not cls._solvers:
            from app.solvers.registry import register_all
            register_all()

    @classmethod
    def get_solver(
        cls,
        solver_name: str,
        cost_function_name: str,
        constraint_checker: ConstraintChecker,
        route_optimizer_name: str | None = None,
        **cost_function_kwargs,
    ) -> Solver:
        """Build a solver from registered components."""
        cls._ensure_registered()
        if solver_name not in cls._solvers:
            raise ValueError(f"Unknown solver: {solver_name}. Available: {list(cls._solvers.keys())}")
        if cost_function_name not in cls._cost_functions:
            raise ValueError(f"Unknown cost function: {cost_function_name}. Available: {list(cls._cost_functions.keys())}")

        cf = cls._cost_functions[cost_function_name](**cost_function_kwargs)

        ro: RouteOptimizer = NoOpRouteOptimizer()
        if route_optimizer_name and route_optimizer_name != "none":
            if route_optimizer_name not in cls._route_optimizers:
                raise ValueError(f"Unknown route optimizer: {route_optimizer_name}.")
            ro = cls._route_optimizers[route_optimizer_name]()

        return cls._solvers[solver_name](
            cost_function=cf,
            constraint_checker=constraint_checker,
            route_optimizer=ro,
        )

    @classmethod
    def list_solvers(cls) -> list[str]:
        return list(cls._solvers.keys())

    @classmethod
    def list_cost_functions(cls) -> list[str]:
        return list(cls._cost_functions.keys())

    @classmethod
    def list_route_optimizers(cls) -> list[str]:
        return list(cls._route_optimizers.keys())
