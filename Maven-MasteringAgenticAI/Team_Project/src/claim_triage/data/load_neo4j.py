"""Load the silver and gold layers into Neo4j as the Customer-360 graph, with BM25 (full-text) indexes.

    uv run python -m claim_triage.data.load_neo4j            # loads into an EMPTY database
    uv run python -m claim_triage.data.load_neo4j --clear    # deletes every node in the target database first

Graph (privacy: only reviewer-safe values; tokens instead of phones, accounts, addresses and vehicle registrations):
  (Person)-[:HOLDS]->(Policy)-[:HAS_CLAIM]->(Claim)-[:AT_PROVIDER]->(Provider)
  (Claim)-[:SUPPORTED_BY]->(Document)   (Claim)-[:FOR_CONDITION]->(Condition)   (Claim)-[:PAID_TO]->(BankAccount)
  (Claim)-[:FOR_VEHICLE]->(Vehicle)     (Policy)-[:COVERS]->(Vehicle)           (Policy)-[:HAS_NOMINEE {relation}]->(Nominee)
  (Person)-[:USES]->(Contact | BankAccount)                                     (Person)-[:LIVES_IN]->(District)
"""
from __future__ import annotations

import argparse
import time

import duckdb

from .config import ROOT, load_config, paths
from .graph_conn import connect
from .privacy_split import fetch
from .quality import canary

