"""
Moderate extension of `mock_data.py` (which stays untouched).

Adds just enough structure to make the GraphRAG vs. vector RAG comparison meaningful:
  - an older 2024 audit (so "the *last* audit" needs date ordering) with a distractor
    person (Henry) and tool (Vanta) that only belong to the older audit,
  - Team nodes + MEMBER_OF (derived from each person's `team` field),
  - REPORTS_TO reporting lines,
  - Decision -[:BASED_ON]-> Document provenance edges.
"""

import re
from typing import Any, Dict, List

from . import mock_data as base

# ----- People -----
PEOPLE = base.PEOPLE + [
    {"id": "p_henry", "name": "Henry Okafor", "role": "Compliance Analyst", "team": "Risk & Compliance"},
]

# ----- Projects -----
PROJECTS = base.PROJECTS + [
    {
        "id": "proj_audit_2024",
        "name": "Q3 2024 SOC2 Compliance Audit",
        "status": "Completed",
        "started": "2024-07-01",
        "ended": "2024-09-30",
        "summary": "Prior-year SOC2 Type II audit covering security and availability controls.",
    },
]

SKILLS = list(base.SKILLS)

# ----- Tools -----
TOOLS = base.TOOLS + [
    {"id": "tool_vanta", "name": "Vanta", "vendor": "Vanta Inc."},
]

# ----- Documents -----
DOCUMENTS = base.DOCUMENTS + [
    {
        "id": "doc_audit_2024",
        "title": "Q3 2024 SOC2 Audit Final Report",
        "type": "Audit Report",
        "content": (
            "The Q3 2024 SOC2 Type II audit concluded on 2024-09-27 with three medium-severity findings "
            "related to quarterly access reviews and gaps in change-management evidence. "
            "Evidence was collected via Vanta, with several AWS control screenshots gathered manually. "
            "The report recommends fully automating evidence collection before the next audit cycle."
        ),
    },
]

# ----- Decisions -----
DECISIONS = base.DECISIONS + [
    {
        "id": "dec_switch_drata",
        "title": "Replace Vanta with Drata for audit evidence automation",
        "date": "2025-06-10",
        "rationale": (
            "Vanta required manual evidence uploads for AWS controls, which contributed to the 2024 findings; "
            "Drata's continuous control monitoring automates evidence collection."
        ),
    },
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# ----- Teams (derived from each person's `team` field) -----
TEAMS = [
    {"id": f"team_{_slug(name)}", "name": name}
    for name in dict.fromkeys(p["team"] for p in PEOPLE)
]
_TEAM_ID = {t["name"]: t["id"] for t in TEAMS}


# ----- Relationships (edges) -----
RELATIONSHIPS: List[tuple] = base.RELATIONSHIPS + [
    # 2024 audit
    ("p_alice", "proj_audit_2024", "WORKED_ON", {"role": "Lead"}),
    ("p_henry", "proj_audit_2024", "WORKED_ON", {"role": "Evidence Analyst"}),
    ("p_henry", "skill_soc2", "HAS_SKILL", {"level": "Intermediate"}),
    ("proj_audit_2024", "tool_vanta", "USED_TOOL", {}),
    ("p_alice", "doc_audit_2024", "AUTHORED", {}),
    ("doc_audit_2024", "proj_audit_2024", "ABOUT", {}),

    # Switch from Vanta to Drata (made for the 2025 audit)
    ("dec_switch_drata", "proj_audit_q3", "DECISION_FOR", {}),
    ("p_alice", "dec_switch_drata", "APPROVED", {}),
    ("p_grace", "dec_switch_drata", "APPROVED", {}),

    # REPORTS_TO: reporting lines
    ("p_bob", "p_grace", "REPORTS_TO", {}),
    ("p_frank", "p_grace", "REPORTS_TO", {}),
    ("p_david", "p_grace", "REPORTS_TO", {}),
    ("p_carol", "p_grace", "REPORTS_TO", {}),
    ("p_henry", "p_alice", "REPORTS_TO", {}),
    ("p_alice", "p_eve", "REPORTS_TO", {}),

    # BASED_ON: decision -> supporting document
    ("dec_tiered_pricing", "doc_churn_analysis", "BASED_ON", {}),
    ("dec_tiered_pricing", "doc_pricing_memo", "BASED_ON", {}),
    ("dec_mfa_mandate", "doc_security_policy", "BASED_ON", {}),
    ("dec_switch_drata", "doc_audit_2024", "BASED_ON", {}),
] + [
    # MEMBER_OF: person -> team
    (p["id"], _TEAM_ID[p["team"]], "MEMBER_OF", {})
    for p in PEOPLE
]

# Allowed (source kind, relation, target kind) — the graph schema, used for validation
# and as the schema description given to Text2Cypher.
SCHEMA: List[tuple] = [
    ("Person", "WORKED_ON", "Project"),
    ("Person", "HAS_SKILL", "Skill"),
    ("Project", "USED_TOOL", "Tool"),
    ("Person", "AUTHORED", "Document"),
    ("Document", "ABOUT", "Project"),
    ("Decision", "DECISION_FOR", "Project"),
    ("Person", "APPROVED", "Decision"),
    ("Person", "MEMBER_OF", "Team"),
    ("Person", "REPORTS_TO", "Person"),
    ("Decision", "BASED_ON", "Document"),
]


def all_nodes() -> List[Dict[str, Any]]:
    """Return all nodes with a `kind` field attached."""
    nodes = []
    for p in PEOPLE:     nodes.append({**p, "kind": "Person"})
    for p in PROJECTS:   nodes.append({**p, "kind": "Project"})
    for s in SKILLS:     nodes.append({**s, "kind": "Skill"})
    for t in TOOLS:      nodes.append({**t, "kind": "Tool"})
    for d in DOCUMENTS:  nodes.append({**d, "kind": "Document"})
    for d in DECISIONS:  nodes.append({**d, "kind": "Decision"})
    for t in TEAMS:      nodes.append({**t, "kind": "Team"})
    return nodes


if __name__ == "__main__":
    nodes = all_nodes()
    print(f"Total nodes: {len(nodes)}")
    print(f"Total edges: {len(RELATIONSHIPS)}")
    kinds: Dict[str, int] = {}
    for n in nodes:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    for k, v in kinds.items():
        print(f"  {k}: {v}")
