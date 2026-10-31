"""Answer generation shared by both systems.

Vector RAG and GraphRAG differ only in how they retrieve. They hand their context to the
same prompt, model and refusal rule here, so the comparison isolates retrieval.
"""

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from langchain_core.prompts import ChatPromptTemplate

from .config import get_chat_llm

REFUSAL = "I couldn't find this in the org knowledge base."

ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", f"""You answer questions about an organization's people, teams, projects, skills,
tools, documents and decisions.

Rules:
- Use ONLY the context below. Never use outside knowledge or guess.
- Every source in the context starts with its id in square brackets. Cite ids right after
  each fact, e.g. "Alice Chen led the audit [proj_audit_q3]". Every bullet or sentence that
  states a fact needs at least one citation.
- Prefer citing entity ids (like p_alice, proj_pricing, dec_mfa_mandate). When a source is a
  table of results, cite the entity ids that appear in its rows, not the table itself.
- If the context does not contain the answer, reply exactly: "{REFUSAL}"
- If it answers only part of the question, answer that part and say what is missing.
- Be concise. Use a bullet list when there are several items."""),
    ("human", "Question: {question}\n\nContext:\n{context}"),
])

_CITATION_GROUP = re.compile(r"\[([a-z0-9_#,\s]+)\]")


def cited_ids(text: str) -> List[str]:
    """Ids cited as [a] or [a, b] - the model often groups several ids in one bracket."""
    return [i.strip() for group in _CITATION_GROUP.findall(text) for i in group.split(",") if "_" in i]


@dataclass
class Source:
    id: str
    text: str
    score: float | None = None


@dataclass
class RAGResult:
    system: str
    question: str
    answer: str
    sources: List[Source]
    citations: List[str]
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)  # e.g. generated Cypher, route

    @property
    def refused(self) -> bool:
        return REFUSAL.lower() in self.answer.lower()

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "refused": self.refused}


def format_context(sources: List[Source]) -> str:
    return "\n\n".join(f"[{s.id}] {s.text}" for s in sources)


def generate(system: str, question: str, sources: List[Source], started: float, **extra) -> RAGResult:
    """Answer from `sources`; `started` is the time.perf_counter() when retrieval began."""
    if sources:
        response = (ANSWER_PROMPT | get_chat_llm()).invoke(
            {"question": question, "context": format_context(sources)}
        )
        answer = response.content
        usage = response.usage_metadata or {}
    else:  # nothing retrieved: refuse without calling the model
        answer, usage = REFUSAL, {}

    # Citable ids: each source's own id plus ids it mentions as [id] (GraphRAG neighbour triples).
    known = {s.id for s in sources} | {i for s in sources for i in cited_ids(s.text)}
    citations = list(dict.fromkeys(c for c in cited_ids(answer) if c in known))
    return RAGResult(
        system=system,
        question=question,
        answer=answer,
        sources=sources,
        citations=citations,
        latency_s=round(time.perf_counter() - started, 2),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        extra=extra,
    )