BATCH = 1000
CONSTRAINTS = [
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (n:Person) REQUIRE n.customer_id IS UNIQUE",
    "CREATE CONSTRAINT policy_id IF NOT EXISTS FOR (n:Policy) REQUIRE n.policy_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.claim_id IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.doc_id IS UNIQUE",
    "CREATE CONSTRAINT provider_id IF NOT EXISTS FOR (n:Provider) REQUIRE n.provider_id IS UNIQUE",
    "CREATE CONSTRAINT contact_token IF NOT EXISTS FOR (n:Contact) REQUIRE n.token IS UNIQUE",
    "CREATE CONSTRAINT bank_token IF NOT EXISTS FOR (n:BankAccount) REQUIRE n.token IS UNIQUE",
    "CREATE CONSTRAINT vehicle_token IF NOT EXISTS FOR (n:Vehicle) REQUIRE n.token IS UNIQUE",
    "CREATE CONSTRAINT nominee_token IF NOT EXISTS FOR (n:Nominee) REQUIRE n.token IS UNIQUE",
    "CREATE CONSTRAINT condition_name IF NOT EXISTS FOR (n:Condition) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT district_name IF NOT EXISTS FOR (n:District) REQUIRE n.name IS UNIQUE",
]
FULLTEXT = [
    "CREATE FULLTEXT INDEX document_text IF NOT EXISTS FOR (n:Document) ON EACH [n.text]",
    "CREATE FULLTEXT INDEX claim_text IF NOT EXISTS FOR (n:Claim) ON EACH [n.description]",
    "CREATE FULLTEXT INDEX provider_names IF NOT EXISTS FOR (n:Provider) ON EACH [n.name]",
]
KNOWLEDGE_CONSTRAINTS = [
    "CREATE CONSTRAINT pattern_code IF NOT EXISTS FOR (n:Pattern) REQUIRE n.code IS UNIQUE",
    "CREATE CONSTRAINT clause_id IF NOT EXISTS FOR (n:Clause) REQUIRE n.clause_id IS UNIQUE",
    "CREATE CONSTRAINT signal_id IF NOT EXISTS FOR (n:ApprovedSignal) REQUIRE n.signal_id IS UNIQUE",
]
KNOWLEDGE_FULLTEXT = "CREATE FULLTEXT INDEX knowledge_text IF NOT EXISTS FOR (n:Pattern|Clause|ApprovedSignal) ON EACH [n.text]"
Q_PATTERN = "UNWIND $rows AS r MERGE (p:Pattern {code: r.code}) SET p += r"
Q_CLAUSE = "UNWIND $rows AS r MERGE (c:Clause {clause_id: r.clause_id}) SET c += r"
Q_PERSON = "UNWIND $rows AS r MERGE (p:Person {customer_id: r.customer_id}) SET p += r"
Q_PROVIDER = "UNWIND $rows AS r MERGE (p:Provider {provider_id: r.provider_id}) SET p += r"
Q_DISTRICT = "UNWIND $rows AS r MERGE (d:District {name: r.name}) SET d += r"
Q_POLICY = """
UNWIND $rows AS r
MERGE (pol:Policy {policy_id: r.policy_id}) SET pol += r.props
WITH pol, r
MATCH (p:Person {customer_id: r.customer_id}) MERGE (p)-[:HOLDS]->(pol)
FOREACH (t IN CASE WHEN r.vehicle_token IS NULL THEN [] ELSE [r.vehicle_token] END |
  MERGE (v:Vehicle {token: t}) SET v.segment = r.vehicle_segment MERGE (pol)-[:COVERS]->(v))
FOREACH (t IN CASE WHEN r.nominee_token IS NULL THEN [] ELSE [r.nominee_token] END |
  MERGE (n:Nominee {token: t}) MERGE (pol)-[:HAS_NOMINEE {relation: r.nominee_relation}]->(n))
"""
Q_CLAIM = """
UNWIND $rows AS r
MERGE (c:Claim {claim_id: r.claim_id}) SET c += r.props
WITH c, r
MATCH (pol:Policy {policy_id: r.policy_id}) MERGE (pol)-[:HAS_CLAIM]->(c)
WITH c, r
OPTIONAL MATCH (pr:Provider {provider_id: r.provider_id})
FOREACH (_ IN CASE WHEN pr IS NULL THEN [] ELSE [1] END | MERGE (c)-[:AT_PROVIDER]->(pr))
FOREACH (t IN CASE WHEN r.payout_token IS NULL THEN [] ELSE [r.payout_token] END |
  MERGE (b:BankAccount {token: t}) MERGE (c)-[:PAID_TO]->(b))
FOREACH (t IN CASE WHEN r.vehicle_token IS NULL THEN [] ELSE [r.vehicle_token] END |
  MERGE (v:Vehicle {token: t}) MERGE (c)-[:FOR_VEHICLE]->(v))
FOREACH (n IN CASE WHEN r.condition IS NULL THEN [] ELSE [r.condition] END |
  MERGE (k:Condition {name: n}) MERGE (c)-[:FOR_CONDITION]->(k))
"""
Q_DOCUMENT = """
UNWIND $rows AS r
MERGE (d:Document {doc_id: r.doc_id}) SET d += r.props
WITH d, r
MATCH (c:Claim {claim_id: r.claim_id}) MERGE (c)-[:SUPPORTED_BY]->(d)
"""
Q_CONTACT = """
UNWIND $rows AS r
MERGE (k:Contact {token: r.token}) SET k.kind = r.token_type
WITH k, r
MATCH (p:Person {customer_id: r.customer_id}) MERGE (p)-[u:USES]->(k) ON CREATE SET u.first_seen = r.first_seen, u.source = r.source
"""
Q_BANK = """
UNWIND $rows AS r
MERGE (b:BankAccount {token: r.token})
WITH b, r
MATCH (p:Person {customer_id: r.customer_id}) MERGE (p)-[u:USES]->(b) ON CREATE SET u.first_seen = r.first_seen, u.source = r.source
"""
Q_LIVES_IN = "MATCH (p:Person) MATCH (d:District {name: p.district}) MERGE (p)-[:LIVES_IN]->(d)"


def run(session, query: str, rows: list[dict], size: int = BATCH) -> None:
    for i in range(0, len(rows), size):
        chunk = rows[i:i + size]
        session.execute_write(lambda tx, chunk=chunk: tx.run(query, rows=chunk).consume())


