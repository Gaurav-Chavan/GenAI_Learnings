"""Prompts for the GraphRAG pipeline (the shared answer prompt lives in generation.py).

The Text2Cypher few-shot questions are deliberately NOT the eval questions; they teach
query patterns (role filters, date ordering, aggregation, provenance, paths, text lookup).
"""

from langchain_core.prompts import ChatPromptTemplate

from .data.mock_data_ext import SCHEMA

# ---------------------------------------------------------------- router

ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """Classify a question for an organizational knowledge assistant whose knowledge
graph holds people, teams, projects, skills, tools, documents and decisions.

- "graph": about specific entities and how they relate - who/which/how many, lists,
  roles, approvals, reporting lines, dates, "the last X", connections between things.
- "semantic": about what a document or policy says - details, numbers, explanations,
  rationale wording, "what is our policy on X".
- "out_of_scope": not about this organization at all (general knowledge, chit-chat).

Questions about plausible org topics that may simply be missing (e.g. an unknown
project) are still "graph" or "semantic", never "out_of_scope"."""),
    ("human", "{question}"),
])

# ---------------------------------------------------------------- Text2Cypher

_RELATIONSHIPS = "\n".join(f"(:{src})-[:{rel}]->(:{dst})" for src, rel, dst in SCHEMA)

GRAPH_SCHEMA = f"""Node labels and properties (every node ALSO has label :Entity with `id`, `name`, `description`):
- Person {{id, name, role, team}}
- Team {{id, name}}
- Project {{id, name, status ('Completed' | 'In Progress'), started: Date, ended: Date (null while ongoing), summary}}
- Skill {{id, name, category}}
- Tool {{id, name, vendor}}
- Document {{id, name, title, type, content}}   (name == title)
- Decision {{id, name, title, date: Date, rationale}}   (name == title)

Relationships (direction matters):
{_RELATIONSHIPS}
Relationship properties: WORKED_ON {{role}}, HAS_SKILL {{level}}."""

CYPHER_FEW_SHOTS = [
    ("Which projects did Carol Wu work on and in what role?", """
MATCH (p:Person)-[w:WORKED_ON]->(proj:Project)
WHERE toLower(p.name) CONTAINS 'carol'
RETURN p.id AS person_id, p.name AS person, w.role AS role, proj.id AS project_id, proj.name AS project"""),

    ("What was the most recent security project and which tools did it use?", """
MATCH (proj:Project)
WHERE toLower(proj.name) CONTAINS 'security'
WITH proj ORDER BY coalesce(proj.ended, date()) DESC LIMIT 1
OPTIONAL MATCH (proj)-[:USED_TOOL]->(t:Tool)
RETURN proj.id AS project_id, proj.name AS project, proj.started AS started, proj.ended AS ended,
       collect({tool_id: t.id, tool: t.name}) AS tools"""),

    ("Which skills are held by more than one person?", """
MATCH (p:Person)-[h:HAS_SKILL]->(s:Skill)
WITH s, collect({person_id: p.id, person: p.name, level: h.level}) AS people
WHERE size(people) > 1
RETURN s.id AS skill_id, s.name AS skill, size(people) AS count, people
ORDER BY count DESC"""),

    ("Which documents support the decision to mandate MFA, and who wrote them?", """
MATCH (d:Decision)-[:BASED_ON]->(doc:Document)
WHERE toLower(d.name) CONTAINS 'mfa'
OPTIONAL MATCH (author:Person)-[:AUTHORED]->(doc)
RETURN d.id AS decision_id, d.name AS decision, doc.id AS document_id, doc.name AS document,
       author.id AS author_id, author.name AS author"""),

    ("How is Henry Okafor connected to Drata?", """
MATCH (a:Entity), (b:Entity)
WHERE toLower(a.name) CONTAINS 'henry' AND toLower(b.name) = 'drata'
MATCH path = shortestPath((a)-[*..5]-(b))
RETURN [n IN nodes(path) | {id: n.id, name: n.name}] AS path_nodes,
       [r IN relationships(path) | {type: type(r), from: startNode(r).name, to: endNode(r).name, role: r.role}] AS path_relationships"""),

    ("Who reports to Alice Chen, and which decisions has each of them approved?", """
MATCH (r:Person)-[:REPORTS_TO]->(m:Person)
WHERE toLower(m.name) CONTAINS 'alice'
OPTIONAL MATCH (r)-[:APPROVED]->(d:Decision)
RETURN m.id AS manager_id, m.name AS manager, r.id AS person_id, r.name AS person,
       collect({decision_id: d.id, decision: d.name, date: d.date}) AS approved"""),

    ("What does the ML architecture document say about serving?", """
MATCH (doc:Document)
WHERE toLower(doc.content) CONTAINS 'serving'
RETURN doc.id AS document_id, doc.name AS document, doc.content AS content"""),
]

_EXAMPLES = "\n\n".join(f"Question: {q}\nCypher:{c}" for q, c in CYPHER_FEW_SHOTS)

CYPHER_SYSTEM = f"""You translate questions into a single read-only Neo4j Cypher query.

{GRAPH_SCHEMA}

Entity names in the graph (match against these):
<<ENTITY_CATALOG>>

Rules:
- Read-only: MATCH / OPTIONAL MATCH / WITH / WHERE / RETURN / ORDER BY / LIMIT / UNWIND only.
- Match names case-insensitively with toLower(x.name) CONTAINS '<distinctive keyword>'.
- Roles and levels are free text: "leads"/"led by" means toLower(w.role) CONTAINS 'lead'
  (matches 'Lead' and 'Tech Lead'); never test a role with exact equality.
- Filter on HAS_SKILL.level only when the question names a level ("expert-level",
  "intermediate"). "Expertise in X", "knows X", "skilled in X" mean anyone with skill X -
  return the level instead of filtering on it.
- With several OPTIONAL MATCH clauses, use collect(DISTINCT ...) so rows aren't duplicated.
- Always return the `id` of every node you return, next to its name.
- "last", "latest", "most recent" mean ORDER BY the date property DESC (coalesce(ended, date())
  for projects so ongoing ones count as most recent), then LIMIT.
- Prefer aggregated rows (collect) over many duplicate rows. Never return embeddings. LIMIT 50.
- If the graph cannot hold the answer, still write the closest query; empty results are fine.
- Output ONLY the Cypher query - no explanation, no markdown fences.

Examples:

{_EXAMPLES}"""


def _template_safe(text: str) -> str:
    """Escape Cypher map braces so ChatPromptTemplate doesn't read them as variables."""
    return text.replace("{", "{{").replace("}", "}}").replace("<<ENTITY_CATALOG>>", "{entity_catalog}")


CYPHER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _template_safe(CYPHER_SYSTEM)),
    ("human", "Question: {question}{error_feedback}\nCypher:"),
])

CYPHER_RETRY_FEEDBACK = """

Your previous query failed:
{cypher}
Error: {error}
Write a corrected query."""

# ---------------------------------------------------------------- grader

GRADER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You check whether retrieved knowledge-graph results contain enough information to
fully answer a question. Answer sufficient=true only if every part of the question can be
answered from the results alone. Missing parts, empty values or unrelated rows mean false."""),
    ("human", "Question: {question}\n\nResults:\n{context}"),
])
