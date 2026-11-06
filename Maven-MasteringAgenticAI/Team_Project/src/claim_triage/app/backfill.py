"""Fill the app database so the dashboard has something to show. Costs nothing: no model is called.

    uv run python -m claim_triage.app.backfill

  * every claim awaiting assessment gets the rules-only assessment (the same deterministic baseline the evaluation uses);
  * where a saved agentic run exists in saved_runs/ (shipped with the repository) or traces/ (your own runs), the newest one per claim is stored as the "agents" view, exactly as it was saved
    (those runs came from small samples, so the dashboard labels them as saved runs, not as a full evaluation).
"""
from __future__ import annotations

import glob
import json
from collections import Counter
from pathlib import Path

import duckdb

from ..baseline import assess_claim
from ..data.config import ROOT, load_config, paths
from ..scoring.scorer import load_weights
from ..tools import ToolBox
from .store import Store


def claim_ids(cfg: dict) -> list[str]:
    gold = duckdb.connect(str(paths(cfg)["gold"]), read_only=True)
    try:
        return [r[0] for r in gold.execute("SELECT claim_id FROM claim_features ORDER BY claim_id").fetchall()]
    finally:
        gold.close()


def backfill_rules(store: Store, cfg: dict, ids: list[str]) -> Counter:
    W, box, bands = load_weights(), ToolBox(cfg, graph=False), Counter()
    try:
        for cid in ids:
            box.returned_ids.clear()
            result = assess_claim(box, cid, W)
            store.put_assessment(cid, "rules", result, "rules-only baseline (no model)")
            bands[result.band] += 1
    finally:
        box.close()
    return bands


def saved_agent_runs(trace_dir: Path | None = None) -> dict[str, tuple[str, dict]]:
    """The newest saved agentic result per claim: claim_id -> (file name, record).

    By default the runs that ship with the repository (saved_runs/) are read first and your own runs (traces/) after them, so a run you made yourself wins."""
    latest: dict[str, tuple[str, dict]] = {}
    folders = [trace_dir] if trace_dir else [ROOT / "saved_runs", ROOT / "traces"]
    for file in [f for folder in folders for f in sorted(glob.glob(str(folder / "*.jsonl")))]:  # oldest first, so later files win
        for line in open(file, encoding="utf-8"):
            rec = json.loads(line)
            latest[rec["claim_id"]] = (Path(file).name, rec)
    return latest


def backfill_agents(store: Store, ids: set[str], trace_dir: Path | None = None) -> int:
    n = 0
    for cid, (name, rec) in saved_agent_runs(trace_dir).items():
        if cid not in ids:
            continue
        trace = rec.get("trace") or {}
        result = dict(claim_id=cid, case_id=rec.get("case_id"), band=rec["band"], trust_score=rec["trust"], evidence_status="unknown", reasons=rec.get("reasons", []),
                      proposed=trace.get("proposed", []), summary=trace.get("summary", ""), tools=(trace.get("investigator") or {}).get("tools", []),
                      verifier_reviews=(trace.get("verifier") or {}).get("reviews", []), models_used=(trace.get("investigator") or {}).get("models", []),
                      saved_run=name)
        store.put_assessment(cid, "agents", result, f"saved agentic run ({name})")
        n += 1
    return n


def main() -> None:
    cfg = load_config()
    ids = claim_ids(cfg)
    store = Store(cfg)
    bands = backfill_rules(store, cfg, ids)
    agents = backfill_agents(store, set(ids))
    print(f"rules-only assessments: {sum(bands.values())} ({dict(bands)}); saved agent runs: {agents}; database: {store.path}")
    store.close()


if __name__ == "__main__":
    main()