def build_rows(cfg: dict) -> dict[str, list[dict]]:
    p = paths(cfg)
    silver, gold = duckdb.connect(str(p["silver"]), read_only=True), duckdb.connect(str(p["gold"]), read_only=True)
    persons = fetch(gold, "customer_360")
    policies, claims, docs = fetch(silver, "policies"), fetch(silver, "claims"), fetch(silver, "documents")
    providers, tokens, areas = fetch(silver, "providers"), fetch(silver, "entity_tokens"), fetch(silver, "areas")
    district_rates = fetch(gold, "area_risk_district")
    silver.close()
    gold.close()
    state_of = {a["district"]: a["state"] for a in areas}
    districts: dict[str, dict] = {d: {"name": d, "state": s} for d, s in state_of.items()}
    for r in district_rates:
        d = districts[r["district"]]
        d[f"{r['line']}_claims"], d[f"{r['line']}_confirmed_fraud"], d[f"{r['line']}_fraud_rate"] = r["n_claims"], r["confirmed_fraud"], r["rate"]
    pol_rows = [dict(policy_id=x["policy_id"], customer_id=x["customer_id"], vehicle_token=x["vehicle_token"], vehicle_segment=x["vehicle_segment"],
                     nominee_token=x["nominee_token"], nominee_relation=x["nominee_relation"],
                     props={k: x[k] for k in ("line", "start_date", "end_date", "sum_insured", "premium", "status", "lapse_date", "declared_conditions")})
                for x in policies]
    claim_props = ("case_id", "line", "filed_date", "incident_date", "admit_date", "discharge_date", "procedure_code", "condition_tag",
                   "damage_items", "amount", "invoice_no", "incident_district", "status", "decided_date", "outcome", "cert_date", "description", "source")
    claim_rows = [dict(claim_id=x["claim_id"], policy_id=x["policy_id"], provider_id=x["provider_id"], payout_token=x["payout_token"],
                       vehicle_token=x["vehicle_token"], condition=x["condition_tag"], props={k: x[k] for k in claim_props}) for x in claims]
    doc_rows = [dict(doc_id=x["doc_id"], claim_id=x["claim_id"], props={k: x[k] for k in ("doc_type", "doc_date", "text")}) for x in docs]
    return dict(persons=persons, providers=providers, districts=list(districts.values()), policies=pol_rows, claims=claim_rows, documents=doc_rows,
                contacts=[t for t in tokens if t["token_type"] in ("phone", "address")], banks=[t for t in tokens if t["token_type"] == "bank"])


def knowledge_rows() -> dict[str, list[dict]]:
    """Pattern cards and policy clauses as graph rows. ApprovedSignal nodes are written later by the Knowledge Base tab (pending, then active)."""
    import yaml

    folder = ROOT / "knowledge"
    with open(folder / "fraud_patterns.yaml", encoding="utf-8") as fh:
        cards = yaml.safe_load(fh)
    with open(folder / "policy_clauses.yaml", encoding="utf-8") as fh:
        clauses = yaml.safe_load(fh)
    patterns = [dict(code=code, title=c["name"], status="active",
                     text=" ".join([c["name"] + ".", c["description"], "Check: " + "; ".join(c.get("investigate", [])) + ".",
                                    "Innocent explanations: " + " ".join(c.get("legitimate_exceptions", []))]))
                for code, c in cards.items()]
    clause_rows = [dict(clause_id=cid, title=c["title"], line=c["line"], status="active", text=c["title"] + ". " + c["text"]) for cid, c in clauses.items()]
    return dict(patterns=patterns, clauses=clause_rows)


def load_knowledge(session) -> dict[str, int]:
    """Add (or refresh) the knowledge nodes and their BM25 index without touching any claim data."""
    rows = knowledge_rows()
    for stmt in KNOWLEDGE_CONSTRAINTS:
        session.run(stmt).consume()
    run(session, Q_PATTERN, rows["patterns"])
    run(session, Q_CLAUSE, rows["clauses"])
    session.run(KNOWLEDGE_FULLTEXT).consume()
    session.run("CALL db.awaitIndexes(300)").consume()
    return {k: len(v) for k, v in rows.items()}


