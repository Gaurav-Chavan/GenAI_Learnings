"""The app's own database (data/app.duckdb): assessments shown in the dashboard, reviewer decisions, and knowledge-base signals.

It is separate from the data layers on purpose: rebuilding the data never erases a reviewer's decision, and the dashboard never needs
the vault or the hidden answer keys. Nothing here is read by the scorer.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from ..contracts import FinalResult
from ..data.config import load_config

SCHEMA = {
    # one row per claim and system: "rules" (rules-only baseline, free, every claim) or "agents" (a saved agentic run, where one exists)
    "assessments": """claim_id VARCHAR, system VARCHAR, band VARCHAR, trust_score DOUBLE, evidence_status VARCHAR, result_json VARCHAR,
                      source VARCHAR, created_at TIMESTAMP, PRIMARY KEY (claim_id, system)""",
    # the audit trail: every reviewer decision, including the ones that disagree with the system
    "decisions": """decision_id VARCHAR PRIMARY KEY, claim_id VARCHAR, reviewer VARCHAR, decision VARCHAR, final_band VARCHAR, note VARCHAR,
                    system_band VARCHAR, disagrees_with_system BOOLEAN, created_at TIMESTAMP, tags VARCHAR, escalated_to VARCHAR""",
    # who owns a claim now: set by hand on the claim page, or by an escalation
    "assignments": "claim_id VARCHAR PRIMARY KEY, assignee VARCHAR, assigned_by VARCHAR, created_at TIMESTAMP",
    # knowledge-base signals ticked by a reviewer: pending -> active (after the regression gate) or blocked
    "signals": """signal_id VARCHAR PRIMARY KEY, source_claim_id VARCHAR, indicator_code VARCHAR, plain_text VARCHAR, evidence_ids VARCHAR,
                  reviewer VARCHAR, note VARCHAR, status VARCHAR, gate_result VARCHAR, created_at TIMESTAMP, updated_at TIMESTAMP""",
}
SYSTEMS = ("rules", "agents")
OPEN_DECISIONS = ("hold", "escalate")  # these keep a claim open: only agree and override close it


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Store:
    def __init__(self, cfg: dict | None = None, path: Path | None = None):
        self.cfg = cfg or load_config()
        self.path = path or Path(self.cfg["data_dir"]) / "app.duckdb"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        for table, columns in SCHEMA.items():
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns})")
        # added after the first version: whether the reviewer's decision contradicted the system's band when the signal was proposed
        self.con.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS disagreed BOOLEAN DEFAULT FALSE")
        # added later: a reviewer's tags on what was wrong with the system's output, and who a claim was escalated to
        self.con.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS tags VARCHAR")
        self.con.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS escalated_to VARCHAR")

    def close(self) -> None:
        self.con.close()

    def rows(self, sql: str, *params) -> list[dict]:
        cur = self.con.execute(sql, list(params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    # ------------------------------------------------------------------ assessments
    def put_assessment(self, claim_id: str, system: str, result: FinalResult | dict, source: str) -> None:
        """Insert or replace the assessment of a claim by one system. A result is a FinalResult, or a plain dict for a saved agent run."""
        if system not in SYSTEMS:
            raise ValueError(f"unknown system {system!r}; choose from {SYSTEMS}")
        data = result.model_dump(mode="json") if isinstance(result, FinalResult) else dict(result)
        self.con.execute("INSERT OR REPLACE INTO assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         [claim_id, system, data["band"], data["trust_score"], data.get("evidence_status", "unknown"),
                          json.dumps(data, default=str), source, now()])

    def get_assessment(self, claim_id: str, system: str = "rules") -> dict | None:
        rows = self.rows("SELECT * FROM assessments WHERE claim_id = ? AND system = ?", claim_id, system)
        if not rows:
            return None
        row = rows[0]
        row["result"] = json.loads(row.pop("result_json"))
        return row

    def count_assessments(self) -> dict[str, int]:
        return {r["system"]: r["n"] for r in self.rows("SELECT system, count(*) AS n FROM assessments GROUP BY system")}

    # ------------------------------------------------------------------ decisions and signals (used by the dashboard)
    def add_decision(self, claim_id: str, reviewer: str, decision: str, final_band: str, note: str, system_band: str,
                     tags: list[str] | None = None, escalated_to: str = "") -> str:
        decision_id = f"DEC-{uuid.uuid4().hex[:10]}"
        self.con.execute("INSERT INTO decisions (decision_id, claim_id, reviewer, decision, final_band, note, system_band, disagrees_with_system, created_at, tags, escalated_to) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [decision_id, claim_id, reviewer, decision, final_band, note, system_band, final_band != system_band, now(), json.dumps(tags or []), escalated_to or None])
        return decision_id

    def decisions_for(self, claim_id: str) -> list[dict]:
        rows = self.rows("SELECT * FROM decisions WHERE claim_id = ? ORDER BY created_at DESC", claim_id)
        for r in rows:
            r["tags"] = json.loads(r["tags"] or "[]")  # decisions saved before tags existed have none
        return rows

    # ------------------------------------------------------------------ who owns a claim
    def assign(self, claim_id: str, assignee: str, by: str) -> None:
        """Give a claim to a reviewer (replacing any earlier owner). An empty name removes the owner."""
        if not assignee.strip():
            self.con.execute("DELETE FROM assignments WHERE claim_id = ?", [claim_id])
            return
        self.con.execute("INSERT OR REPLACE INTO assignments VALUES (?, ?, ?, ?)", [claim_id, assignee.strip(), by.strip(), now()])

    def assignee_of(self, claim_id: str) -> str | None:
        rows = self.rows("SELECT assignee FROM assignments WHERE claim_id = ?", claim_id)
        return rows[0]["assignee"] if rows else None

    def assignments(self) -> dict[str, str]:
        return {r["claim_id"]: r["assignee"] for r in self.rows("SELECT claim_id, assignee FROM assignments")}

    def add_signal(self, source_claim_id: str, indicator_code: str, plain_text: str, evidence_ids: list[str], reviewer: str, note: str = "",
                   disagreed: bool = False) -> str:
        signal_id = f"SIG-{uuid.uuid4().hex[:10]}"
        self.con.execute("INSERT INTO signals (signal_id, source_claim_id, indicator_code, plain_text, evidence_ids, reviewer, note, status, gate_result, created_at, "
                         "updated_at, disagreed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [signal_id, source_claim_id, indicator_code, plain_text, json.dumps(evidence_ids), reviewer, note, "pending", None, now(), now(), disagreed])
        return signal_id

    def set_signal_status(self, signal_id: str, status: str, gate_result: str | None) -> None:
        if status not in ("pending", "active", "blocked"):
            raise ValueError(f"unknown signal status {status!r}")
        self.con.execute("UPDATE signals SET status = ?, gate_result = ?, updated_at = ? WHERE signal_id = ?", [status, gate_result, now(), signal_id])

    def signals(self, status: str | None = None) -> list[dict]:
        where, params = ("WHERE status = ?", [status]) if status else ("", [])
        return self.rows(f"SELECT * FROM signals {where} ORDER BY created_at DESC", *params)
