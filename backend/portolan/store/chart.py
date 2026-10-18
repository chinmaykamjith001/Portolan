"""Persistence for the concept graph (spec §9.3): one row per Concept, one per Layer, and a
membership table because a Concept can appear in several Layers (the graph is a DAG)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from portolan.models.concept import MASTER, Concept, Layer
from portolan.store.db import Database


class ChartStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- concepts --------------------------------------------------------------------------------

    def upsert_concept(self, paper_id: str, c: Concept) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO concepts"
            " (paper_id, concept_id, canonical_name, depth, concept_json) VALUES (?, ?, ?, ?, ?)",
            (paper_id, c.id, c.canonical_name, c.depth, c.model_dump_json()),
        )

    def get_concept(self, paper_id: str, concept_id: str) -> Concept | None:
        row = self.db.fetchone(
            "SELECT concept_json FROM concepts WHERE paper_id = ? AND concept_id = ?",
            (paper_id, concept_id),
        )
        return Concept.model_validate_json(row["concept_json"]) if row else None

    def concepts(self, paper_id: str, ids: Iterable[str] | None = None) -> dict[str, Concept]:
        """All of a paper's concepts, or only those in `ids` (unknown ids are skipped)."""
        wanted: list[str] = []
        rows: list[sqlite3.Row] = []
        if ids is None:
            rows = self.db.fetchall(
                "SELECT concept_json FROM concepts WHERE paper_id = ? ORDER BY concept_id",
                (paper_id,),
            )
        else:
            wanted = list(dict.fromkeys(ids))
            if not wanted:
                return {}
            for start in range(0, len(wanted), 500):  # stay under SQLite's variable limit
                chunk = wanted[start : start + 500]
                rows += self.db.fetchall(
                    "SELECT concept_json FROM concepts WHERE paper_id = ?"
                    f" AND concept_id IN ({', '.join('?' * len(chunk))})",
                    (paper_id, *chunk),
                )
        out: dict[str, Concept] = {}
        for row in rows:
            c = Concept.model_validate_json(row["concept_json"])
            out[c.id] = c
        if ids is not None:  # keep the caller's order
            out = {i: out[i] for i in wanted if i in out}
        return out

    def set_issue_counts(self, paper_id: str, counts: Mapping[str, int]) -> None:
        """Set `issue_count_self` on every concept: `counts[id]`, or 0 when absent."""
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT concept_id, concept_json FROM concepts WHERE paper_id = ?", (paper_id,)
            ).fetchall()
            for row in rows:
                c = Concept.model_validate_json(row["concept_json"])
                wanted = counts.get(c.id, 0)
                if c.issue_count_self != wanted:
                    c.issue_count_self = wanted
                    conn.execute(
                        "UPDATE concepts SET concept_json = ?"
                        " WHERE paper_id = ? AND concept_id = ?",
                        (c.model_dump_json(), paper_id, c.id),
                    )

    def find_by_canonical(self, paper_id: str, canonical_name: str) -> Concept | None:
        row = self.db.fetchone(
            "SELECT concept_json FROM concepts WHERE paper_id = ? AND canonical_name = ?"
            " ORDER BY depth, concept_id LIMIT 1",
            (paper_id, canonical_name),
        )
        return Concept.model_validate_json(row["concept_json"]) if row else None

    # -- layers ----------------------------------------------------------------------------------

    def save_layer(self, layer: Layer) -> None:
        """Write the layer, replace its memberships, and mark its center as jumpable."""
        members = list(dict.fromkeys([layer.center, *layer.concepts]))
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO layers"
                " (paper_id, layer_id, center, gen_state, layer_json, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    layer.paper_id,
                    layer.id,
                    layer.center,
                    layer.gen_state,
                    layer.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.execute(
                "DELETE FROM layer_members WHERE paper_id = ? AND layer_id = ?",
                (layer.paper_id, layer.id),
            )
            conn.executemany(
                "INSERT INTO layer_members (paper_id, layer_id, concept_id) VALUES (?, ?, ?)",
                [(layer.paper_id, layer.id, cid) for cid in members],
            )
            if layer.id != MASTER:
                row = conn.execute(
                    "SELECT concept_json FROM concepts WHERE paper_id = ? AND concept_id = ?",
                    (layer.paper_id, layer.center),
                ).fetchone()
                if row is not None:
                    center = Concept.model_validate_json(row["concept_json"])
                    if not center.has_layer:
                        center.has_layer = True
                        conn.execute(
                            "UPDATE concepts SET concept_json = ?"
                            " WHERE paper_id = ? AND concept_id = ?",
                            (center.model_dump_json(), layer.paper_id, layer.center),
                        )

    def get_layer(self, paper_id: str, layer_id: str) -> Layer | None:
        row = self.db.fetchone(
            "SELECT layer_json FROM layers WHERE paper_id = ? AND layer_id = ?",
            (paper_id, layer_id),
        )
        return Layer.model_validate_json(row["layer_json"]) if row else None

    def layers_containing(self, paper_id: str, concept_id: str) -> list[str]:
        """Ids of every layer the concept appears in, as center or member ("Also appears in…")."""
        rows = self.db.fetchall(
            "SELECT layer_id FROM layer_members WHERE paper_id = ? AND concept_id = ?"
            " ORDER BY layer_id",
            (paper_id, concept_id),
        )
        return [str(r["layer_id"]) for r in rows]

    def layer_ids(self, paper_id: str) -> list[str]:
        rows = self.db.fetchall(
            "SELECT layer_id FROM layers WHERE paper_id = ? ORDER BY layer_id", (paper_id,)
        )
        return [str(r["layer_id"]) for r in rows]

    def children(self, paper_id: str, concept_id: str) -> list[str]:
        """The concepts of the layer centred on `concept_id` (layer id = center id), or []."""
        layer = self.get_layer(paper_id, concept_id)
        return list(layer.concepts) if layer else []

    def delete_paper(self, paper_id: str) -> None:
        with self.db.transaction() as conn:
            for table in ("layer_members", "layers", "concepts"):
                conn.execute(f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,))
