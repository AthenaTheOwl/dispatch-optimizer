"""Pluggable solver framework for dispatch optimization.

Importing this package auto-registers all solvers, cost functions, and
route optimizers. No need for `import app.solvers.registry` at call sites.
"""

from app.solvers.registry import register_all as _register_all

_register_all()
