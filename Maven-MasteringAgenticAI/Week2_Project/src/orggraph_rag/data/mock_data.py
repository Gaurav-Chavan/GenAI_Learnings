"""
Mock organizational knowledge: people, projects, skills, documents, decisions.

Designed so questions like:
  - "Who worked on the last compliance audit and what tools did they use?"
  - "What decisions were made about our pricing model and who approved them?"
require multi-hop relational traversal that pure semantic search struggles with.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any


# ----- People -----
PEOPLE = [
    {"id": "p_alice",  "name": "Alice Chen",     "role": "Compliance Lead",    "team": "Risk & Compliance"},
    {"id": "p_bob",    "name": "Bob Martinez",   "role": "Senior Engineer",    "team": "Platform"},
    {"id": "p_carol",  "name": "Carol Wu",       "role": "Product Manager",    "team": "Growth"},
    {"id": "p_david",  "name": "David Kim",      "role": "Data Scientist",     "team": "ML Research"},
    {"id": "p_eve",    "name": "Eve Johnson",    "role": "CFO",                "team": "Finance"},
    {"id": "p_frank",  "name": "Frank Lee",      "role": "Security Engineer",  "team": "Security"},
    {"id": "p_grace",  "name": "Grace Patel",    "role": "VP Engineering",     "team": "Engineering Leadership"},
]

# ----- Projects -----
PROJECTS = [
    {
        "id": "proj_audit_q3",
        "name": "Q3 2025 SOC2 Compliance Audit",
        "status": "Completed",
        "started": "2025-07-01",
        "ended": "2025-09-30",
        "summary": "Annual SOC2 Type II audit covering security, availability, and confidentiality controls.",
    },
    {
        "id": "proj_pricing",
        "name": "Pricing Model Redesign",
        "status": "Completed",
        "started": "2025-04-01",
        "ended": "2025-06-15",
        "summary": "Moved from flat seat-based pricing to a 3-tier usage-based pricing model.",
    },
    {
        "id": "proj_ml_recs",
        "name": "ML Recommendation Engine",
        "status": "In Progress",
        "started": "2025-08-01",
        "ended": None,
        "summary": "Personalization engine for the customer dashboard using collaborative filtering and LLM reranking.",
    },
    {
        "id": "proj_zerotrust",
        "name": "Zero-Trust Security Rollout",
        "status": "In Progress",
        "started": "2025-10-01",
        "ended": None,
        "summary": "Org-wide rollout of zero-trust networking, mandatory MFA, and device posture checks.",
    },
]

# ----- Skills / Tools -----
SKILLS = [
    {"id": "skill_soc2",       "name": "SOC2 Compliance",     "category": "Compliance"},
    {"id": "skill_python",     "name": "Python",              "category": "Programming"},
    {"id": "skill_finmodel",   "name": "Financial Modeling",  "category": "Finance"},
    {"id": "skill_ml",         "name": "Machine Learning",    "category": "Data Science"},
    {"id": "skill_pentest",    "name": "Penetration Testing", "category": "Security"},
    {"id": "skill_aws",        "name": "AWS",                 "category": "Cloud"},
]

# ----- Tools (treated as nodes too — "what tools did they use") -----
TOOLS = [
    {"id": "tool_aws_audit",   "name": "AWS Audit Manager",   "vendor": "AWS"},
    {"id": "tool_drata",       "name": "Drata",               "vendor": "Drata Inc."},
    {"id": "tool_okta",        "name": "Okta",                "vendor": "Okta"},
    {"id": "tool_snowflake",   "name": "Snowflake",           "vendor": "Snowflake"},
    {"id": "tool_pytorch",     "name": "PyTorch",             "vendor": "Meta"},
    {"id": "tool_tableau",     "name": "Tableau",             "vendor": "Salesforce"},
]

# ----- Documents -----
DOCUMENTS = [
    {
        "id": "doc_audit_report",
        "title": "Q3 2025 SOC2 Audit Final Report",
        "type": "Audit Report",
        "content": (
            "The Q3 2025 SOC2 Type II audit concluded on 2025-09-28 with zero critical findings. "
            "The audit covered logical access, change management, and incident response. "
            "Evidence collection was automated via Drata, with cloud-control attestations sourced from AWS Audit Manager. "
            "The audit team coordinated remediation of two medium-severity observations around log retention."
        ),
    },
    {
        "id": "doc_pricing_memo",
        "title": "Pricing Strategy Memo (April 2025)",
        "type": "Strategy Memo",
        "content": (
            "We recommend transitioning from flat seat-based pricing to a tiered usage-based model with Starter, Growth, "
            "and Enterprise tiers. The proposal projects a 22% lift in net revenue retention over 12 months. "
            "Approval requires CFO sign-off and product leadership endorsement."
        ),
    },
    {
        "id": "doc_ml_arch",
        "title": "ML Recommendation Engine Architecture",
        "type": "Technical Design",
        "content": (
            "The recommendation engine combines a two-tower collaborative filtering model with an LLM-based reranker. "
            "Training pipelines run on PyTorch with feature data warehoused in Snowflake. "
            "Serving is via a low-latency vector store fronted by a Python gRPC service."
        ),
    },
    {
        "id": "doc_security_policy",
        "title": "Information Security Policy v3",
        "type": "Policy",
        "content": (
            "All production access requires MFA via Okta. Device posture checks are enforced for any access to "
            "customer data. Quarterly penetration tests are conducted against the customer-facing API."
        ),
    },
    {
        "id": "doc_churn_analysis",
        "title": "Customer Churn Analysis Q2 2025",
        "type": "Analysis",
        "content": (
            "Churn analysis on Q2 cohorts identified pricing friction as the leading driver among SMB customers. "
            "The findings directly motivated the pricing model redesign."
        ),
    },
]

# ----- Decisions -----
DECISIONS = [
    {
        "id": "dec_tiered_pricing",
        "title": "Adopt 3-tier usage-based pricing model",
        "date": "2025-05-20",
        "rationale": "Reduce SMB churn and increase NRR by aligning price with realized value.",
    },
    {
        "id": "dec_aws_audit_mgr",
        "title": "Use AWS Audit Manager as primary cloud control attestation source",
        "date": "2025-07-15",
        "rationale": "Native integration with AWS controls reduces manual evidence collection for SOC2.",
    },
    {
        "id": "dec_mfa_mandate",
        "title": "Mandate MFA for all production system access",
        "date": "2025-10-05",
        "rationale": "Foundational control for the zero-trust rollout; closes the highest-likelihood breach vector.",
    },
    {
        "id": "dec_pytorch",
        "title": "Standardize on PyTorch for ML training pipelines",
        "date": "2025-08-10",
        "rationale": "Team expertise alignment and ecosystem maturity for reranker architectures.",
    },
]


# ----- Relationships (edges) -----
# Format: (source_id, target_id, relation_type, optional_attrs)
RELATIONSHIPS: List[tuple] = [
    # WORKED_ON: who worked on which project
    ("p_alice",  "proj_audit_q3",   "WORKED_ON", {"role": "Lead"}),
    ("p_frank",  "proj_audit_q3",   "WORKED_ON", {"role": "Security Reviewer"}),
    ("p_bob",    "proj_audit_q3",   "WORKED_ON", {"role": "Evidence Engineer"}),
    ("p_carol",  "proj_pricing",    "WORKED_ON", {"role": "PM Owner"}),
    ("p_eve",    "proj_pricing",    "WORKED_ON", {"role": "Executive Sponsor"}),
    ("p_david",  "proj_pricing",    "WORKED_ON", {"role": "Analyst"}),
    ("p_david",  "proj_ml_recs",    "WORKED_ON", {"role": "Tech Lead"}),
    ("p_bob",    "proj_ml_recs",    "WORKED_ON", {"role": "Platform Engineer"}),
    ("p_frank",  "proj_zerotrust",  "WORKED_ON", {"role": "Tech Lead"}),
    ("p_grace",  "proj_zerotrust",  "WORKED_ON", {"role": "Executive Sponsor"}),
    ("p_bob",    "proj_zerotrust",  "WORKED_ON", {"role": "Platform Engineer"}),

    # HAS_SKILL
    ("p_alice",  "skill_soc2",     "HAS_SKILL", {"level": "Expert"}),
    ("p_alice",  "skill_aws",      "HAS_SKILL", {"level": "Intermediate"}),
    ("p_bob",    "skill_python",   "HAS_SKILL", {"level": "Expert"}),
    ("p_bob",    "skill_aws",      "HAS_SKILL", {"level": "Expert"}),
    ("p_david",  "skill_python",   "HAS_SKILL", {"level": "Expert"}),
    ("p_david",  "skill_ml",       "HAS_SKILL", {"level": "Expert"}),
    ("p_eve",    "skill_finmodel", "HAS_SKILL", {"level": "Expert"}),
    ("p_frank",  "skill_pentest",  "HAS_SKILL", {"level": "Expert"}),
    ("p_frank",  "skill_soc2",     "HAS_SKILL", {"level": "Intermediate"}),

    # USED_TOOL: project -> tool
    ("proj_audit_q3",  "tool_aws_audit", "USED_TOOL", {}),
    ("proj_audit_q3",  "tool_drata",     "USED_TOOL", {}),
    ("proj_audit_q3",  "tool_okta",      "USED_TOOL", {}),
    ("proj_ml_recs",   "tool_pytorch",   "USED_TOOL", {}),
    ("proj_ml_recs",   "tool_snowflake", "USED_TOOL", {}),
    ("proj_pricing",   "tool_tableau",   "USED_TOOL", {}),
    ("proj_pricing",   "tool_snowflake", "USED_TOOL", {}),
    ("proj_zerotrust", "tool_okta",      "USED_TOOL", {}),

    # AUTHORED: person -> document
    ("p_alice", "doc_audit_report",     "AUTHORED", {}),
    ("p_carol", "doc_pricing_memo",     "AUTHORED", {}),
    ("p_david", "doc_ml_arch",          "AUTHORED", {}),
    ("p_frank", "doc_security_policy",  "AUTHORED", {}),
    ("p_david", "doc_churn_analysis",   "AUTHORED", {}),

    # ABOUT: document -> project
    ("doc_audit_report",    "proj_audit_q3",  "ABOUT", {}),
    ("doc_pricing_memo",    "proj_pricing",   "ABOUT", {}),
    ("doc_ml_arch",         "proj_ml_recs",   "ABOUT", {}),
    ("doc_security_policy", "proj_zerotrust", "ABOUT", {}),
    ("doc_churn_analysis",  "proj_pricing",   "ABOUT", {}),

    # DECISION_FOR: decision -> project
    ("dec_tiered_pricing", "proj_pricing",   "DECISION_FOR", {}),
    ("dec_aws_audit_mgr",  "proj_audit_q3",  "DECISION_FOR", {}),
    ("dec_mfa_mandate",    "proj_zerotrust", "DECISION_FOR", {}),
    ("dec_pytorch",        "proj_ml_recs",   "DECISION_FOR", {}),

    # APPROVED: person -> decision
    ("p_eve",   "dec_tiered_pricing", "APPROVED", {}),
    ("p_carol", "dec_tiered_pricing", "APPROVED", {}),
    ("p_alice", "dec_aws_audit_mgr",  "APPROVED", {}),
    ("p_grace", "dec_mfa_mandate",    "APPROVED", {}),
    ("p_frank", "dec_mfa_mandate",    "APPROVED", {}),
    ("p_grace", "dec_pytorch",        "APPROVED", {}),
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
    return nodes


if __name__ == "__main__":
    nodes = all_nodes()
    print(f"Total nodes: {len(nodes)}")
    print(f"Total edges: {len(RELATIONSHIPS)}")
    kinds = {}
    for n in nodes:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    for k, v in kinds.items():
        print(f"  {k}: {v}")
