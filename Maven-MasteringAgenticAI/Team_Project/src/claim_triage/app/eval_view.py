"""What the Evaluation page shows: the runs we have actually done, summarised from the result files in eval/ and labelled with what they do and do not prove.

It reads only those result files (never the answer-key database) and shows totals, never the expected answer for an individual claim.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

from ..data.config import ROOT

BANDS = ("red", "amber", "green")
SCENARIO_NAMES = {
    "H1": "Duplicate health claim", "H2": "Inflated billing vs peers", "T1": "Nominee changed before death", "T2": "Conflicting death dates",
    "M1": "Repeat motor claim", "M2": "Damage already in an earlier inspection", "X1": "Undeclared pre-existing condition", "X2": "Early death vs health history",
    "X3": "Same accident under two policies", "X4": "Ring sharing a payout account",
    "D1": "Big bill: reason is (or is not) in the discharge summary", "D2": "Slightly high bill at a hospital with a bad record",
}
# file prefix in eval/ -> how to present it. Anything not listed is shown with a plain title and no commentary. `hide` keeps trivial trials off the page.
RUNS = {
    "baseline": dict(title="Rules only: the original 300 cases", system="Rules (no AI)", order=1,
                     note="No AI. Near-perfect because the data is clean and the rules were written for it: it shows the rules fit this data, not that fraud detection is solved."),
    "baseline_hard": dict(title="Rules only: the 80 hard cases", system="Rules (no AI)", order=2,
                          note="Rules cannot read text or look up a hospital's record. They call every large bill suspicious and miss slightly-high bills at hospitals with a bad record."),
    "agents_sample": dict(title="3 AI agents: first try (30 original dev cases)", system="3 AI agents", order=3,
                          note="The second-opinion agent doubted almost everything, so most fraud landed in 'review needed' instead of red."),
    "agents_sample2": dict(title="3 AI agents: after fix 1 (same 30 cases)", system="3 AI agents", order=4,
                           note="The second opinion now has to name a real supporting fact, but it still cited facts that excuse nothing."),
    "agents_sample3": dict(title="3 AI agents: after fix 2 (same 30 cases)", system="3 AI agents", order=5,
                           note="Looks perfect, but we tuned on these exact cases, so treat it as a smoke test, not as evidence."),
    "trial_hard_none": dict(title="8-case hard trial, no retrieval", system="3 AI agents, no retrieval", order=96, hide=True, note=""),  # superseded by the full dev runs below
    "trial_hard_hybrid_rerank": dict(title="8-case hard trial, with retrieval", system="3 AI agents + retrieval", order=97, hide=True, note=""),
    "haiku_none_std": dict(title="3 AI agents (small model), no retrieval: all 200 original dev cases", system="3 AI agents, no retrieval", order=8,
                           note="First full dev run. Never put fraud in green or an honest claim in red, but clearly worse than the rules: 16 of 70 real frauds went to 'review needed' "
                                "instead of red, mostly on cross-policy claims, where this run used the small model instead of the larger one."),
    "haiku_rerank_std": dict(title="3 AI agents (small model) with retrieval: all 200 original dev cases", system="3 AI agents + retrieval", order=9,
                             note="Same, with retrieval: 93% against 91.5%. The gap is 3 cases, which is noise. Still well below the rules (99.5%) on this clean data."),
    "haiku_none_hard": dict(title="3 AI agents (small model), no retrieval: all 52 hard dev cases", system="3 AI agents, no retrieval", order=10,
                            note="Reads the discharge summaries (24 of 26 right) but misses every bad-hospital case: all 13 went to green."),
    "haiku_rerank_hard": dict(title="3 AI agents (small model) with retrieval: all 52 hard dev cases", system="3 AI agents + retrieval", order=11,
                              note="52 of 52. Retrieval is what makes a hospital's record visible. Note the checklist tells the agent what to do with that record, so this shows the "
                                   "information matters, not that the agent discovered the pattern by itself."),
    "routed_rerank_x12": dict(title="3 AI agents, LARGER model for cross-policy claims + retrieval: X1 and X2 dev (40 cases)", system="3 AI agents + retrieval (larger model)", order=11.5,
                              note="On the same 40 cases the small model scored 65% (77.5% with retrieval) and the rules 100%. The larger model reaches 95% and calls 12 of 14 frauds red "
                                   "(the small model: 1 and 5), so much of the gap was the small model. It costs about 18 times more per claim ($0.07 against $0.004). Dev only, two scenarios."),
    "haiku_none_std_heldout": dict(title="HELD-OUT: 3 AI agents, no retrieval: all 100 original cases", system="3 AI agents, no retrieval", order=12,
                                   note="The number to quote. 93%, the same as on dev (91.5%), so the dev result held. Safe (no fraud in green, no honest claim in red) but below the rules (99%): "
                                        "6 of 30 real frauds went to 'review needed' instead of red."),
    "haiku_rerank_std_heldout": dict(title="HELD-OUT: 3 AI agents with retrieval: all 100 original cases", system="3 AI agents + retrieval", order=13,
                                     note="92% (93% on dev). Retrieval makes no difference on the original data."),
    "haiku_none_hard_heldout": dict(title="HELD-OUT: 3 AI agents, no retrieval: all 28 hard cases", system="3 AI agents, no retrieval", order=14,
                                    note="71%, as on dev. All 7 bad-hospital frauds went to green. One claim could not be assessed (a model error) and counts as wrong."),
    "haiku_rerank_hard_heldout": dict(title="HELD-OUT: 3 AI agents with retrieval: all 28 hard cases", system="3 AI agents + retrieval", order=15,
                                      note="28 of 28, as on dev. Again this shows the hospital's record matters, because the checklist says how to use it."),
    "agents_trial": dict(title="3-case trial", system="3 AI agents", order=99, hide=True, note=""),
}
NOT_MEASURED = [
    "The larger model on the other cross-policy scenarios (X3, X4) and on the held-out split. On X1 and X2 dev it closed most of the gap (95% against 65-78%) at about 18 times the cost per claim.",
    "Stability: the same claim run three times, to see whether the answer changes.",
    "Explanation quality scored by an independent judge model, and fairness checks for the agents (the rules-only fairness table below is the only one).",
    "Whether approved knowledge-base signals improve agent decisions (the safety check only tests the search).",
]


def knowledge_counts() -> dict[str, int]:
    """How many pattern cards and policy clauses the knowledge base starts with (read from the knowledge files, so it cannot drift)."""
    import yaml

    out = {}
    for name in ("fraud_patterns", "policy_clauses"):
        with open(ROOT / "knowledge" / f"{name}.yaml", encoding="utf-8") as fh:
            out[name] = len(yaml.safe_load(fh))
    return out


def _num(text: str, kind=float):
    try:
        return kind(text) if text not in ("", None) else None
    except ValueError:
        return None


def read_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["trust"], r["cost_usd"], r["latency_ms"] = _num(r.get("trust")), _num(r.get("cost_usd")), _num(r.get("latency_ms"), int)
        r["area_high"] = r.get("area_high") == "True"
    return rows


def pct(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def metrics(rows: list[dict]) -> dict:
    sus = [r for r in rows if r["kind"] == "suspicious"]
    hon = [r for r in rows if r["kind"] == "honest"]
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return dict(cases=len(rows), right_band=pct(sum(r["predicted"] == r["expected"] for r in rows), len(rows)),
                fraud_called_green=pct(sum(r["predicted"] == "green" for r in sus), len(sus)),
                fraud_flagged=pct(sum(r["predicted"] in ("red", "amber") for r in sus), len(sus)),
                honest_called_red=pct(sum(r["predicted"] == "red" for r in hon), len(hon)),
                unavailable=sum(r["predicted"] == "unavailable" for r in rows),
                explainer_runs=sum(r.get("explainer_ok") in ("True", "False") for r in rows),  # empty for the rules-only system and for claims the system could not assess
                explainer_fallbacks=sum(r.get("explainer_ok") == "False" for r in rows),
                cost_total=round(sum(costs), 2) if costs and sum(costs) > 0 else None,
                cost_per_case=round(sum(costs) / len(costs), 4) if costs and sum(costs) > 0 else None,
                seconds_median=round(statistics.median(r["latency_ms"] for r in rows if r["latency_ms"] is not None) / 1000, 1)
                if any(r["latency_ms"] is not None for r in rows) and sum(r["latency_ms"] or 0 for r in rows) > 1000 else None)


def confusion(rows: list[dict]) -> dict[str, dict[str, int]]:
    return {e: {p: sum(1 for r in rows if r["expected"] == e and r["predicted"] == p) for p in BANDS} for e in BANDS}


def per_scenario(rows: list[dict]) -> list[dict]:
    out = []
    for sid in sorted({r["scenario"] for r in rows}, key=lambda s: (s[0] == "D", s)):
        s = [r for r in rows if r["scenario"] == sid]
        out.append(dict(scenario=sid, name=SCENARIO_NAMES.get(sid, sid), cases=len(s), right=sum(r["predicted"] == r["expected"] for r in s),
                        fraud_called_green=sum(r["kind"] == "suspicious" and r["predicted"] == "green" for r in s),
                        honest_called_red=sum(r["kind"] == "honest" and r["predicted"] == "red" for r in s)))
    return out


def fairness(rows: list[dict]) -> list[dict]:
    """Of the honest claims, how many landed red or in review, by gender and by whether the area flag fired (a gap would be worth investigating)."""
    honest, out = [r for r in rows if r["kind"] == "honest"], []
    for label, key in (("Gender", "gender"), ("Area flagged", "area_high")):
        for value in sorted({r[key] for r in honest}, key=str):
            g = [r for r in honest if r[key] == value]
            out.append(dict(slice=f"{label}: {value}", honest_claims=len(g), called_red=pct(sum(r["predicted"] == "red" for r in g), len(g)),
                            sent_to_review=pct(sum(r["predicted"] == "amber" for r in g), len(g))))
    return out


def load_runs(eval_dir: Path | None = None) -> list[dict]:
    """One entry per result file, in presentation order; a file that has both splits yields a dev entry and a held-out entry."""
    folder = eval_dir or ROOT / "eval"
    runs = []
    for path in sorted(folder.glob("*_cases.csv")):
        prefix = path.name.removesuffix("_cases.csv")
        meta = RUNS.get(prefix, dict(title=prefix.replace("_", " ").capitalize(), system="-", order=50, note=""))
        if meta.get("hide"):
            continue
        rows = read_rows(path)
        for split in ("dev", "heldout"):
            part = [r for r in rows if r["split"] == split]
            if part:
                runs.append(dict(key=f"{prefix}:{split}", prefix=prefix, split=split, label=f"{meta['title']} ({'held-out' if split == 'heldout' else 'dev'})" if len({r['split'] for r in rows}) > 1 else meta["title"],
                                 system=meta["system"], note=meta["note"], order=meta["order"], rows=part, metrics=metrics(part)))
    return sorted(runs, key=lambda r: (r["order"], r["split"] != "dev"))
