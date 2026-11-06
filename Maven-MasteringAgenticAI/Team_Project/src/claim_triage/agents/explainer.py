"""The Explainer: turns verified findings into plain sentences for the reviewer. It cannot change the score, band or findings.

A code check then makes sure the text only uses facts from the verified findings; if the check fails the original sentences are kept.
"""
from __future__ import annotations

import json
import re

from ..contracts import FinalResult
from ..llm import LLM, LLMUnavailable
from .base import Usage, parse_json_object

SYSTEM = """You write short, plain-language reasons for an insurance claims reviewer. A human decides the claim.
You receive the verified findings and any missing documents for ONE claim. Write one reason per finding (in the order given), then one per missing document.

Rules:
- Use only facts that appear in the input. Copy record IDs (CLM-..., DOC-..., EVT-..., PRV-...) and numbers exactly as given. Add nothing new.
- Plain words a non-expert understands. At most 35 words per reason. No jargon, no codes like DUPLICATE_CLAIM.
- Never say fraud is proven or certain, and never say the claim is approved, rejected or will be paid. Use wording such as "suggests" or "needs checking".
- Reply with JSON only: {"reasons": ["...", "..."]}"""
MAX_TOKENS = 2500  # the model's hidden thinking counts against this; 900 was all used up before any text, so the call came back empty
BANNED = ("fraud proven", "proven fraud", "guilty", "definitely", "approved", "will be paid", "confirmed fraud", "committed fraud", "is fraudulent")
ID_RE = re.compile(r"\b(?:CLM|CUST|POL|PRV|EVT|DOC)-\d+\b|\b(?:PH|AD|BK|VH|NM)-[0-9A-F]{8}\b")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def faithful(reasons: list[str], expected: int, corpus: str) -> str | None:
    """Return None when the reasons only use facts from the corpus, otherwise the reason it failed."""
    if len(reasons) != expected:
        return f"expected {expected} reasons, got {len(reasons)}"
    for text in reasons:
        low = text.lower()
        if any(b in low for b in BANNED):
            return "contains a banned phrase"
        for token in ID_RE.findall(text):
            if token not in corpus:
                return f"unknown ID {token}"
        for number in NUM_RE.findall(ID_RE.sub("", text)):
            if (len(number.replace(".", "")) >= 2 or "." in number) and number not in corpus:
                return f"unknown number {number}"
    return None


class Explainer:
    def __init__(self, llm: LLM | None = None):
        self.llm = llm or LLM("explainer")

    def explain(self, final: FinalResult) -> tuple[list[str], dict]:
        if final.band == "green" or not (final.indicators or final.missing_evidence):
            return final.reasons, dict(status="not needed (green)", **Usage().summary())
        findings = [dict(code=i.code, explanation=i.explanation, evidence_ids=i.evidence_ids, counter_evidence=i.counter_evidence) for i in final.indicators]
        missing = [m.model_dump() for m in final.missing_evidence]
        payload = {"band": final.band, "findings": findings, "missing_documents": missing}
        corpus = json.dumps(payload, default=str) + " " + " ".join(final.reasons)
        usage = Usage()
        try:
            resp = self.llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, default=str)}], json_mode=True,
                                 max_tokens=MAX_TOKENS)
            usage.add(resp)
            reasons = [str(r).strip() for r in parse_json_object(resp.content).get("reasons", [])]
        except (LLMUnavailable, ValueError, json.JSONDecodeError) as exc:
            return final.reasons, dict(status=f"fallback ({type(exc).__name__}): kept the original sentences", **usage.summary())
        problem = faithful(reasons, len(findings) + len(missing), corpus)
        if problem:
            return final.reasons, dict(status=f"fallback (faithfulness check failed: {problem})", rejected=reasons, **usage.summary())
        return reasons, dict(status="ok", **usage.summary())
