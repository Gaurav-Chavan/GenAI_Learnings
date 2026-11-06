"""The safety check ("regression gate") for a proposed knowledge-base signal.

A signal only informs what the agents can look up; it never changes a score. So the check asks two questions that need no paid model:

  1. Is the signal itself safe? Real evidence IDs, no personal data, nothing that reads like an instruction to an agent, no claims of certainty,
     not a copy of something already in the knowledge base.
  2. Does adding it make the knowledge search worse? A frozen set of plain-language test searches (knowledge/gate_queries.yaml, one finding
     type each) is run against the real search index with and without the signal. It is blocked if it pushes the right pattern card out of the
     top three for any test search, or if it turns up near the top of searches about unrelated findings.

What this does not measure: whether an agent's decisions improve. That needs the paid evaluation, and the result says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

import yaml

from ..data.config import ROOT

TOP = 3            # the right pattern card must stay in the top three of every test search it was in the top three of
FINDABLE_TOP = 5   # the signal should be found in the top five of at least one search about its own finding
POLLUTION_TOP = 3  # ...and may appear in the top three of at most MAX_POLLUTION searches about other findings
MAX_POLLUTION = 2
MAX_TEXT = 800
NEAR_DUPLICATE = 0.8

INSTRUCTION_PATTERNS = [r"ignore (all|any|the|previous|prior|above)", r"disregard", r"system prompt", r"you are now", r"\bnew instructions?\b", r"</?script",
                        r"https?://", r"\b(assistant|system|developer)\s*:", r"override (the )?(score|band|rules?)"]
CERTAINTY_PHRASES = ("proven fraud", "fraud proven", "guilty", "definitely", "always fraud", "never fraud", "auto-approve", "will be paid", "no need to check")
PII_PATTERNS = [r"\+91\d{10}", r"[\w.+-]+@[\w-]+\.[\w.]+", r"\b\d{12}\b", r"\bFlat \d+"]


@dataclass
class GateResult:
    passed: bool
    summary: str
    checks: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(dict(passed=self.passed, summary=self.summary, checks=self.checks, metrics=self.metrics))

    @staticmethod
    def from_json(text: str | None) -> dict:
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}


class Searcher(Protocol):
    def ranked_ids(self, query: str, candidate: str | None = None) -> list[str]:
        """IDs of the top knowledge results for a search: pattern cards, clauses and ACTIVE signals, plus the candidate signal when one is named."""


def load_queries(path=None) -> dict[str, list[str]]:
    with open(path or ROOT / "knowledge" / "gate_queries.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def clean_query(text: str) -> str:
    return " ".join(re.sub(r"[^A-Za-z0-9\s-]", " ", text).split())


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ------------------------------------------------------------------ 1. is the signal itself safe?
def content_checks(signal: dict, evidence_exists: Callable[[str], bool], active_texts: dict[str, str]) -> list[dict]:
    text = f"{signal['plain_text']} {signal.get('note') or ''}"
    bad_evidence = [e for e in signal["evidence_ids"] if not evidence_exists(e)]
    pii = [p for p in PII_PATTERNS if re.search(p, text)]
    instr = [p for p in INSTRUCTION_PATTERNS if re.search(p, text, re.I)]
    certain = [c for c in CERTAINTY_PHRASES if c in text.lower()]
    mine = _words(signal["plain_text"])
    twin = next((sid for sid, t in active_texts.items() if sid != signal["signal_id"] and mine and len(mine & _words(t)) / len(mine | _words(t)) >= NEAR_DUPLICATE), None)
    return [
        dict(check="cites real records", ok=not bad_evidence, detail=f"not found: {', '.join(bad_evidence)}" if bad_evidence else f"{len(signal['evidence_ids'])} record(s) found"),
        dict(check="no personal data", ok=not pii, detail="looks like a phone number, e-mail, account number or address" if pii else "none found"),
        dict(check="nothing that reads like an instruction", ok=not instr, detail="it contains wording that tries to instruct the agents" if instr else "none found"),
        dict(check="no claims of certainty", ok=not certain, detail=f"contains: {', '.join(certain)}" if certain else "none found"),
        dict(check="a reasonable length", ok=len(text) <= MAX_TEXT, detail=f"{len(text)} characters (limit {MAX_TEXT})"),
        dict(check="not a copy of an existing signal", ok=twin is None, detail=f"almost identical to {twin}" if twin else "new"),
    ]


# ------------------------------------------------------------------ 2. does adding it make the search worse?
def rank_of(ids: list[str], target: str) -> int | None:
    return ids.index(target) + 1 if target in ids else None


def baseline_ranks(searcher: Searcher, queries: dict[str, list[str]]) -> dict[str, int | None]:
    """The rank of the right pattern card for every test search, with only the knowledge that is already live."""
    return {clean_query(q): rank_of(searcher.ranked_ids(clean_query(q)), code) for code, phrases in queries.items() for q in phrases}


def retrieval_check(signal: dict, searcher: Searcher, queries: dict[str, list[str]], baseline: dict[str, int | None]) -> tuple[list[dict], dict]:
    sid, code = signal["signal_id"], signal["indicator_code"]
    worse, own_hits, strays, total = [], 0, [], 0
    for target, phrases in queries.items():
        for phrase in phrases:
            q, total = clean_query(phrase), total + 1
            ids = searcher.ranked_ids(q, candidate=sid)
            before, after = baseline[q], rank_of(ids, target)
            if before is not None and before <= TOP and (after is None or after > TOP):
                worse.append(dict(search=phrase, finding=target, before=before, after=after))
            if target == code and sid in ids[:FINDABLE_TOP]:
                own_hits += 1
            if target != code and sid in ids[:POLLUTION_TOP]:
                strays.append(phrase)
    own_total = len(queries.get(code, []))
    metrics = dict(searches=total, worse=len(worse), found_in_own_searches=f"{own_hits}/{own_total}", appears_in_unrelated_searches=len(strays))
    first = worse[0] if worse else None
    checks = [
        dict(check=f"keeps the right pattern card in the top {TOP}", ok=not worse,
             detail=(f"made {len(worse)} of {total} test searches worse, for example \"{first['search']}\": the card moved from {first['before']} to "
                     f"{first['after'] or 'outside the results'}") if worse else f"no change for the top {TOP} in {total} test searches"),
        dict(check="does not show up in unrelated searches", ok=len(strays) <= MAX_POLLUTION,
             detail=(f"in the top {POLLUTION_TOP} of {len(strays)} searches about other findings, for example \"{strays[0]}\"") if strays else "never"),
        dict(check="can be found by searches about its own finding", ok=True, detail=f"top {FINDABLE_TOP} in {own_hits} of {own_total} (information only)"),
    ]
    return checks, metrics


def run_gate(signal: dict, searcher: Searcher, evidence_exists: Callable[[str], bool], active_texts: dict[str, str],
             queries: dict[str, list[str]] | None = None, baseline: dict[str, int | None] | None = None) -> GateResult:
    """signal needs: signal_id, indicator_code, plain_text, note, evidence_ids. The searcher must be able to see the candidate in the index."""
    checks = content_checks(signal, evidence_exists, active_texts)
    if not all(c["ok"] for c in checks):
        failed = "; ".join(f"{c['check']}: {c['detail']}" for c in checks if not c["ok"])
        return GateResult(False, f"Blocked before the search test: {failed}.", checks)
    queries = queries or load_queries()
    baseline = baseline if baseline is not None else baseline_ranks(searcher, queries)
    more, metrics = retrieval_check(signal, searcher, queries, baseline)
    checks += more
    failed = [c for c in more if not c["ok"]]
    note = " This tests the knowledge search only; whether agent decisions improve needs the paid evaluation."
    if failed:
        return GateResult(False, "Blocked: " + "; ".join(f"{c['check']} ({c['detail']})" for c in failed) + ".", checks, metrics)
    return GateResult(True, f"Passed: {metrics['searches']} test searches, none made worse; it appears in {metrics['appears_in_unrelated_searches']} unrelated search(es)." + note, checks, metrics)
