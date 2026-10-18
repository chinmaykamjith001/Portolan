"""Persistence for generated details (explanations, code groundings, examples, graphs)."""

from __future__ import annotations

from datetime import UTC, datetime

from portolan.models.detail import CodeGrounding, EdgeExplanation, Example, Explanation, GraphSpec
from portolan.store.db import Database


class DetailStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save_explanation(self, paper_id: str, e: Explanation) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO explanations"
            " (paper_id, concept_id, angle, explanation_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (paper_id, e.concept_id, e.angle, e.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def explanation(self, paper_id: str, concept_id: str, angle: str) -> Explanation | None:
        row = self.db.fetchone(
            "SELECT explanation_json FROM explanations"
            " WHERE paper_id = ? AND concept_id = ? AND angle = ?",
            (paper_id, concept_id, angle),
        )
        return Explanation.model_validate_json(row["explanation_json"]) if row else None

    def save_edge(self, paper_id: str, e: EdgeExplanation) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO edge_explanations"
            " (paper_id, link_id, explanation_json, created_at) VALUES (?, ?, ?, ?)",
            (paper_id, e.link_id, e.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def edge(self, paper_id: str, link_id: str) -> EdgeExplanation | None:
        row = self.db.fetchone(
            "SELECT explanation_json FROM edge_explanations WHERE paper_id = ? AND link_id = ?",
            (paper_id, link_id),
        )
        return EdgeExplanation.model_validate_json(row["explanation_json"]) if row else None

    def save_grounding(self, paper_id: str, g: CodeGrounding) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO code_groundings"
            " (paper_id, concept_id, grounding_json, created_at) VALUES (?, ?, ?, ?)",
            (paper_id, g.concept_id, g.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def grounding(self, paper_id: str, concept_id: str) -> CodeGrounding | None:
        row = self.db.fetchone(
            "SELECT grounding_json FROM code_groundings WHERE paper_id = ? AND concept_id = ?",
            (paper_id, concept_id),
        )
        return CodeGrounding.model_validate_json(row["grounding_json"]) if row else None

    def save_example(self, paper_id: str, e: Example) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO examples"
            " (paper_id, concept_id, variant, example_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (paper_id, e.concept_id, e.variant, e.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def example(self, paper_id: str, concept_id: str, variant: int) -> Example | None:
        row = self.db.fetchone(
            "SELECT example_json FROM examples"
            " WHERE paper_id = ? AND concept_id = ? AND variant = ?",
            (paper_id, concept_id, variant),
        )
        return Example.model_validate_json(row["example_json"]) if row else None

    def examples(self, paper_id: str, concept_id: str) -> list[Example]:
        rows = self.db.fetchall(
            "SELECT example_json FROM examples WHERE paper_id = ? AND concept_id = ?"
            " ORDER BY variant",
            (paper_id, concept_id),
        )
        return [Example.model_validate_json(r["example_json"]) for r in rows]

    def save_graph(self, paper_id: str, concept_id: str, g: GraphSpec | None) -> None:
        """`None` records "no visual would help" so the drawer hides the Graph tab."""
        self.db.execute(
            "INSERT OR REPLACE INTO graphs"
            " (paper_id, concept_id, graph_json, created_at) VALUES (?, ?, ?, ?)",
            (
                paper_id,
                concept_id,
                g.model_dump_json() if g else "null",
                datetime.now(UTC).isoformat(),
            ),
        )

    def graph(self, paper_id: str, concept_id: str) -> tuple[bool, GraphSpec | None]:
        """(was_generated, spec). `(True, None)` means the model chose no graph."""
        row = self.db.fetchone(
            "SELECT graph_json FROM graphs WHERE paper_id = ? AND concept_id = ?",
            (paper_id, concept_id),
        )
        if row is None:
            return False, None
        return True, (
            None
            if row["graph_json"] == "null"
            else GraphSpec.model_validate_json(row["graph_json"])
        )
