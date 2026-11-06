"""Run the safety check on every pending signal and move each one to active or blocked. A signal never becomes active without passing.

If the knowledge graph cannot be reached, nothing changes: the signal stays pending and says why. Failing closed is the safe direction.
"""
from __future__ import annotations

import json

from ..app.evidence import EvidenceLookup
from ..app.plain import finding_name
from ..app.store import Store
from ..data.config import load_config, when
from .gate import GateResult, baseline_ranks, content_checks, load_queries, run_gate
from .kb_writer import KnowledgeGraph, signal_text


def _signal(row: dict) -> dict:
    return dict(row, evidence_ids=json.loads(row["evidence_ids"] or "[]"))


def process_pending(store: Store, cfg: dict | None = None, graph: KnowledgeGraph | None = None, only: list[str] | None = None) -> list[dict]:
    """Returns one dict per pending signal handled: {signal_id, status, summary, passed}. `only` limits the run to those signal IDs."""
    cfg = cfg or load_config()
    pending = [_signal(r) for r in store.rows("SELECT * FROM signals WHERE status = 'pending' ORDER BY created_at")]
    if only is not None:
        pending = [s for s in pending if s["signal_id"] in set(only)]
    if not pending:
        return []
    owns = graph is None
    try:
        graph = graph or KnowledgeGraph()
    except Exception as exc:  # noqa: BLE001 - the graph being down must not lose the proposals
        return [_could_not_run(store, s, f"the knowledge graph could not be reached ({type(exc).__name__})") for s in pending]
    lookup, queries, out, baseline = EvidenceLookup(cfg), load_queries(), [], None

    def exists(evidence_id: str) -> bool:
        found = lookup.describe(evidence_id)
        return found["kind"] != "Unknown" and not found["title"].endswith("(not found)")

    try:
        for s in pending:
            active = {r["signal_id"]: r["plain_text"] for r in store.rows("SELECT signal_id, plain_text FROM signals WHERE status = 'active'")}
            try:
                if all(c["ok"] for c in content_checks(s, exists, active)):
                    # the candidate goes into the real index as "pending": it is invisible to agents but the test searches can see it
                    finding = finding_name(s["indicator_code"])
                    graph.upsert(s["signal_id"], finding, signal_text(finding, s["plain_text"], s["note"] or ""), s["indicator_code"], s["source_claim_id"], "pending", None)
                    baseline = baseline if baseline is not None else baseline_ranks(graph, queries)
                    result = run_gate(s, graph, exists, active, queries, baseline)
                    if result.passed:
                        graph.set_status(s["signal_id"], "active", when(cfg, "dataset_today"))
                        baseline = None  # the live knowledge just changed: later signals are judged against the new state
                    else:
                        graph.set_status(s["signal_id"], "blocked")
                else:
                    result = run_gate(s, graph, exists, active, queries, baseline={})  # returns at the content checks; nothing is written to the graph
            except Exception as exc:  # noqa: BLE001
                out.append(_could_not_run(store, s, f"{type(exc).__name__}: {str(exc)[:120]}"))
                continue
            status = "active" if result.passed else "blocked"
            store.set_signal_status(s["signal_id"], status, result.to_json())
            out.append(dict(signal_id=s["signal_id"], status=status, summary=result.summary, passed=result.passed))
    finally:
        lookup.close()
        if owns:
            graph.close()
    return out


def _could_not_run(store: Store, s: dict, why: str) -> dict:
    result = GateResult(False, f"The safety check could not run ({why}). The signal stays pending and nothing was added.")
    store.set_signal_status(s["signal_id"], "pending", json.dumps(dict(passed=None, summary=result.summary, checks=[], metrics={})))
    return dict(signal_id=s["signal_id"], status="pending", summary=result.summary, passed=None)