def graph_counts(session) -> dict:
    nodes = {r["label"]: r["n"] for r in session.run("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n")}
    rels = {r["type"]: r["n"] for r in session.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n")}
    return {"nodes": nodes, "relationships": rels}


def verify(session, cfg: dict, rows: dict) -> list[dict]:
    counts = graph_counts(session)["nodes"]
    expect = {"Person": len(rows["persons"]), "Policy": len(rows["policies"]), "Claim": len(rows["claims"]), "Document": len(rows["documents"]),
              "Provider": len(rows["providers"]), "District": len(rows["districts"])}
    res = [dict(check=f"{label} count matches the tables", ok=counts.get(label) == n, detail=f"graph {counts.get(label)} vs tables {n}")
           for label, n in expect.items()]
    pin = session.run("MATCH (p:Person) WHERE p.pincode IS NOT NULL OR p.name IS NOT NULL OR p.phone IS NOT NULL OR p.address IS NOT NULL "
                      "RETURN count(p) AS n").single()["n"]
    res.append(dict(check="no PIN code or personal name on any Person", ok=pin == 0, detail=f"{pin} offenders"))
    strings = [r["s"] for r in session.run("MATCH (n) UNWIND keys(n) AS k WITH n[k] AS v WHERE v IS NOT NULL RETURN DISTINCT toString(v) AS s")]
    leaks = canary(cfg, layers=(), extra_text="\n".join(strings))
    res.append(dict(check="canary: no vault value appears anywhere in the graph", ok=not leaks, detail=f"{len(leaks)} leaked values"))
    labels = {r["l"] for r in session.run("MATCH (n) RETURN DISTINCT labels(n)[0] AS l")}
    res.append(dict(check="no label or answer-key nodes", ok=not (labels & {"Label", "Scenario", "Answer"}), detail=", ".join(sorted(labels))))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="delete EVERY node in the target database before loading")
    ap.add_argument("--verify-only", action="store_true", help="do not load; just run the graph checks")
    ap.add_argument("--knowledge-only", action="store_true", help="only add or refresh the pattern and clause nodes; nothing is deleted or reloaded")
    args = ap.parse_args()
    cfg, t0 = load_config(), time.time()
    if args.knowledge_only:
        driver, database = connect()
        with driver.session(database=database) as session:
            print("knowledge nodes:", load_knowledge(session))
        driver.close()
        return
    rows = build_rows(cfg)
    driver, database = connect()
    if args.verify_only:
        with driver.session(database=database) as session:
            results = verify(session, cfg, rows)
        driver.close()
        for r in results:
            print(("pass  " if r["ok"] else "FAIL  ") + f"{r['check']} ({r['detail']})")
        raise SystemExit(0 if all(r["ok"] for r in results) else "graph checks failed")
    with driver.session(database=database) as session:
        existing = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        if existing and not args.clear:
            raise SystemExit(f"The target database already holds {existing} nodes. Re-run with --clear to replace them "
                             "(this deletes every node in that database).")
        if existing:
            print(f"clearing {existing} nodes ...")
            while session.run("MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c").single()["c"]:
                pass
        for stmt in CONSTRAINTS:
            session.run(stmt).consume()
        for step, query, key in [("persons", Q_PERSON, "persons"), ("providers", Q_PROVIDER, "providers"), ("districts", Q_DISTRICT, "districts"),
                                 ("policies", Q_POLICY, "policies"), ("claims", Q_CLAIM, "claims"), ("documents", Q_DOCUMENT, "documents"),
                                 ("contacts", Q_CONTACT, "contacts"), ("bank accounts", Q_BANK, "banks")]:
            run(session, query, rows[key])
            print(f"  loaded {len(rows[key]):>5} {step}")
        session.run(Q_LIVES_IN).consume()
        for stmt in FULLTEXT:
            session.run(stmt).consume()
        session.run("CALL db.awaitIndexes(300)").consume()
        print("  knowledge:", load_knowledge(session))
        counts = graph_counts(session)
        print("nodes:", counts["nodes"])
        print("relationships:", counts["relationships"])
        results = verify(session, cfg, rows)
    driver.close()
    for r in results:
        print(("pass  " if r["ok"] else "FAIL  ") + f"{r['check']} ({r['detail']})")
    if not all(r["ok"] for r in results):
        raise SystemExit("graph checks failed")
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
