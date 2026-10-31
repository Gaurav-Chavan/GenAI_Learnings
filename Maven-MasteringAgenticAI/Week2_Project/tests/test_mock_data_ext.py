"""Offline checks on the extended dataset — no Neo4j or OpenAI needed.

Besides integrity, these pin down the expected answers the eval relies on, so a data
edit that silently changes a gold answer fails here first.
"""

from collections import Counter

from orggraph_rag.data import mock_data, mock_data_ext as data
from orggraph_rag.verbalize import node_text

NODES = {n["id"]: n for n in data.all_nodes()}


def names(ids):
    return {NODES[i].get("name") or NODES[i].get("title") for i in ids}


def targets(src, rel):
    return [d for s, d, r, _ in data.RELATIONSHIPS if s == src and r == rel]


def sources(dst, rel):
    return [s for s, d, r, _ in data.RELATIONSHIPS if d == dst and r == rel]


# ---------------------------------------------------------------- integrity

def test_extension_keeps_every_original_node_and_edge():
    assert {n["id"] for n in mock_data.all_nodes()} <= NODES.keys()
    assert all(r in data.RELATIONSHIPS for r in mock_data.RELATIONSHIPS)


def test_ids_are_unique():
    ids = [n["id"] for n in data.all_nodes()]
    assert len(ids) == len(set(ids))


def test_size_meets_handout_minimum():
    assert len(NODES) == 44
    assert len(data.RELATIONSHIPS) == 75


def test_every_edge_matches_the_schema():
    allowed = set(data.SCHEMA)
    for src, dst, rel, _ in data.RELATIONSHIPS:
        assert src in NODES and dst in NODES, (src, dst, rel)
        assert (NODES[src]["kind"], rel, NODES[dst]["kind"]) in allowed, (src, rel, dst)


def test_no_duplicate_edges():
    counts = Counter((s, d, r) for s, d, r, _ in data.RELATIONSHIPS)
    assert [k for k, c in counts.items() if c > 1] == []


def test_every_person_is_in_exactly_one_team():
    for person in data.PEOPLE:
        assert len(targets(person["id"], "MEMBER_OF")) == 1


def test_node_text_covers_every_node():
    for node in data.all_nodes():
        text = node_text(node)
        assert (node.get("name") or node.get("title")) in text


# ---------------------------------------------------------------- gold answers

def test_q1_last_audit_people_and_tools():
    audits = [p for p in data.PROJECTS if "Audit" in p["name"]]
    last = max(audits, key=lambda p: p["ended"])
    assert last["id"] == "proj_audit_q3"
    assert names(sources(last["id"], "WORKED_ON")) == {"Alice Chen", "Frank Lee", "Bob Martinez"}
    assert names(targets(last["id"], "USED_TOOL")) == {"AWS Audit Manager", "Drata", "Okta"}


def test_q2_pricing_decision_approvers():
    decisions = sources("proj_pricing", "DECISION_FOR")
    assert decisions == ["dec_tiered_pricing"]
    assert names(sources("dec_tiered_pricing", "APPROVED")) == {"Eve Johnson", "Carol Wu"}


def test_q3_people_on_multiple_projects():
    counts = Counter(s for s, _, r, _ in data.RELATIONSHIPS if r == "WORKED_ON")
    multi = {NODES[p]["name"]: c for p, c in counts.items() if c > 1}
    assert multi == {"Bob Martinez": 3, "David Kim": 2, "Frank Lee": 2, "Alice Chen": 2}


def test_q4_tools_shared_across_projects():
    counts = Counter(d for _, d, r, _ in data.RELATIONSHIPS if r == "USED_TOOL")
    assert names([t for t, c in counts.items() if c > 1]) == {"Snowflake", "Okta"}


def test_q6_q14_grace_approvals_and_bob_manager():
    assert targets("p_bob", "REPORTS_TO") == ["p_grace"]
    assert names(targets("p_grace", "APPROVED")) == {
        "Mandate MFA for all production system access",
        "Standardize on PyTorch for ML training pipelines",
        "Replace Vanta with Drata for audit evidence automation",
    }


def test_q9_soc2_skill_holders():
    assert names(sources("skill_soc2", "HAS_SKILL")) == {"Alice Chen", "Frank Lee", "Henry Okafor"}


def test_q13_tool_before_drata():
    assert names(targets("proj_audit_2024", "USED_TOOL")) == {"Vanta"}
    assert targets("dec_switch_drata", "BASED_ON") == ["doc_audit_2024"]
