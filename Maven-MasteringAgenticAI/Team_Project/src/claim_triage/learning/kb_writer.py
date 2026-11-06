"""The only code that writes to the knowledge graph from the app, and the live search the safety check runs against.

Writing is deliberately narrow: three fixed statements, each touching only ApprovedSignal nodes. It never deletes anything and never touches a claim,
a pattern card or a clause. The reviewer-facing record of every proposal is the app database; the graph holds the searchable copy.
"""
from __future__ import annotations

import re
from datetime import date

from ..data.graph_conn import connect

# the line a signal applies to, so a nominee lesson is not offered on a motor claim ("all" = every line)
LINE_OF = {"DUPLICATE_CLAIM": "health", "BILL_OUTLIER_VS_PEERS": "health", "PRE_EXISTING_NEAR_INCEPTION": "health", "NOMINEE_CHANGE_NEAR_EVENT": "term",
           "DOC_DATE_MISMATCH": "term", "EARLY_DEATH_VS_HEALTH_HISTORY": "term", "REPEAT_INCIDENT_CLAIM": "motor", "DAMAGE_IN_PRIOR_INSPECTION": "motor"}
STATUSES = ("pending", "active", "blocked")

CYPHER_UPSERT = """
MERGE (s:ApprovedSignal {signal_id: $sid})
SET s.title = $title, s.text = $text, s.indicator_code = $code, s.source_claim_id = $claim, s.line = $line, s.status = $status, s.approved_date = $approved
"""
CYPHER_SET_STATUS = "MATCH (s:ApprovedSignal {signal_id: $sid}) SET s.status = $status, s.approved_date = $approved RETURN count(s) AS n"
CYPHER_SEARCH = """
CALL db.index.fulltext.queryNodes('knowledge_text', $q) YIELD node, score
WHERE node:Pattern OR node:Clause OR (node:ApprovedSignal AND (node.status = 'active' OR node.signal_id = $candidate))
RETURN coalesce(node.code, node.clause_id, node.signal_id) AS id
ORDER BY score DESC LIMIT 10
"""
WRITE_STATEMENTS = [CYPHER_UPSERT, CYPHER_SET_STATUS]


def signal_text(finding: str, plain_text: str, note: str) -> str:
    """What the search index sees: the finding, what was seen, and the reviewer's own lesson."""
    return f"{finding}. {plain_text} Reviewer note: {note}".strip()


class KnowledgeGraph:
    """One connection used for the safety check's searches and for writing signal nodes."""

    def __init__(self):
        self.driver, self.database = connect(retries=2, wait=3, log=lambda *a, **k: None)

    def close(self) -> None:
        self.driver.close()

    # --- the live search (a Searcher for learning.gate) ---------------------------------------------
    def ranked_ids(self, query: str, candidate: str | None = None) -> list[str]:
        from neo4j import READ_ACCESS

        q = re.sub(r"[^A-Za-z0-9\s-]", " ", query)
        with self.driver.session(database=self.database, default_access_mode=READ_ACCESS) as session:
            return session.execute_read(lambda tx: [r["id"] for r in tx.run(CYPHER_SEARCH, q=" ".join(q.split()), candidate=candidate or "")])

    # --- writes: ApprovedSignal nodes only ----------------------------------------------------------
    def upsert(self, signal_id: str, finding: str, text: str, code: str, claim_id: str, status: str, approved: date | None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown signal status {status!r}")
        with self.driver.session(database=self.database) as session:
            session.execute_write(lambda tx: tx.run(CYPHER_UPSERT, sid=signal_id, title=finding, text=text, code=code, claim=claim_id, line=LINE_OF.get(code, "all"),
                                                    status=status, approved=approved).consume())

    def set_status(self, signal_id: str, status: str, approved: date | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown signal status {status!r}")
        with self.driver.session(database=self.database) as session:
            session.execute_write(lambda tx: tx.run(CYPHER_SET_STATUS, sid=signal_id, status=status, approved=approved).consume())
