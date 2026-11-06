"""The ToolBox: fixed, read-only, validated tools over the silver/gold tables and the Neo4j graph.

Safety rules (each one is tested):
  * Read-only: DuckDB connections are opened read-only; Neo4j sessions use READ access; every Cypher text is fixed in this
    file (agents pass values, never queries) and contains no write clause.
  * Validated arguments: IDs and dates must match strict patterns; unknown arguments are rejected; limits are clamped.
  * Point in time: every tool answers "as of" a date (default: the dataset date) and never returns anything later.
  * No personal data, no labels: tools only read silver and gold, which hold neither.
  * Evidence trail: every ID a tool returns is remembered (``returned_ids``), so a verifier can check that a cited
    evidence ID was really returned by a tool.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import duckdb

from ..data.config import ROOT, load_config, paths, when

MAX_LIMIT = 100
ID_PATTERNS = {
    "customer_id": (r"^CUST-\d{6}$", "CUST-000123"),
    "claim_id": (r"^CLM-\d{6}$", "CLM-000123"),
    "policy_id": (r"^POL-\d{6}$", "POL-000123"),
    "provider_id": (r"^PRV-\d{3}$", "PRV-012"),
    "token": (r"^(PH|AD|BK|VH|NM)-[0-9A-F]{8}$", "VH-1A2B3C4D"),
    "procedure_code": (r"^P\d{2}$", "P04"),
}


class ToolError(ValueError):
    """A tool call was rejected (bad argument, unknown tool, unknown record). The message is safe to show an agent."""


@dataclass(frozen=True)
class Param:
    type: str  # an ID_PATTERNS key, or date | int | text | enum
    description: str = ""
    required: bool = False
    default: Any = None
    minimum: int = 1
    maximum: int = MAX_LIMIT
    options: tuple = ()
    max_len: int = 200

    def check(self, name: str, value: Any) -> Any:
        if self.type in ID_PATTERNS:
            pattern, example = ID_PATTERNS[self.type]
            if not isinstance(value, str) or not re.match(pattern, value):
                raise ToolError(f"'{name}' must look like {example}")
            return value
        if self.type == "date":
            try:
                return value if isinstance(value, date) else date.fromisoformat(str(value))
            except ValueError:
                raise ToolError(f"'{name}' must be an ISO date such as 2026-08-31") from None
        if self.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolError(f"'{name}' must be a whole number")
            return max(self.minimum, min(self.maximum, value))  # clamped: results are always record-limited
        if self.type == "text":
            if not isinstance(value, str) or not value.strip() or len(value) > self.max_len:
                raise ToolError(f"'{name}' must be non-empty text of at most {self.max_len} characters")
            return value.strip()
        if self.type == "enum":
            if value not in self.options:
                raise ToolError(f"'{name}' must be one of {list(self.options)}")
            return value
        raise ToolError(f"unsupported parameter type {self.type}")

    def schema(self) -> dict:
        if self.type in ID_PATTERNS:
            out: dict = {"type": "string", "pattern": ID_PATTERNS[self.type][0]}
        elif self.type == "date":
            out = {"type": "string", "format": "date"}
        elif self.type == "int":
            out = {"type": "integer", "minimum": self.minimum, "maximum": self.maximum}
        elif self.type == "enum":
            out = {"type": "string", "enum": list(self.options)}
        else:
            out = {"type": "string", "maxLength": self.max_len}
        out["description"] = self.description
        return out


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, Param]
    fn: Callable = field(repr=False, default=None)


def _clean(x: Any) -> Any:
    """Make query results JSON-friendly (dates become ISO strings)."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, date):
        return x.isoformat()
    if hasattr(x, "iso_format"):  # neo4j.time.Date
        return x.iso_format()
    return x


AS_OF = Param("date", "Only use information known on or before this date (default: the dataset date). For a claim, pass its filing date.")
LIMIT = Param("int", "Maximum number of records to return", default=25)

