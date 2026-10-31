"""Offline checks for GraphRAG's safety guard, prompt templates and helpers (no Neo4j/OpenAI)."""

import pytest

from orggraph_rag.graph_rag import collect_ids, is_read_only, lucene_query, merge_sources, strip_fences
from orggraph_rag.generation import Source
from orggraph_rag.prompts import CYPHER_FEW_SHOTS, CYPHER_PROMPT


@pytest.mark.parametrize("cypher", [
    "MATCH (n) DETACH DELETE n",
    "CREATE (:Person {name: 'x'})",
    "MATCH (p:Person) SET p.name = 'x'",
    "MERGE (t:Tool {id: 'x'})",
    "MATCH (p) REMOVE p.name",
    "DROP INDEX entity_names",
    "LOAD CSV FROM 'http://x' AS row RETURN row",
    "CALL apoc.periodic.iterate('x', 'y', {})",
    "CALL dbms.security.listUsers()",
])
def test_write_queries_are_rejected(cypher):
    assert not is_read_only(cypher)


@pytest.mark.parametrize("cypher", [query for _, query in CYPHER_FEW_SHOTS] + [
    "MATCH (d:Document) WHERE toLower(d.content) CONTAINS 'dataset' RETURN d.id",  # 'set' inside a word
    "MATCH (p:Project) RETURN p.id ORDER BY p.started DESC LIMIT 1",
])
def test_read_queries_are_allowed(cypher):
    assert is_read_only(cypher)


def test_strip_fences():
    assert strip_fences("```cypher\nMATCH (n) RETURN n\n```") == "MATCH (n) RETURN n"
    assert strip_fences("  MATCH (n) RETURN n  ") == "MATCH (n) RETURN n"


def test_lucene_query_removes_special_characters():
    assert lucene_query('Who approved "MFA" (v3)? AND/OR: x*') == "who approved mfa v3 and or x"


def test_collect_ids_walks_nested_results():
    known = frozenset({"p_bob", "proj_audit_q3", "tool_okta"})
    rows = [{"person_id": "p_bob", "projects": [{"project_id": "proj_audit_q3", "role": "Lead"}]},
            {"tools": ["tool_okta", "Okta"], "n": 3}]
    assert collect_ids(rows, known) == ["p_bob", "proj_audit_q3", "tool_okta"]


def test_merge_sources_keeps_first_occurrence():
    merged = merge_sources([Source("a", "1")], [Source("a", "2"), Source("b", "3")])
    assert [(s.id, s.text) for s in merged] == [("a", "1"), ("b", "3")]


def test_cited_ids_handles_grouped_brackets():
    from orggraph_rag.generation import cited_ids
    text = "Grace approved it [dec_mfa_mandate, proj_zerotrust]. Eve sponsored [p_eve]. See [1]."
    assert cited_ids(text) == ["dec_mfa_mandate", "proj_zerotrust", "p_eve"]


def test_cypher_prompt_only_has_expected_variables():
    # Cypher map braces in the few-shots must be escaped, not read as template variables.
    assert set(CYPHER_PROMPT.input_variables) == {"entity_catalog", "question", "error_feedback"}
