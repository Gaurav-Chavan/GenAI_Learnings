"""Offline checks on the vector baseline's corpus (entity cards) and answer helpers."""

from orggraph_rag.data import mock_data_ext as data
from orggraph_rag.generation import REFUSAL, RAGResult, Source
from orggraph_rag.verbalize import display_name, entity_cards

NODES = data.all_nodes()
BY_ID = {n["id"]: n for n in NODES}
CARDS = entity_cards(NODES, data.RELATIONSHIPS)


def test_one_card_per_node():
    assert set(CARDS) == set(BY_ID)


def test_every_relationship_appears_on_both_endpoint_cards():
    # The baseline must hold the same facts as the graph: each edge is visible from both ends.
    for src, dst, _, _ in data.RELATIONSHIPS:
        assert display_name(BY_ID[dst]) in CARDS[src], (src, dst)
        assert display_name(BY_ID[src]) in CARDS[dst], (src, dst)


def test_edge_properties_are_kept():
    assert "Q3 2025 SOC2 Compliance Audit (role: Evidence Engineer)" in CARDS["p_bob"]
    assert "Alice Chen (level: Expert)" in CARDS["skill_soc2"]


def test_document_cards_include_full_content():
    for doc in data.DOCUMENTS:
        assert doc["content"] in CARDS[doc["id"]]


def test_card_example_last_audit_project():
    card = CARDS["proj_audit_q3"]
    for expected in ["Alice Chen (role: Lead)", "Frank Lee", "Bob Martinez", "Drata", "Okta",
                     "AWS Audit Manager", "Replace Vanta with Drata"]:
        assert expected in card


def test_refused_flag():
    result = RAGResult("vector", "q", REFUSAL, [Source("p_bob", "x")], [], 0.1)
    assert result.refused and result.to_dict()["refused"]