# ---- fixed Cypher (read-only; values are passed as parameters) -------------------------------------------------------
CYPHER_HOUSEHOLD = """
MATCH (p:Person {customer_id: $cid})-[u1:USES]->(:Contact {kind: 'address'})<-[u2:USES]-(o:Person)
WHERE o <> p AND u1.first_seen <= $as_of AND u2.first_seen <= $as_of
RETURN collect(DISTINCT o.customer_id) AS household
"""
CYPHER_SHARED_CONTACTS = """
MATCH (p:Person {customer_id: $cid})-[u1:USES]->(k)
WHERE (k:Contact OR k:BankAccount) AND u1.first_seen <= $as_of
MATCH (k)<-[u2:USES]-(o:Person)
WHERE o <> p AND u2.first_seen <= $as_of
WITH k, collect(DISTINCT o.customer_id) AS others
RETURN labels(k)[0] AS node_type, coalesce(k.kind, 'bank') AS kind, k.token AS token, others
ORDER BY size(others) DESC LIMIT $limit
"""
CYPHER_SHARED_PAYOUTS = """
MATCH (p:Person {customer_id: $cid})-[:HOLDS]->(:Policy)-[:HAS_CLAIM]->(c:Claim)-[:PAID_TO]->(b:BankAccount)
WHERE c.filed_date <= $as_of
MATCH (b)<-[:PAID_TO]-(oc:Claim)<-[:HAS_CLAIM]-(:Policy)<-[:HOLDS]-(o:Person)
WHERE o <> p AND oc.filed_date <= $as_of AND oc.filed_date >= $as_of - duration({days: $window})
OPTIONAL MATCH (oc)-[:AT_PROVIDER]->(pr:Provider)
WITH b, collect(DISTINCT o.customer_id) AS others, collect(DISTINCT oc.claim_id) AS claim_ids, collect(DISTINCT pr.provider_id) AS providers
RETURN b.token AS token, others, claim_ids, providers
ORDER BY size(others) DESC LIMIT $limit
"""
CYPHER_SEARCH_DOCUMENTS = """
CALL db.index.fulltext.queryNodes('document_text', $q) YIELD node, score
WHERE node.doc_date <= $as_of
MATCH (c:Claim)-[:SUPPORTED_BY]->(node)
RETURN node.doc_id AS doc_id, node.doc_type AS doc_type, c.claim_id AS claim_id, node.doc_date AS doc_date,
       round(score, 3) AS score, node.text AS text
ORDER BY score DESC LIMIT $limit
"""
CYPHER_SEARCH_CLAIMS = """
CALL db.index.fulltext.queryNodes('claim_text', $q) YIELD node, score
WHERE node.filed_date <= $as_of
RETURN node.claim_id AS claim_id, node.line AS line, node.filed_date AS filed_date, round(score, 3) AS score, node.description AS text
ORDER BY score DESC LIMIT $limit
"""
# Knowledge retrieval (used by retrieval/hybrid.py). A precedent is an earlier claim whose outcome a reviewer confirmed BEFORE the claim being assessed was filed.
CYPHER_KNOWLEDGE = """
CALL db.index.fulltext.queryNodes('knowledge_text', $q) YIELD node, score
WHERE coalesce(node.status, 'active') = 'active' AND (node.approved_date IS NULL OR node.approved_date <= $kb_as_of)
RETURN CASE labels(node)[0] WHEN 'Pattern' THEN 'pattern' WHEN 'Clause' THEN 'clause' ELSE 'signal' END AS kind,
       coalesce(node.code, node.clause_id, node.signal_id) AS id, node.title AS title, node.text AS text, node.line AS line, round(score, 3) AS score
ORDER BY score DESC LIMIT $limit
"""
CYPHER_PRECEDENT_TEXT = """
CALL db.index.fulltext.queryNodes('claim_text', $q) YIELD node, score
WHERE node.outcome IS NOT NULL AND node.decided_date <= $as_of AND node.line = $line AND node.claim_id <> $cid
RETURN node.claim_id AS id, node.line AS line, node.description AS text, node.outcome AS outcome, node.decided_date AS decided_date, round(score, 3) AS score
ORDER BY score DESC LIMIT $limit
"""
CYPHER_PRECEDENT_GRAPH = """
MATCH (c:Claim {claim_id: $cid})
CALL {
  WITH c MATCH (c)<-[:HAS_CLAIM]-(:Policy)<-[:HOLDS]-(p:Person), (c)-[:PAID_TO]->(:BankAccount)<-[:PAID_TO]-(o:Claim)<-[:HAS_CLAIM]-(:Policy)<-[:HOLDS]-(q:Person)
  WHERE q <> p RETURN o, 'paid to the same account as another customer' AS why, 3 AS w
  UNION
  WITH c MATCH (c)-[:FOR_VEHICLE]->(:Vehicle)<-[:FOR_VEHICLE]-(o:Claim) RETURN o, 'same vehicle' AS why, 3 AS w
  UNION
  WITH c MATCH (c)-[:AT_PROVIDER]->(:Provider)<-[:AT_PROVIDER]-(o:Claim) WHERE o.procedure_code = c.procedure_code RETURN o, 'same provider and procedure' AS why, 2 AS w
  UNION
  WITH c MATCH (c)-[:AT_PROVIDER]->(:Provider)<-[:AT_PROVIDER]-(o:Claim) RETURN o, 'same provider' AS why, 1 AS w
}
WITH o, collect(DISTINCT why) AS reasons, sum(w) AS weight
WHERE o.claim_id <> $cid AND o.outcome IS NOT NULL AND o.decided_date <= $as_of
RETURN o.claim_id AS id, o.line AS line, o.description AS text, o.outcome AS outcome, o.decided_date AS decided_date, reasons, weight
ORDER BY weight DESC, o.decided_date DESC LIMIT $limit
"""
# The provider's track record: how many of its claims decided in the last 24 months a reviewer confirmed as fraud.
CYPHER_PROVIDER_HISTORY = """
MATCH (c:Claim {claim_id: $cid})-[:AT_PROVIDER]->(p:Provider)<-[:AT_PROVIDER]-(o:Claim)
WHERE o.claim_id <> $cid AND o.outcome IS NOT NULL AND o.decided_date <= $as_of AND o.decided_date >= $as_of - duration({days: 730})
RETURN p.provider_id AS provider_id, p.name AS name, count(o) AS decided, sum(CASE WHEN o.outcome = 'confirmed_fraud' THEN 1 ELSE 0 END) AS confirmed_fraud
"""
ALL_CYPHER = [CYPHER_HOUSEHOLD, CYPHER_SHARED_CONTACTS, CYPHER_SHARED_PAYOUTS, CYPHER_SEARCH_DOCUMENTS, CYPHER_SEARCH_CLAIMS,
              CYPHER_KNOWLEDGE, CYPHER_PRECEDENT_TEXT, CYPHER_PRECEDENT_GRAPH, CYPHER_PROVIDER_HISTORY]


