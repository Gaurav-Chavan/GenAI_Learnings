"""Load the org knowledge graph into Neo4j (idempotent - safe to re-run).

    uv run python -m orggraph_rag.ingest              # build / update the graph, then verify
    uv run python -m orggraph_rag.ingest --verify     # only print the verification report
    uv run python -m orggraph_rag.ingest --reset      # delete OUR nodes + indexes, then rebuild
    uv run python -m orggraph_rag.ingest --clear-db   # delete EVERY node in the database, then rebuild

Graph layout
  - Every org node has the shared label :Entity plus its kind label (:Person, :Project, ...),
    a unique `id`, a display `name`, a text `description` and its `embedding`.
  - Each Document is split into (:DocChunk)-[:PART_OF]->(:Document) with embeddings.
    (DocChunk, not Chunk, so it never mixes with the tutorial's :Chunk nodes/index.)
  - Indexes: entity_embeddings + chunk_embeddings (vector), entity_names (full-text).
"""

import argparse
import sys
from collections import defaultdict
from datetime import date

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import get_driver, get_embeddings, settings
from .data import mock_data_ext as data
from .verbalize import display_name, node_text

KINDS = sorted({kind for src, _, dst in data.SCHEMA for kind in (src, dst)})
REL_TYPES = sorted({rel for _, rel, _ in data.SCHEMA})
DATE_FIELDS = ("started", "ended", "date")

ENTITY_VECTOR_INDEX = "entity_embeddings"
CHUNK_VECTOR_INDEX = "chunk_embeddings"
ENTITY_FULLTEXT_INDEX = "entity_names"
OUR_INDEXES = (ENTITY_VECTOR_INDEX, CHUNK_VECTOR_INDEX, ENTITY_FULLTEXT_INDEX)

# Documents are 2-4 sentences, so each one stays a single chunk; longer docs would split.
text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)


def run(query: str, **params):
    return get_driver().execute_query(query, params, database_=settings.neo4j_database).records


# ---------------------------------------------------------------- safety checks

def foreign_labels() -> dict:
    """Labels of nodes in the database that this project did not create."""
    records = run("""
        MATCH (n) WHERE NOT n:Entity AND NOT n:DocChunk
        UNWIND labels(n) AS label
        RETURN label, count(*) AS count
    """)
    return {r["label"]: r["count"] for r in records}


def clear_database():
    # CALL ... IN TRANSACTIONS needs an auto-commit transaction, hence session.run.
    with get_driver().session(database=settings.neo4j_database) as session:
        session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS").consume()
    print("Deleted every node in the database.")


def reset_project_data():
    run("MATCH (n) WHERE n:Entity OR n:DocChunk DETACH DELETE n")
    for name in OUR_INDEXES:
        run(f"DROP INDEX {name} IF EXISTS")
    print("Deleted this project's nodes and indexes.")


# ---------------------------------------------------------------- schema

def setup_schema():
    run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE")
    run("CREATE CONSTRAINT doc_chunk_id IF NOT EXISTS FOR (c:DocChunk) REQUIRE c.id IS UNIQUE")
    run(f"CREATE FULLTEXT INDEX {ENTITY_FULLTEXT_INDEX} IF NOT EXISTS FOR (n:Entity) ON EACH [n.name]")
    for index, label in [(ENTITY_VECTOR_INDEX, "Entity"), (CHUNK_VECTOR_INDEX, "DocChunk")]:
        run(f"""
            CREATE VECTOR INDEX {index} IF NOT EXISTS
            FOR (n:{label}) ON (n.embedding)
            OPTIONS {{ indexConfig: {{
                `vector.dimensions`: {settings.embedding_dim},
                `vector.similarity_function`: 'cosine'
            }} }}
        """)
    print("Constraints and indexes are set.")


# ---------------------------------------------------------------- ingestion

def _node_props(node: dict) -> dict:
    props = {k: v for k, v in node.items() if k != "kind" and v is not None}
    for field in DATE_FIELDS:
        if field in props:
            props[field] = date.fromisoformat(props[field])
    props["name"] = display_name(node)
    props["description"] = node_text(node)
    return props


def ingest_nodes(embeddings):
    nodes = data.all_nodes()
    vectors = embeddings.embed_documents([node_text(n) for n in nodes])

    by_kind = defaultdict(list)
    for node, vector in zip(nodes, vectors):
        by_kind[node["kind"]].append({"id": node["id"], "props": _node_props(node), "embedding": vector})

    for kind, rows in by_kind.items():
        assert kind in KINDS, f"Unknown kind {kind}"
        run(f"""
            UNWIND $rows AS row
            MERGE (n:Entity {{id: row.id}})
            SET n:{kind}, n += row.props, n.embedding = row.embedding
        """, rows=rows)
    print(f"Merged {len(nodes)} entity nodes.")


def ingest_relationships():
    by_type = defaultdict(list)
    for src, dst, rel, props in data.RELATIONSHIPS:
        by_type[rel].append({"src": src, "dst": dst, "props": props})

    for rel, rows in by_type.items():
        assert rel in REL_TYPES, f"Unknown relationship {rel}"
        run(f"""
            UNWIND $rows AS row
            MATCH (a:Entity {{id: row.src}}), (b:Entity {{id: row.dst}})
            MERGE (a)-[r:{rel}]->(b)
            SET r += row.props
        """, rows=rows)
    print(f"Merged {len(data.RELATIONSHIPS)} relationships.")


