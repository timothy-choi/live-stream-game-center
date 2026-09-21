"""Read-only product API for synchronized games.

Clients never start, stop, own, or reset M4-M10 tracking. PostgreSQL is
authoritative. Realtime delivery is an optimization for freshness.
"""

from lsgc.api.app import create_app
from lsgc.api.schemas import GameEvent, GameSummary

__all__ = ["GameEvent", "GameSummary", "create_app"]