class ToolBox:
    def __init__(self, cfg: dict | None = None, graph: bool = True):
        self.cfg = cfg or load_config()
        p = paths(self.cfg)
        self.silver = duckdb.connect(str(p["silver"]), read_only=True)
        self.gold = duckdb.connect(str(p["gold"]), read_only=True)
        self.today = when(self.cfg, "dataset_today")
        self.graph_enabled, self._driver, self._database = graph, None, None
        self.calls: list[dict] = []
        self.returned_ids: set[str] = set()
        self.retriever = None  # built on first use; configure_retrieval() changes the mode or attaches a reranker
        self.retrieval_mode, self.reranker = "hybrid", None
        # the knowledge snapshot date: reviewer-approved signals are visible once approved on or before this date, whatever the claim's own filing date
        # (precedents stay point-in-time per claim). A run that needs a frozen knowledge base sets this to the snapshot date.
        self.kb_as_of = self.today
        self.tools = self._register()

    def configure_retrieval(self, mode: str | None, reranker=None) -> None:
        """mode None removes the knowledge tool entirely (the 'no retrieval' ablation); otherwise hybrid | bm25 | graph, with an optional reranker."""
        self.retriever = None
        self.retrieval_mode, self.reranker = mode or "hybrid", reranker
        self.tools = self._register()
        if mode is None:
            self.tools.pop("search_knowledge")

    # ------------------------------------------------------------------ plumbing
    def close(self) -> None:
        self.silver.close()
        self.gold.close()
        if self._driver:
            self._driver.close()

    def driver(self):
        if not self.graph_enabled:
            raise ToolError("the graph is not available in this session")
        if self._driver is None:
            from ..data.graph_conn import connect
            self._driver, self._database = connect(retries=3, wait=5, log=lambda *a, **k: None)
        return self._driver

    def _read_graph(self, cypher: str, **params) -> list[dict]:
        from neo4j import READ_ACCESS
        driver = self.driver()
        with driver.session(database=self._database, default_access_mode=READ_ACCESS) as session:
            return session.execute_read(lambda tx: [r.data() for r in tx.run(cypher, **params)])

    @staticmethod
    def _rows(con, sql: str, *params) -> list[dict]:
        cur = con.execute(sql, list(params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def _result(self, tool: str, args: dict, records: list, ids: list[str], limit: int | None = None) -> dict:
        truncated = bool(limit is not None and len(records) > limit)
        records = _clean(records[:limit] if limit is not None else records)
        return {"tool": tool, "args": _clean(args), "count": len(records), "truncated": truncated, "records": records,
                "evidence_ids": sorted({i for i in ids if i})}

    def _register(self) -> dict[str, Tool]:
        T = Tool
        tools = [
            T("get_claim", "Details of one claim: amounts, dates, provider, the documents on file (types only), and the scrubbed description.",
              {"claim_id": Param("claim_id", "Claim to look up", True)}, self.get_claim),
            T("get_claim_documents", "The text of the documents on file for one claim (bill, discharge summary, death certificate and so on), as scrubbed text. "
              "Read these when a finding might be explained, or contradicted, by what the documents say.",
              {"claim_id": Param("claim_id", "Claim whose documents to read", True)}, self.get_claim_documents),
            T("get_claim_facts", "The prepared fact sheet for a claim to assess: policy age, prior claims, bill vs peers, duplicates, area risk, "
              "documents on file, and other signals, all computed as of the filing date. Empty fields mean 'not applicable'.",
              {"claim_id": Param("claim_id", "Claim to assess", True)}, self.get_claim_facts),
            T("get_area_signal", "Whether the claim comes from an area with a high rate of reviewer-confirmed fraud, with a plain-words reason.",
              {"claim_id": Param("claim_id", "Claim to assess", True)}, self.get_area_signal),
            T("get_customer_summary", "Profile and history summary of a customer (coarse bands only, never identity).",
              {"customer_id": Param("customer_id", "Customer to look up", True)}, self.get_customer_summary),
            T("get_policy", "Details of one policy: cover, dates, status, nominee relation and declared conditions.",
              {"policy_id": Param("policy_id", "Policy to look up", True)}, self.get_policy),
            T("get_person_timeline", "A customer's events (policy changes, nominee/phone/address/bank changes, claims), newest first.",
              {"customer_id": Param("customer_id", "Customer", True), "as_of": AS_OF, "limit": LIMIT}, self.get_person_timeline),
            T("find_similar_claims", "Earlier claims that look like this one: same invoice, near-duplicate treatment, same vehicle and incident date, "
              "or paid to the same account for another customer. Each match lists why.",
              {"claim_id": Param("claim_id", "Claim to compare", True), "limit": Param("int", "Maximum matches", default=10)}, self.find_similar_claims),
            T("get_provider_stats", "A hospital or garage: registration status, recent volume, and typical bill for a procedure compared with peers.",
              {"provider_id": Param("provider_id", "Provider", True), "procedure_code": Param("procedure_code", "Procedure to compare, e.g. P04"),
               "as_of": AS_OF}, self.get_provider_stats),
            T("get_vehicle_inspections", "Inspections and repairs recorded for a vehicle (by its token), newest first.",
              {"vehicle_token": Param("token", "Vehicle token such as VH-1A2B3C4D", True), "as_of": AS_OF, "limit": LIMIT}, self.get_vehicle_inspections),
            T("find_shared_entities", "Other customers who share this customer's phone, address or bank-account token, and other customers whose "
              "claims were paid to the same account recently. Household members (same address) are marked.",
              {"customer_id": Param("customer_id", "Customer", True), "as_of": AS_OF,
               "window_days": Param("int", "Look-back window for shared payouts", default=90, minimum=7, maximum=365)}, self.find_shared_entities),
            T("search_documents", "Keyword (BM25) search over claim documents or claim descriptions.",
              {"query": Param("text", "Words to search for", True, max_len=120),
               "source": Param("enum", "What to search", default="documents", options=("documents", "claims")), "as_of": AS_OF,
               "limit": Param("int", "Maximum hits", default=5, maximum=25)}, self.search_documents),
            T("search_knowledge", "Search our knowledge for a claim: fraud-pattern cards, policy clauses, and earlier claims whose outcome a reviewer confirmed "
              "(precedents), found by keyword and by links in the graph, best first. Everything returned is context to weigh, never proof. Only decisions made "
              "before this claim was filed are used.",
              {"claim_id": Param("claim_id", "The claim being assessed", True),
               "query": Param("text", "What you want to know, in plain words, e.g. 'nominee changed shortly before death'", True, max_len=160),
               "limit": Param("int", "Maximum results", default=5, maximum=10)}, self.search_knowledge),
        ]
        return {t.name: t for t in tools}

    # ------------------------------------------------------------------ public API
    def specs(self) -> list[dict]:
        """Tool definitions in the function-calling format used by LLM APIs."""
        out = []
        for t in self.tools.values():
            out.append({"type": "function", "function": {
                "name": t.name, "description": t.description,
                "parameters": {"type": "object", "additionalProperties": False,
                               "properties": {n: p.schema() for n, p in t.params.items()},
                               "required": [n for n, p in t.params.items() if p.required]}}})
        return out

    def call(self, name: str, **args) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool '{name}'; available: {sorted(self.tools)}")
        unknown = set(args) - set(tool.params)
        if unknown:
            raise ToolError(f"unknown argument(s) for {name}: {sorted(unknown)}")
        clean = {}
        for pname, p in tool.params.items():
            value = args.get(pname, p.default)
            if value is None:
                if p.required:
                    raise ToolError(f"missing required argument '{pname}'")
                clean[pname] = None
            else:
                clean[pname] = p.check(pname, value)
        t0 = time.time()
        result = tool.fn(**clean)
        self.returned_ids.update(result["evidence_ids"])
        self.calls.append({"tool": name, "args": result["args"], "count": result["count"], "ms": round((time.time() - t0) * 1000)})
        return result

    def unknown_evidence(self, ids) -> list[str]:
        """Evidence IDs that no tool call in this session has returned (a verifier drops indicators that cite them)."""
        return sorted(set(ids) - self.returned_ids)

    # ------------------------------------------------------------------ the tools
    def get_claim(self, claim_id: str) -> dict:
        rows = self._rows(self.silver, "select * from claims where claim_id = ?", claim_id)
        if not rows:
            raise ToolError(f"no claim {claim_id}")
        claim = rows[0]
        docs = self._rows(self.silver, "select doc_id, doc_type, doc_date from documents where claim_id = ? order by doc_id", claim_id)
        prov = self._rows(self.silver, "select provider_id, name, kind, district, reg_status from providers where provider_id = ?", claim["provider_id"]) if claim["provider_id"] else []
        record = {**claim, "documents": docs, "provider": prov[0] if prov else None}
        return self._result("get_claim", {"claim_id": claim_id}, [record], [claim_id] + [d["doc_id"] for d in docs])

    def get_claim_documents(self, claim_id: str) -> dict:
        if not self._rows(self.silver, "select 1 as x from claims where claim_id = ?", claim_id):
            raise ToolError(f"no claim {claim_id}")
        docs = self._rows(self.silver, "select doc_id, doc_type, doc_date, text from documents where claim_id = ? order by doc_id", claim_id)
        return self._result("get_claim_documents", {"claim_id": claim_id}, docs, [claim_id] + [d["doc_id"] for d in docs])

    def get_claim_facts(self, claim_id: str) -> dict:
        rows = self._rows(self.gold, "select * from claim_features where claim_id = ?", claim_id)
        if not rows:
            raise ToolError(f"no prepared fact sheet for {claim_id} (fact sheets exist for claims awaiting assessment)")
        facts = {k: v for k, v in rows[0].items() if v is not None}
        return self._result("get_claim_facts", {"claim_id": claim_id}, [facts], [claim_id])

    def get_area_signal(self, claim_id: str) -> dict:
        rows = self._rows(self.gold, "select claim_id, line, location_shown, area_basis, area_n, area_fraud, area_rate, area_portfolio_rate, "
                                     "area_ratio, area_is_high from claim_features where claim_id = ?", claim_id)
        if not rows:
            raise ToolError(f"no prepared fact sheet for {claim_id}")
        r = rows[0]
        if r["area_is_high"]:
            reason = (f"Claims from this area ({r['location_shown']}, {r['line']}) are confirmed as fraud about {r['area_ratio']:.1f}x more often than "
                      f"average ({r['area_rate']:.0%} vs {r['area_portfolio_rate']:.0%}; {r['area_n']} past claims, based on {r['area_basis']}).")
        else:
            reason = (f"No elevated fraud history for this area ({r['location_shown']}, {r['line']}): {r['area_rate']:.0%} vs {r['area_portfolio_rate']:.0%} "
                      f"overall, based on {r['area_n']} past claims ({r['area_basis']} level).")
        record = {"claim_id": claim_id, "is_high": bool(r["area_is_high"]), "ratio": r["area_ratio"], "rate": r["area_rate"],
                  "portfolio_rate": r["area_portfolio_rate"], "past_claims": r["area_n"], "basis": r["area_basis"],
                  "location_shown": r["location_shown"], "reason": reason}
        return self._result("get_area_signal", {"claim_id": claim_id}, [record], [claim_id])

    def get_customer_summary(self, customer_id: str) -> dict:
        rows = self._rows(self.gold, "select * from customer_360 where customer_id = ?", customer_id)
        if not rows:
            raise ToolError(f"no customer {customer_id}")
        return self._result("get_customer_summary", {"customer_id": customer_id}, [rows[0]], [customer_id])

    def get_policy(self, policy_id: str) -> dict:
        rows = self._rows(self.silver, "select policy_id, customer_id, line, start_date, end_date, sum_insured, premium, status, lapse_date, "
                                       "nominee_relation, nominee_token, vehicle_token, vehicle_segment, declared_conditions from policies where policy_id = ?", policy_id)
        if not rows:
            raise ToolError(f"no policy {policy_id}")
        return self._result("get_policy", {"policy_id": policy_id}, [rows[0]], [policy_id])

    def get_person_timeline(self, customer_id: str, as_of: date | None, limit: int) -> dict:
        as_of = as_of or self.today
        rows = self._rows(self.silver, "select event_id, event_date, event_type, policy_id, claim_id, details from events "
                                       "where customer_id = ? and event_date <= ? order by event_date desc, event_id desc limit ?",
                          customer_id, as_of, limit + 1)
        for r in rows:
            r["details"] = json.loads(r["details"])
        return self._result("get_person_timeline", {"customer_id": customer_id, "as_of": as_of, "limit": limit}, rows,
                            [r["event_id"] for r in rows[:limit]], limit)

    def find_similar_claims(self, claim_id: str, limit: int) -> dict:
        rows = self._rows(self.silver, "select * from claims where claim_id = ?", claim_id)
        if not rows:
            raise ToolError(f"no claim {claim_id}")
        c = rows[0]
        found: dict[str, dict] = {}

        def add(matches: list[dict], reason: str) -> None:
            for m in matches:
                entry = found.setdefault(m["claim_id"], {**m, "reasons": []})
                entry["reasons"].append(reason)

        cols = "claim_id, customer_id, line, filed_date, incident_date, admit_date, amount, provider_id, procedure_code, invoice_no, status"
        if c["invoice_no"]:
            add(self._rows(self.silver, f"select {cols} from claims where invoice_no = ? and claim_id <> ? and filed_date < ?",
                           c["invoice_no"], claim_id, c["filed_date"]), "same_invoice_number")
        if c["line"] == "health" and c["procedure_code"] and c["admit_date"]:
            add(self._rows(self.silver, f"select {cols} from claims where customer_id = ? and line = 'health' and procedure_code = ? and provider_id = ? "
                                        "and claim_id <> ? and filed_date < ? and admit_date is not null "
                                        "and abs(date_diff('day', admit_date, ?)) <= 3 and abs(amount * 1.0 / ? - 1) <= 0.05",
                           c["customer_id"], c["procedure_code"], c["provider_id"], claim_id, c["filed_date"], c["admit_date"], c["amount"]),
                "near_duplicate_same_customer_procedure_provider_dates_amount")
        if c["vehicle_token"] and c["incident_date"]:
            add(self._rows(self.silver, f"select {cols} from claims where vehicle_token = ? and claim_id <> ? and filed_date < ? "
                                        "and incident_date is not null and abs(date_diff('day', incident_date, ?)) <= 1",
                           c["vehicle_token"], claim_id, c["filed_date"], c["incident_date"]), "same_vehicle_same_incident_date")
        if c["payout_token"]:
            add(self._rows(self.silver, f"select {cols} from claims where payout_token = ? and customer_id <> ? and filed_date < ? "
                                        "and filed_date >= ? - INTERVAL 90 DAY", c["payout_token"], c["customer_id"], c["filed_date"], c["filed_date"]),
                "paid_to_same_account_for_another_customer_90d")
        ordered = sorted(found.values(), key=lambda m: (-len(m["reasons"]), -m["filed_date"].toordinal()))  # most reasons first, then newest
        return self._result("find_similar_claims", {"claim_id": claim_id, "limit": limit}, ordered, [m["claim_id"] for m in ordered[:limit]], limit)

    def get_provider_stats(self, provider_id: str, procedure_code: str | None, as_of: date | None) -> dict:
        as_of = as_of or self.today
        prov = self._rows(self.silver, "select provider_id, name, kind, district, reg_status from providers where provider_id = ?", provider_id)
        if not prov:
            raise ToolError(f"no provider {provider_id}")
        prov = prov[0]
        vol = self._rows(self.silver, "select count(*) n, count(distinct customer_id) customers from claims where provider_id = ? and filed_date < ? "
                                      "and filed_date >= ? - INTERVAL 90 DAY", provider_id, as_of, as_of)[0]
        record = {**prov, "claims_last_90d": vol["n"], "distinct_customers_last_90d": vol["customers"]}
        if procedure_code:
            own = self._rows(self.silver, "select count(*) n, quantile_cont(amount, 0.5) median, quantile_cont(amount, 0.9) p90 from claims "
                                          "where provider_id = ? and procedure_code = ? and filed_date < ?", provider_id, procedure_code, as_of)[0]
            peers = self._rows(self.silver, "select count(*) n, quantile_cont(c.amount, 0.5) median from claims c join providers p using(provider_id) "
                                            "where c.procedure_code = ? and p.district = ? and c.provider_id <> ? and c.filed_date < ?",
                               procedure_code, prov["district"], provider_id, as_of)[0]
            record["procedure"] = {"code": procedure_code, "provider_claims": own["n"], "provider_median": own["median"], "provider_p90": own["p90"],
                                   "peer_claims": peers["n"], "peer_median": peers["median"],
                                   "provider_vs_peer": round(own["median"] / peers["median"], 2) if own["median"] and peers["median"] else None}
        return self._result("get_provider_stats", {"provider_id": provider_id, "procedure_code": procedure_code, "as_of": as_of}, [record], [provider_id])

    def get_vehicle_inspections(self, vehicle_token: str, as_of: date | None, limit: int) -> dict:
        as_of = as_of or self.today
        rows = self._rows(self.silver, "select event_id, event_date, event_type, claim_id, details from events "
                                       "where event_type in ('vehicle_inspected', 'vehicle_repaired') "
                                       "and json_extract_string(details, '$.vehicle_token') = ? and event_date <= ? "
                                       "order by event_date desc, event_id desc limit ?", vehicle_token, as_of, limit + 1)
        for r in rows:
            r["details"] = json.loads(r["details"])
        return self._result("get_vehicle_inspections", {"vehicle_token": vehicle_token, "as_of": as_of, "limit": limit}, rows,
                            [r["event_id"] for r in rows[:limit]], limit)

    def find_shared_entities(self, customer_id: str, as_of: date | None, window_days: int) -> dict:
        as_of = as_of or self.today
        exists = self._rows(self.silver, "select 1 as x from customers where customer_id = ?", customer_id)
        if not exists:
            raise ToolError(f"no customer {customer_id}")
        household = set(self._read_graph(CYPHER_HOUSEHOLD, cid=customer_id, as_of=as_of)[0]["household"])
        contacts = self._read_graph(CYPHER_SHARED_CONTACTS, cid=customer_id, as_of=as_of, limit=MAX_LIMIT)
        payouts = self._read_graph(CYPHER_SHARED_PAYOUTS, cid=customer_id, as_of=as_of, window=window_days, limit=MAX_LIMIT)
        records, ids = [], []
        for c in contacts:
            others = c["others"]
            records.append({"type": "shared_" + c["kind"], "token": c["token"], "other_customers": others[:25], "n_other_customers": len(others),
                            "household_members": sorted(set(others) & household), "n_unrelated": len(set(others) - household)})
            ids.extend(others)
        for p in payouts:
            others = p["others"]
            records.append({"type": "shared_payout_account", "token": p["token"], "other_customers": others[:25], "n_other_customers": len(others),
                            "household_members": sorted(set(others) & household), "n_unrelated": len(set(others) - household),
                            "claim_ids": p["claim_ids"][:25], "providers": p["providers"]})
            ids.extend(p["claim_ids"])
            ids.extend(others)
        return self._result("find_shared_entities", {"customer_id": customer_id, "as_of": as_of, "window_days": window_days}, records, ids + [customer_id])

    def search_documents(self, query: str, source: str, as_of: date | None, limit: int) -> dict:
        as_of = as_of or self.today
        q = re.sub(r"[^A-Za-z0-9\s-]", " ", query)  # drop Lucene operators so a query cannot change meaning
        q = " ".join(q.split())
        if not q:
            raise ToolError("'query' must contain letters or digits")
        cypher = CYPHER_SEARCH_DOCUMENTS if source == "documents" else CYPHER_SEARCH_CLAIMS
        rows = self._read_graph(cypher, q=q, as_of=as_of, limit=limit)
        ids = [r.get("doc_id") for r in rows] + [r["claim_id"] for r in rows]
        return self._result("search_documents", {"query": q, "source": source, "as_of": as_of, "limit": limit}, rows, ids)

    def search_knowledge(self, claim_id: str, query: str, limit: int) -> dict:
        from ..retrieval import HybridRetriever  # imported here: the retrieval package itself builds on this module

        if self.retriever is None:
            self.retriever = HybridRetriever(self, self.reranker, self.retrieval_mode)
        return self.retriever.search(claim_id, query, limit)


def write_docs(out: Path | None = None) -> Path:
    box = ToolBox(graph=False)
    lines = ["# Read-only tools", "", "Generated by `claim_triage.tools.toolbox`. Every tool is read-only, validates its arguments, limits its results, "
             "answers as of a date, and never returns personal data or labels.", "",
             "| Tool | What it answers | Arguments |", "|---|---|---|"]
    for t in box.tools.values():
        args = ", ".join(f"`{n}`{' (required)' if p.required else ''}" for n, p in t.params.items())
        lines.append(f"| `{t.name}` | {t.description} | {args} |")
    box.close()
    out = out or ROOT / "docs" / "tools.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(write_docs())
