"""Evaluation harness. Only this package (and the data pipeline) may read the hidden labels.

    uv run python -m claim_triage.evaluation.harness --split both     # dev + held-out, writes eval/baseline_report.md

Expected band: the label says red (suspicious), amber (missing evidence) or green (honest). By design a claim from a high-fraud
area is flagged "review needed" (never red), so an HONEST claim whose area flag fires is expected to be amber, not green.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import duckdb

from ..baseline import assess_claim
from ..contracts import FinalResult
from ..data.config import ROOT, load_config, paths
from ..scoring.scorer import load_weights
from ..tools import ToolBox

DOC_ALIAS = {"original_bill": "bill"}
BANDS = ["red", "amber", "green"]
TARGETS = {"fraud_in_green": ("<=", 0.05), "legit_in_red": ("<=", 0.10), "amber_rate": ("<=", 0.40), "missing_never_green": (">=", 1.0),
           "evidence_coverage": (">=", 1.0)}


TIERS = ("standard", "hard", "all")


def load_cases(cfg: dict, split: str, tier: str = "standard") -> list[dict]:
    """standard = the original 300 cases (table labels); hard = probes that need reading documents or past decisions (table labels_hard)."""
    con = duckdb.connect(str(paths(cfg)["labels"]), read_only=True)
    where = "" if split == "all" else f"where split = '{split}'"
    tables = {"standard": ["labels"], "hard": ["labels_hard"], "all": ["labels", "labels_hard"]}[tier]
    cur = con.execute(" union all ".join(f"select * from {t} {where}" for t in tables) + " order by case_id")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    con.close()
    for r in rows:  # an empty list is stored as NULL; the metrics expect text
        for key in ("expected_indicators", "expected_absent", "expected_missing"):
            r[key] = r[key] or ""
    return rows


def case_context(cfg: dict) -> dict[str, dict]:
    """claim_id -> reviewer-visible context used only for fairness slices (gender) and the area flag."""
    p = paths(cfg)
    gold = duckdb.connect(str(p["gold"]), read_only=True)
    out = {r[0]: dict(gender=r[1], area_high=bool(r[2])) for r in gold.execute(
        "select f.claim_id, c.gender, f.area_is_high from claim_features f join customer_360 c using(customer_id)").fetchall()}
    gold.close()
    return out


def run(cfg: dict, cases: list[dict], assess: Callable[[ToolBox, str], FinalResult], *, graph: bool = False, workers: int = 1,
        trace_path: Path | None = None, progress: bool = False) -> list[dict]:
    """Run an assessor over labelled cases. Each worker thread has its own ToolBox (DuckDB connections are not shared across threads)."""
    ctx, local, boxes, lock = case_context(cfg), threading.local(), [], threading.Lock()

    def one(label: dict) -> dict:
        if not hasattr(local, "box"):
            local.box = ToolBox(cfg, graph=graph)
            with lock:
                boxes.append(local.box)
        box = local.box
        box.returned_ids.clear()
        c = ctx[label["claim_id"]]
        expected = "amber" if label["kind"] == "honest" and c["area_high"] else label["expected_band"]
        row = dict(case_id=label["case_id"], claim_id=label["claim_id"], scenario=label["scenario_id"], kind=label["kind"], split=label["split"],
                   expected=expected, label_band=label["expected_band"], expected_indicators=label["expected_indicators"],
                   expected_absent=label["expected_absent"], expected_missing=label["expected_missing"], area_high=c["area_high"],
                   hotspot_pin=label["hotspot_pin"], gender=c["gender"])
        t0 = time.time()
        try:
            r = assess(box, label["claim_id"])
        except Exception as exc:  # noqa: BLE001 - a failed analysis is reported, never turned into a made-up score
            row.update(predicted="unavailable", trust=None, fired="", dropped="", predicted_missing="", n_reasons=0, error=f"{type(exc).__name__}: {str(exc)[:120]}",
                       latency_ms=round((time.time() - t0) * 1000), tokens=0, cost_usd=None, tool_calls=0, loop_guard=False, proposed="", verifier_removed="",
                       explainer_ok=None, agent_missing="")
            return row
        t = r.trace or {}
        proposed = t.get("proposed", [])
        fired = [i.code for i in r.indicators]
        row.update(predicted=r.band, trust=r.trust_score, fired=";".join(fired), dropped=";".join(r.dropped_indicators),
                   predicted_missing=";".join(m.document for m in r.missing_evidence), n_reasons=len(r.reasons), error="",
                   latency_ms=round((time.time() - t0) * 1000), tokens=r.tokens, cost_usd=r.cost_usd,
                   tool_calls=(t.get("investigator") or {}).get("tool_calls", 0), loop_guard=bool((t.get("investigator") or {}).get("loop_guard")),
                   proposed=";".join(proposed), verifier_removed=";".join(c for c in proposed if c not in fired),
                   explainer_ok=None if not t else (t.get("explainer", {}).get("status", "").startswith(("ok", "not needed"))),
                   agent_missing=";".join(t.get("agent_reported_missing", [])))
        if trace_path is not None:
            with lock, open(trace_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(case_id=label["case_id"], claim_id=label["claim_id"], band=r.band, trust=r.trust_score, reasons=r.reasons,
                                         trace=t), default=str) + "\n")
        return row

    results: list[dict] = []
    try:
        if workers <= 1:
            for i, label in enumerate(cases, 1):
                results.append(one(label))
                if progress:
                    print(f"  {i}/{len(cases)} {label['case_id']} -> {results[-1]['predicted']}", flush=True)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for i, row in enumerate(pool.map(one, cases), 1):
                    results.append(row)
                    if progress:
                        print(f"  {i}/{len(cases)} {row['case_id']} -> {row['predicted']}", flush=True)
    finally:
        for b in boxes:
            b.close()
    return results


def pct(a: int, b: int):
    return round(a / b, 3) if b else None


def compute(rows: list[dict]) -> dict:
    sus = [r for r in rows if r["kind"] == "suspicious"]
    hon = [r for r in rows if r["kind"] == "honest"]
    mis = [r for r in rows if r["kind"] == "missing"]
    fired = lambda r: set(filter(None, r["fired"].split(";")))  # noqa: E731
    sus_red = [r for r in sus if r["expected"] == "red"]  # a hard-tier suspicious case can be expected amber ("look at this one"), not red
    n_fired = sum(len(fired(r)) for r in rows)
    n_dropped = sum(len(list(filter(None, r["dropped"].split(";")))) for r in rows)
    m = dict(cases=len(rows), suspicious=len(sus), honest=len(hon), missing=len(mis),
             band_accuracy=pct(sum(r["predicted"] == r["expected"] for r in rows), len(rows)),
             fraud_in_green=pct(sum(r["predicted"] == "green" for r in sus), len(sus)),
             fraud_red_recall=pct(sum(r["predicted"] == "red" for r in sus_red), len(sus_red)),
             fraud_flagged_recall=pct(sum(r["predicted"] in ("red", "amber") for r in sus), len(sus)),
             legit_in_red=pct(sum(r["predicted"] == "red" for r in hon), len(hon)),
             legit_as_expected=pct(sum(r["predicted"] == r["expected"] for r in hon), len(hon)),
             amber_rate=pct(sum(r["predicted"] == "amber" for r in rows), len(rows)),
             missing_never_green=pct(sum(r["predicted"] != "green" for r in mis), len(mis)),
             missing_as_expected=pct(sum(r["predicted"] == "amber" for r in mis), len(mis)),
             indicator_recall=pct(sum(set(r["expected_indicators"].split(";")) <= fired(r) for r in sus), len(sus)),
             false_indicator_rate=pct(sum(bool(set(filter(None, r["expected_absent"].split(";"))) & fired(r)) for r in hon), len(hon)),
             missing_item_recall=pct(sum(DOC_ALIAS.get(r["expected_missing"], r["expected_missing"]) in r["predicted_missing"].split(";") for r in mis), len(mis)),
             evidence_coverage=round(1 - n_dropped / (n_fired + n_dropped), 3) if n_fired + n_dropped else 1.0,
             latency_ms_median=int(statistics.median(r["latency_ms"] for r in rows)) if rows else None,
             latency_ms_p95=sorted(r["latency_ms"] for r in rows)[int(0.95 * (len(rows) - 1))] if rows else None)
    if any(r.get("tokens") for r in rows):  # agentic runs: cost, effort and what each agent did
        agent = [r for r in rows if "tokens" in r]
        split_codes = lambda r, key: set(filter(None, r.get(key, "").split(";")))  # noqa: E731
        costs = [r["cost_usd"] for r in agent if r["cost_usd"] is not None]
        m.update(unavailable=sum(r["predicted"] == "unavailable" for r in agent),
                 tokens_per_case=int(statistics.mean(r["tokens"] for r in agent)), cost_usd_total=round(sum(costs), 4) if costs else None,
                 cost_usd_per_case=round(sum(costs) / len(costs), 5) if costs else None,
                 tool_calls_mean=round(statistics.mean(r["tool_calls"] for r in agent), 2), loop_guard_trips=sum(bool(r["loop_guard"]) for r in agent),
                 required_proposed_recall=pct(sum(set(r["expected_indicators"].split(";")) <= split_codes(r, "proposed") for r in agent if r["kind"] == "suspicious"),
                                              sum(r["kind"] == "suspicious" for r in agent)),
                 verifier_removed_true_signal=sum(bool(set(r["expected_indicators"].split(";")) & split_codes(r, "verifier_removed")) for r in agent if r["kind"] == "suspicious"),
                 verifier_removed_false_signal=sum(bool(split_codes(r, "verifier_removed") & set(filter(None, r["expected_absent"].split(";")))) for r in agent if r["kind"] == "honest"),
                 investigator_false_signal=sum(bool(split_codes(r, "proposed") & set(filter(None, r["expected_absent"].split(";")))) for r in agent if r["kind"] == "honest"),
                 explainer_fallbacks=sum(r["explainer_ok"] is False for r in agent),
                 agent_noticed_missing=pct(sum(DOC_ALIAS.get(r["expected_missing"], r["expected_missing"]) in r["agent_missing"].split(";") for r in agent if r["kind"] == "missing"),
                                           sum(r["kind"] == "missing" for r in agent)))
    m["confusion"] = {e: {p: sum(1 for r in rows if r["expected"] == e and r["predicted"] == p) for p in BANDS} for e in BANDS}
    by_scn = {}
    for sid in sorted({r["scenario"] for r in rows}):
        s = [r for r in rows if r["scenario"] == sid]
        su, ho, mi = ([r for r in s if r["kind"] == k] for k in ("suspicious", "honest", "missing"))
        by_scn[sid] = dict(suspicious_red=f"{sum(r['predicted'] == r['expected'] for r in su)}/{len(su)}",
                           honest_as_expected=f"{sum(r['predicted'] == r['expected'] for r in ho)}/{len(ho)}",
                           missing_amber=f"{sum(r['predicted'] == 'amber' for r in mi)}/{len(mi)}")
    m["by_scenario"] = by_scn
    fair = {}
    for name, key in (("gender", "gender"), ("area_flagged", "area_high")):
        for value in sorted({r[key] for r in hon}, key=str):
            g = [r for r in hon if r[key] == value]
            fair[f"{name}={value}"] = dict(n=len(g), red=pct(sum(r["predicted"] == "red" for r in g), len(g)),
                                           amber=pct(sum(r["predicted"] == "amber" for r in g), len(g)))
    m["fairness_honest_cases"] = fair
    m["area_only_flags"] = sum(1 for r in rows if fired(r) == {"HIGH_FRAUD_AREA"})
    return m


def verdict(metrics: dict) -> dict[str, str]:
    out = {}
    for key, (op, target) in TARGETS.items():
        v = metrics[key]
        ok = v is not None and (v <= target if op == "<=" else v >= target)
        out[key] = f"{'PASS' if ok else 'MISS'} ({v} vs {op} {target})"
    return out


def render(name: str, results: dict[str, dict]) -> str:
    lines = [f"# {name}: evaluation report", "",
             "Expected band: red for suspicious cases, review needed (amber) for missing-evidence cases, green for honest lookalikes, "
             "except that an honest claim from a high-fraud area is expected to be review needed (the area flag is policy). "
             "Weights are tuned on the dev split only; the held-out numbers are the ones to quote.", "",
             "How this baseline was built (read before trusting the numbers):", "",
             "- It uses no LLM: tools -> deterministic rules -> scorer. It reads only the tools' output, never the answer keys.",
             "- The first dev run exposed that five 'missing evidence' scenarios also carried a strong fraud signal, so labelling them amber was "
             "inconsistent. The data generator was changed so that missing-evidence cases are genuinely ambiguous; this changed all splits equally.",
             "- Two rule changes came from dev misses: an accident documented by a police report weakens the early-death signal, and the claimed "
             "damage is compared with every earlier inspection, not only the latest.",
             "- Several fixes came from data-consistency checks (impossible dates, deaths followed by activity), not from scores.",
             "- The remaining misses (a moderately inflated bill scored amber, and a weak ring plus an area flag scored red) were left untouched "
             "on purpose: they are real limits of fixed rules.",
             "- The scenarios are still fairly clean, so there is little headroom: a score near 100% shows the rules fit this synthetic data, not "
             "that fraud detection is solved. Harder, ambiguous cases are needed before the agents can show added value.", ""]
    keys = ["cases", "band_accuracy", "fraud_in_green", "fraud_red_recall", "fraud_flagged_recall", "legit_in_red", "legit_as_expected", "amber_rate", "missing_never_green",
            "missing_as_expected", "indicator_recall", "false_indicator_rate", "missing_item_recall", "evidence_coverage", "latency_ms_median", "latency_ms_p95"]
    lines += ["| Metric | " + " | ".join(results) + " | Target |", "|---|" + "---|" * (len(results) + 1)]
    first = next(iter(results.values()))
    keys += [k for k in ("unavailable", "tokens_per_case", "cost_usd_total", "cost_usd_per_case", "tool_calls_mean", "loop_guard_trips", "required_proposed_recall",
                         "investigator_false_signal", "verifier_removed_true_signal", "verifier_removed_false_signal", "explainer_fallbacks",
                         "agent_noticed_missing") if k in first]
    for k in keys:
        target = f"{TARGETS[k][0]} {TARGETS[k][1]}" if k in TARGETS else ""
        lines.append(f"| {k} | " + " | ".join(str(r[k]) for r in results.values()) + f" | {target} |")
    for split, m in results.items():
        lines += ["", f"## {split}", "", "Targets: " + "; ".join(f"{k}: {v}" for k, v in verdict(m).items()), "",
                  "Confusion matrix (rows: expected, columns: predicted)", "", "| expected \\ predicted | red | amber | green |", "|---|---|---|---|"]
        lines += [f"| {e} | " + " | ".join(str(m["confusion"][e][p]) for p in BANDS) + " |" for e in BANDS]
        lines += ["", "Per scenario", "", "| Scenario | suspicious as expected | honest as expected | missing -> amber |", "|---|---|---|---|"]
        lines += [f"| {s} | {v['suspicious_red']} | {v['honest_as_expected']} | {v['missing_amber']} |" for s, v in m["by_scenario"].items()]
        lines += ["", "Fairness check on honest cases (share landing red / amber)", "", "| Slice | n | red | amber |", "|---|---|---|---|"]
        lines += [f"| {k} | {v['n']} | {v['red']} | {v['amber']} |" for k, v in m["fairness_honest_cases"].items()]
        lines += ["", f"Cases flagged only because of area history: {m['area_only_flags']}"]
    return "\n".join(lines) + "\n"


def write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def failures(rows: list[dict], limit: int = 40) -> list[dict]:
    return [r for r in rows if r["predicted"] != r["expected"]][:limit]


def sample_cells(cases: list[dict], per_cell: int) -> list[dict]:
    """A small stratified sample: the first `per_cell` cases of every (scenario, kind) cell."""
    seen: Counter = Counter()
    out = []
    for c in cases:
        key = (c["scenario_id"], c["kind"])
        if seen[key] < per_cell:
            seen[key] += 1
            out.append(c)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout", "all", "both"], default="both")
    ap.add_argument("--tier", choices=list(TIERS), default="standard", help="standard = the original 300 cases; hard = the document-reading and track-record probes")
    ap.add_argument("--system", choices=["baseline", "agents"], default="baseline")
    ap.add_argument("--plan", choices=["routed", "all_haiku", "all_sonnet"], default="routed", help="agents only: which Claude model the Investigator uses")
    ap.add_argument("--retrieval", choices=["none", "bm25", "graph", "hybrid", "hybrid_rerank"], default="none",
                    help="agents only: what the Investigator may search (none removes the knowledge tool)")
    ap.add_argument("--per-cell", type=int, help="small sample: this many cases per (scenario, kind) cell")
    ap.add_argument("--scenarios", help="comma-separated scenario IDs, e.g. H1,X4")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--name")
    ap.add_argument("--out")
    ap.add_argument("--show-failures", action="store_true")
    args = ap.parse_args()
    cfg, W = load_config(), load_weights()
    name = args.name or ("Rules-only baseline" if args.system == "baseline" else f"Agentic pipeline ({args.plan}, retrieval: {args.retrieval})")
    out = args.out or ("baseline" if args.system == "baseline" else f"agents_{args.plan}_{args.retrieval}")
    if args.tier != "standard" and not args.out:
        out += f"_{args.tier}"  # never overwrite the standard-tier report with a different set of cases
    if args.system == "agents":
        from ..pipeline import AgentPipeline
        trace_path = ROOT / "traces" / f"{out}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        trace_path.parent.mkdir(exist_ok=True)
        assess = lambda box, cid: AgentPipeline(box, W, plan=args.plan, retrieval=args.retrieval).assess(cid)  # noqa: E731
    else:
        trace_path, assess = None, lambda box, cid: assess_claim(box, cid, W)  # noqa: E731
    splits = ["dev", "heldout"] if args.split == "both" else [args.split]
    results, all_rows = {}, []
    for split in splits:
        cases = load_cases(cfg, split, args.tier)
        if args.scenarios:
            wanted = set(args.scenarios.split(","))
            cases = [c for c in cases if c["scenario_id"] in wanted]
        if args.per_cell:
            cases = sample_cells(cases, args.per_cell)
        print(f"[{split}] running {len(cases)} cases with {name} ...", flush=True)
        rows = run(cfg, cases, assess, graph=args.system == "agents", workers=args.workers, trace_path=trace_path, progress=args.system == "agents")
        results[split] = compute(rows)
        all_rows += rows
        if args.show_failures:
            print(f"--- {split}: misclassified ---")
            for r in failures(rows):
                print(f"  {r['case_id']} {r['scenario']} {r['kind']:<10} expected {r['expected']:<5} got {r['predicted']:<5} trust {r['trust']} fired={r['fired']} missing={r['predicted_missing']}")
    write_rows(all_rows, ROOT / "eval" / f"{out}_cases.csv")
    report = ROOT / "eval" / f"{out}_report.md"
    report.write_text(render(name, results), encoding="utf-8")
    for split, m in results.items():
        print(f"[{split}] cases={m['cases']} accuracy={m['band_accuracy']} fraud_in_green={m['fraud_in_green']} red_recall={m['fraud_red_recall']} "
              f"legit_in_red={m['legit_in_red']} amber_rate={m['amber_rate']} missing_never_green={m['missing_never_green']} "
              f"evidence_coverage={m['evidence_coverage']} p95={m['latency_ms_p95']}ms")
    print("report:", report)


if __name__ == "__main__":
    main()
