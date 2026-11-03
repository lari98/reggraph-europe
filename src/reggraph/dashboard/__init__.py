"""Management dashboard (Phase 5): role-shaped read model and a dependency-free web page."""

from pathlib import Path

from reggraph.dashboard.service import DashboardService

STATIC_DIR = Path(__file__).parent / "static"

__all__ = ["STATIC_DIR", "DashboardService"]
