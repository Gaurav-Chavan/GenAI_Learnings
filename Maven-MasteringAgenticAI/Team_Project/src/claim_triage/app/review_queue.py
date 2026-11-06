"""The review queue: one row per assessed claim, riskiest first. Plain functions so they can be tested without the dashboard.

The queue shows a customer by ID only, plus the claim's own line, date and amount. It reads silver (never the vault or answer keys) and the app database.
"""
from __future__ import annotations

import json

import duckdb

from ..data.config import paths
from .store import Store

BAND_RANK = {"red": 0, "amber": 1, "green": 2}
BAND_LABEL = {"red": "Red", "amber": "Review needed", "green": "Green"}
BAND_ICON = {"red": "🔴", "amber": "🟠", "green": "🟢"}


def _top_reason(result: dict) -> str:
    reasons = result.get("reasons") or []
    return reasons[0] if reasons else ""


def _flags(result: dict) -> int:
    """How many findings the assessment rests on (the agents' saved runs list codes, the rules-only result lists indicators)."""
    return len(result.get("indicators") or result.get("proposed") or [])


def load_queue(cfg: dict, store: Store, system: str = "rules") -> list[dict]:
    assessed = {r["claim_id"]: r for r in store.rows("SELECT claim_id, band, trust_score, evidence_status, result_json FROM assessments WHERE system = ?", system)}
    if not assessed:
        return []
    ids = list(assessed)
    silver = duckdb.connect(str(paths(cfg)["silver"]), read_only=True)
    try:
        cur = silver.execute(f"SELECT claim_id, customer_id, line, filed_date, amount FROM claims WHERE claim_id IN ({','.join('?' * len(ids))})", ids)
        claims = {r[0]: dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()}
    finally:
        silver.close()
    latest = {r["claim_id"]: r for r in store.rows(
        "SELECT claim_id, final_band, decision FROM decisions WHERE decision NOT IN ('hold', 'escalate') "  # a hold or an escalation is not a decision: the claim stays open
        "QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY created_at DESC) = 1")}
    has_agent = {r["claim_id"] for r in store.rows("SELECT claim_id FROM assessments WHERE system = 'agents'")}
    owners = store.assignments()
    rows = []
    for cid, a in assessed.items():
        if cid not in claims:
            continue
        result = json.loads(a["result_json"])
        d = latest.get(cid)
        rows.append(dict(claim_id=cid, customer_id=claims[cid]["customer_id"], line=claims[cid]["line"], filed_date=claims[cid]["filed_date"],
                         amount=claims[cid]["amount"], band=a["band"], trust_score=a["trust_score"], evidence_status=a["evidence_status"],
                         top_reason=_top_reason(result), flags=_flags(result), missing=len(result.get("missing_evidence") or []),
                         reviewed=d["final_band"] if d else None, has_agent_run=cid in has_agent, assignee=owners.get(cid)))
    return sorted(rows, key=lambda r: (r["trust_score"], BAND_RANK[r["band"]], r["claim_id"]))  # least trusted first


def summarise(rows: list[dict]) -> dict:
    out = {"total": len(rows), "unreviewed": sum(r["reviewed"] is None for r in rows)}
    for band in BAND_RANK:
        out[band] = sum(r["band"] == band for r in rows)
    return out


def apply_filters(rows: list[dict], bands: list[str] | None = None, lines: list[str] | None = None, only_unreviewed: bool = False, search: str = "",
                  assignee: str | None = None) -> list[dict]:
    """assignee: keep only claims owned by that reviewer (compared without regard to case); None keeps everything."""
    needle, owner = search.strip().lower(), (assignee or "").strip().lower()
    return [r for r in rows
            if (not bands or r["band"] in bands) and (not lines or r["line"] in lines) and (not only_unreviewed or r["reviewed"] is None)
            and (not owner or (r.get("assignee") or "").lower() == owner)
            and (not needle or needle in r["claim_id"].lower() or needle in r["customer_id"].lower())]
