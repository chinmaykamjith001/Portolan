"""Persistence for routes."""

from __future__ import annotations

from datetime import UTC, datetime

from portolan.models.route import Route
from portolan.store.db import Database


class RouteStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, route: Route) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO routes (paper_id, route_id, route_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (route.paper_id, route.id, route.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def get(self, paper_id: str, route_id: str) -> Route | None:
        row = self.db.fetchone(
            "SELECT route_json FROM routes WHERE paper_id = ? AND route_id = ?",
            (paper_id, route_id),
        )
        return Route.model_validate_json(row["route_json"]) if row else None

    def find_stop_route(self, paper_id: str, stop_id: str) -> Route | None:
        """The route that contains `stop_id` (main, or a sub-route)."""
        rows = self.db.fetchall("SELECT route_json FROM routes WHERE paper_id = ?", (paper_id,))
        for row in rows:
            route = Route.model_validate_json(row["route_json"])
            if any(s.id == stop_id for s in route.stops):
                return route
        return None

    def delete_paper(self, paper_id: str) -> None:
        self.db.execute("DELETE FROM routes WHERE paper_id = ?", (paper_id,))
