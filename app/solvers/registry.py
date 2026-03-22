"""Register all available solvers, cost functions, and route optimizers.

Import this module once at startup to populate the SolverRegistry.
"""

from app.solvers.base import SolverRegistry, NoOpRouteOptimizer
from app.solvers.greedy import GreedySolver
from app.solvers.hungarian import HungarianSolver
from app.solvers.constraints import DefaultConstraintChecker
from app.solvers.route_optimizers import NearestNeighborOptimizer, TwoOptOptimizer
from app.solvers.cost_functions.distance import DistanceCostFunction
from app.solvers.cost_functions.composite import CompositeCostFunction
from app.solvers.cost_functions.greedy_scorer import GreedyScorerCostFunction


def register_all() -> None:
    """Register all built-in components with the SolverRegistry."""

    # Solvers
    SolverRegistry.register_solver("greedy", GreedySolver)
    SolverRegistry.register_solver("hungarian", HungarianSolver)

    # Cost functions
    SolverRegistry.register_cost_function("distance_only", DistanceCostFunction)
    SolverRegistry.register_cost_function("composite", CompositeCostFunction)
    SolverRegistry.register_cost_function("greedy_scorer", GreedyScorerCostFunction)

    # Route optimizers
    SolverRegistry.register_route_optimizer("none", NoOpRouteOptimizer)
    SolverRegistry.register_route_optimizer("nearest_neighbor", NearestNeighborOptimizer)
    SolverRegistry.register_route_optimizer("nn_2opt", TwoOptOptimizer)


# Auto-register on import
register_all()