def ingest_chunks(embeddings):
    rows = []
    for doc in data.DOCUMENTS:
        for i, chunk in enumerate(text_splitter.split_text(doc["content"])):
            rows.append({
                "id": f"{doc['id']}#{i}",
                "doc_id": doc["id"],
                "index": i,
                # Prefix the title so a chunk is self-describing when retrieved on its own.
                "text": f"{doc['title']} ({doc['type']}): {chunk}",
            })
    vectors = embeddings.embed_documents([r["text"] for r in rows])
    for row, vector in zip(rows, vectors):
        row["embedding"] = vector

    # Replace each document's chunks wholesale so edited documents never leave stale chunks.
    run("MATCH (c:DocChunk) DETACH DELETE c")
    run("""
        UNWIND $rows AS row
        MATCH (d:Document {id: row.doc_id})
        CREATE (c:DocChunk {id: row.id, index: row.index, text: row.text, embedding: row.embedding})
        CREATE (c)-[:PART_OF]->(d)
    """, rows=rows)
    print(f"Created {len(rows)} document chunks.")


# ---------------------------------------------------------------- verification

def verify() -> bool:
    ok = True

    def check(label: str, actual, expected):
        nonlocal ok
        status = "OK" if actual == expected else "MISMATCH"
        ok &= actual == expected
        print(f"  [{status:^8}] {label}: {actual} (expected {expected})")

    print("\nNodes")
    expected_kinds = defaultdict(int)
    for n in data.all_nodes():
        expected_kinds[n["kind"]] += 1
    actual_kinds = {
        r["kind"]: r["count"]
        for r in run("MATCH (n:Entity) RETURN [l IN labels(n) WHERE l <> 'Entity'][0] AS kind, count(*) AS count")
    }
    for kind in KINDS:
        check(kind, actual_kinds.get(kind, 0), expected_kinds[kind])
    check("Entity total", sum(actual_kinds.values()), len(data.all_nodes()))
    check("DocChunk", run("MATCH (c:DocChunk)-[:PART_OF]->(:Document) RETURN count(c) AS c")[0]["c"],
          len(data.DOCUMENTS))

    print("\nRelationships")
    expected_rels = defaultdict(int)
    for _, _, rel, _ in data.RELATIONSHIPS:
        expected_rels[rel] += 1
    actual_rels = {
        r["rel"]: r["count"]
        for r in run("MATCH (:Entity)-[r]->(:Entity) RETURN type(r) AS rel, count(*) AS count")
    }
    for rel in REL_TYPES:
        check(rel, actual_rels.get(rel, 0), expected_rels[rel])
    check("Relationship total", sum(actual_rels.values()), len(data.RELATIONSHIPS))

    print("\nIndexes")
    states = {
        r["name"]: r["state"]
        for r in run("SHOW INDEXES YIELD name, state WHERE name IN $names RETURN name, state", names=list(OUR_INDEXES))
    }
    for name in OUR_INDEXES:
        check(name, states.get(name, "MISSING"), "ONLINE")

    print("\nSmoke tests")
    last_audit = run("""
        MATCH (p:Project) WHERE p.name CONTAINS 'Audit'
        WITH p ORDER BY p.ended DESC LIMIT 1
        MATCH (person:Person)-[:WORKED_ON]->(p)
        WITH p, collect(person.name) AS people
        OPTIONAL MATCH (p)-[:USED_TOOL]->(t:Tool)
        RETURN p.name AS project, people, collect(t.name) AS tools
    """)[0]
    print(f"  Last audit (graph): {last_audit['project']}")
    print(f"    people: {sorted(last_audit['people'])}")
    print(f"    tools:  {sorted(last_audit['tools'])}")

    fulltext = run(f"""
        CALL db.index.fulltext.queryNodes('{ENTITY_FULLTEXT_INDEX}', 'okta~') YIELD node, score
        RETURN node.name AS name LIMIT 3
    """)
    print(f"  Full-text 'okta~': {[r['name'] for r in fulltext]}")

    query_vector = get_embeddings().embed_query("Who collected evidence for the SOC2 audit?")
    entities = run(f"""
        CALL db.index.vector.queryNodes('{ENTITY_VECTOR_INDEX}', 3, $v) YIELD node, score
        RETURN node.name AS name, round(score, 3) AS score
    """, v=query_vector)
    chunks = run(f"""
        CALL db.index.vector.queryNodes('{CHUNK_VECTOR_INDEX}', 2, $v) YIELD node, score
        MATCH (node)-[:PART_OF]->(d:Document)
        RETURN d.name AS document, round(score, 3) AS score
    """, v=query_vector)
    print(f"  Vector 'Who collected evidence for the SOC2 audit?'")
    print(f"    entities: {[(r['name'], r['score']) for r in entities]}")
    print(f"    chunks:   {[(r['document'], r['score']) for r in chunks]}")

    print("\nVerification", "PASSED" if ok else "FAILED")
    return ok


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify", action="store_true", help="only verify the existing graph")
    group.add_argument("--reset", action="store_true", help="delete this project's nodes/indexes, then rebuild")
    group.add_argument("--clear-db", action="store_true", help="delete EVERY node in the database, then rebuild")
    args = parser.parse_args()

    if args.verify:
        return 0 if verify() else 1

    if args.clear_db:
        clear_database()
    else:
        foreign = foreign_labels()
        if foreign:
            print(
                "The database already holds nodes from another project "
                f"({', '.join(f'{label}: {count}' for label, count in foreign.items())}).\n"
                "Their labels (e.g. :Skill) and constraints collide with this graph.\n"
                "Re-run with --clear-db to delete them, or point NEO4J_* at an empty database.",
                file=sys.stderr,
            )
            return 1
        if args.reset:
            reset_project_data()

    embeddings = get_embeddings()
    setup_schema()
    ingest_nodes(embeddings)
    ingest_relationships()
    ingest_chunks(embeddings)
    run("CALL db.awaitIndexes(300)")
    return 0 if verify() else 1


if __name__ == "__main__":
    raise SystemExit(main())
