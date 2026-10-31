"""Turn graph nodes into plain text.

`node_text` describes a node from its own properties only. It is embedded on each
:Entity node so GraphRAG can find seed nodes by meaning (entity-anchored retrieval).

`entity_cards` adds every relationship the node takes part in (both directions). These
cards are the vector RAG baseline's corpus, so the baseline sees the same facts as the
graph and the comparison tests the retrieval pattern, not access to the data.
"""

from collections import defaultdict
from typing import Any, Dict, List

# (relation, direction) -> phrase. "out" = this node is the source of the edge.
REL_PHRASES = {
    ("WORKED_ON", "out"): "Worked on",
    ("WORKED_ON", "in"): "People who worked on it",
    ("HAS_SKILL", "out"): "Skills",
    ("HAS_SKILL", "in"): "People with this skill",
    ("USED_TOOL", "out"): "Tools used",
    ("USED_TOOL", "in"): "Used in projects",
    ("AUTHORED", "out"): "Authored",
    ("AUTHORED", "in"): "Authored by",
    ("ABOUT", "out"): "About project",
    ("ABOUT", "in"): "Documents about it",
    ("DECISION_FOR", "out"): "Decision for project",
    ("DECISION_FOR", "in"): "Decisions made for it",
    ("APPROVED", "out"): "Approved decisions",
    ("APPROVED", "in"): "Approved by",
    ("MEMBER_OF", "out"): "Member of team",
    ("MEMBER_OF", "in"): "Team members",
    ("REPORTS_TO", "out"): "Reports to",
    ("REPORTS_TO", "in"): "Direct reports",
    ("BASED_ON", "out"): "Based on documents",
    ("BASED_ON", "in"): "Supports decisions",
}


def display_name(node: Dict[str, Any]) -> str:
    return node.get("name") or node.get("title") or node["id"]


def node_text(node: Dict[str, Any]) -> str:
    kind = node["kind"]
    name = display_name(node)
    if kind == "Person":
        return f"Person: {name}, {node['role']} on the {node['team']} team."
    if kind == "Project":
        ended = node.get("ended") or "ongoing"
        return (
            f"Project: {name}. Status: {node['status']}. "
            f"Started {node['started']}, ended {ended}. {node['summary']}"
        )
    if kind == "Skill":
        return f"Skill: {name} (category: {node['category']})."
    if kind == "Tool":
        return f"Tool: {name} (vendor: {node['vendor']})."
    if kind == "Document":
        return f"Document: {name} ({node['type']}). {node['content']}"
    if kind == "Decision":
        return f"Decision: {name} (decided {node['date']}). Rationale: {node['rationale']}"
    if kind == "Team":
        return f"Team: {name}."
    raise ValueError(f"Unknown node kind: {kind}")


def _describe_props(props: Dict[str, Any]) -> str:
    return f" ({', '.join(f'{k}: {v}' for k, v in props.items())})" if props else ""


def entity_cards(nodes: List[Dict[str, Any]], relationships: List[tuple]) -> Dict[str, str]:
    """One self-contained text card per node: its own description + all its relationships."""
    by_id = {n["id"]: n for n in nodes}
    lines: Dict[str, Dict[tuple, List[str]]] = defaultdict(lambda: defaultdict(list))
    for src, dst, rel, props in relationships:
        lines[src][(rel, "out")].append(display_name(by_id[dst]) + _describe_props(props))
        lines[dst][(rel, "in")].append(display_name(by_id[src]) + _describe_props(props))

    cards = {}
    for node in nodes:
        parts = [node_text(node)]
        for key in REL_PHRASES:  # fixed order keeps cards stable across runs
            if key in lines[node["id"]]:
                parts.append(f"{REL_PHRASES[key]}: {'; '.join(lines[node['id']][key])}.")
        cards[node["id"]] = "\n".join(parts)
    return cards
