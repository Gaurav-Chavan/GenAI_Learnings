"""The Evaluation page: summaries computed from the result files, honest labels, and no per-claim answer keys."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from claim_triage.app.eval_view import (NOT_MEASURED, RUNS, SCENARIO_NAMES, confusion, fairness, knowledge_counts, load_runs, metrics, per_scenario, read_rows)  # noqa: E501
from claim_triage.data.config import ROOT

FIELDS = ["case_id", "claim_id", "scenario", "kind", "split", "expected", "predicted", "trust", "gender", "area_high", "latency_ms", "cost_usd", "explainer_ok"]


def write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**dict(case_id="C", claim_id="CLM-1", scenario="H1", kind="honest", split="dev", expected="green", predicted="green", trust="0.9", gender="F",
                               area_high="False", latency_ms="1500", cost_usd="0.05"), **r})
    return path


def rows_of(tmp_path, spec: list[dict]) -> list[dict]:
    return read_rows(write_csv(tmp_path / "x.csv", spec))


def test_metrics_count_the_right_things(tmp_path):
    rows = rows_of(tmp_path, [
        dict(kind="suspicious", expected="red", predicted="red"), dict(kind="suspicious", expected="red", predicted="green"),
        dict(kind="suspicious", expected="amber", predicted="amber"), dict(kind="honest", expected="green", predicted="red"),
        dict(kind="honest", expected="green", predicted="green"), dict(kind="missing", expected="amber", predicted="unavailable")])
    m = metrics(rows)
    assert m["cases"] == 6 and m["right_band"] == pytest.approx(0.5) and m["fraud_called_green"] == pytest.approx(0.333, abs=1e-3)
    assert m["fraud_flagged"] == pytest.approx(0.667, abs=1e-3) and m["honest_called_red"] == 0.5 and m["unavailable"] == 1
    assert m["cost_total"] == 0.3 and m["cost_per_case"] == 0.05 and m["seconds_median"] == 1.5


def test_rules_only_runs_show_no_cost_and_no_timing(tmp_path):
    m = metrics(rows_of(tmp_path, [dict(cost_usd="", latency_ms="12"), dict(cost_usd="", latency_ms="9")]))
    assert m["cost_total"] is None and m["cost_per_case"] is None and m["seconds_median"] is None


def test_explainer_fallbacks_are_counted_and_the_rules_only_system_has_none(tmp_path):
    m = metrics(rows_of(tmp_path, [dict(explainer_ok="True"), dict(explainer_ok="False"), dict(explainer_ok="False"), dict(explainer_ok=""), dict(predicted="unavailable")]))
    assert (m["explainer_runs"], m["explainer_fallbacks"]) == (3, 2)
    rules = metrics(rows_of(tmp_path, [dict(), dict()]))
    assert (rules["explainer_runs"], rules["explainer_fallbacks"]) == (0, 0)


def test_empty_groups_give_none_not_a_crash(tmp_path):
    m = metrics(rows_of(tmp_path, [dict(kind="honest")]))
    assert m["fraud_called_green"] is None and m["fraud_flagged"] is None


def test_confusion_and_per_scenario(tmp_path):
    rows = rows_of(tmp_path, [dict(scenario="D2", kind="suspicious", expected="amber", predicted="green"), dict(scenario="D2", kind="suspicious", expected="amber", predicted="amber"),
                              dict(scenario="H1", kind="honest", expected="green", predicted="red")])
    assert confusion(rows)["amber"] == {"red": 0, "amber": 1, "green": 1} and confusion(rows)["green"]["red"] == 1
    sc = per_scenario(rows)
    assert [s["scenario"] for s in sc] == ["H1", "D2"]  # hard scenarios last
    d2 = sc[1]
    assert (d2["cases"], d2["right"], d2["fraud_called_green"], d2["name"]) == (2, 1, 1, SCENARIO_NAMES["D2"])
    assert sc[0]["honest_called_red"] == 1


def test_fairness_compares_groups_of_honest_claims_only(tmp_path):
    rows = rows_of(tmp_path, [dict(gender="F", predicted="red"), dict(gender="F", predicted="green"), dict(gender="M", predicted="amber", area_high="True"),
                              dict(kind="suspicious", gender="M", predicted="red")])
    f = {x["slice"]: x for x in fairness(rows)}
    assert f["Gender: F"]["honest_claims"] == 2 and f["Gender: F"]["called_red"] == 0.5 and f["Gender: M"]["honest_claims"] == 1
    assert f["Area flagged: True"]["sent_to_review"] == 1.0


def test_load_runs_splits_files_hides_trials_and_orders_by_presentation(tmp_path):
    write_csv(tmp_path / "baseline_cases.csv", [dict(split="dev"), dict(split="heldout")])
    write_csv(tmp_path / "agents_trial_cases.csv", [dict()])  # hidden
    write_csv(tmp_path / "agents_sample3_cases.csv", [dict()])
    write_csv(tmp_path / "mystery_cases.csv", [dict()])  # unknown file: plain title, no commentary
    runs = load_runs(tmp_path)
    assert [r["key"] for r in runs] == ["baseline:dev", "baseline:heldout", "agents_sample3:dev", "mystery:dev"]
    assert runs[0]["label"].endswith("(dev)") and runs[1]["label"].endswith("(held-out)") and runs[3]["label"] == "Mystery" and runs[3]["note"] == ""
    assert "tuned on these exact cases" in runs[2]["note"]


def test_the_page_never_carries_a_claims_expected_answer(tmp_path):
    runs = load_runs(tmp_path) or [dict(rows=rows_of(tmp_path, [dict()]))]
    for r in runs:
        assert not any(k in r for k in ("claims", "misses", "failures"))
    from claim_triage.app import eval_view

    source = Path(eval_view.__file__).read_text(encoding="utf-8")
    assert "labels.duckdb" not in source and '["labels"]' not in source and "labels_hard" not in source


def test_every_agent_run_is_labelled_with_a_caveat_and_the_gaps_are_listed():
    for prefix, meta in RUNS.items():
        if prefix.startswith(("agents_", "trial_", "haiku_")) and not meta.get("hide"):
            assert meta["note"], prefix
    assert RUNS["agents_sample3"]["note"].startswith("Looks perfect, but we tuned")
    assert len(NOT_MEASURED) >= 4 and any("larger model" in n for n in NOT_MEASURED)  # the main open question is listed, not hidden


def test_knowledge_counts_match_the_knowledge_files():
    assert knowledge_counts() == {"fraud_patterns": 12, "policy_clauses": 14}


@pytest.mark.skipif(not (ROOT / "eval" / "baseline_cases.csv").exists(), reason="run the baseline first")
def test_real_result_files_load_and_agree_with_the_harness_report():
    runs = {r["key"]: r for r in load_runs()}
    base = runs["baseline:dev"]["metrics"]
    assert base["cases"] == 200 and base["right_band"] >= 0.98 and base["cost_total"] is None
    hard = runs["baseline_hard:dev"]["metrics"]
    assert hard["right_band"] == 0.5 and hard["fraud_called_green"] == 0.5  # rules cannot read text or look up a hospital's record
    if "haiku_none_std:dev" in runs:  # the full small-model dev runs: safe, but below the rules on the clean original data
        for key in ("haiku_none_std:dev", "haiku_rerank_std:dev"):
            m = runs[key]["metrics"]
            assert m["cases"] == 200 and m["fraud_called_green"] == 0 and m["honest_called_red"] == 0 and m["right_band"] < base["right_band"]
        assert runs["haiku_rerank_hard:dev"]["metrics"]["right_band"] > runs["haiku_none_hard:dev"]["metrics"]["right_band"] > hard["right_band"]
    if "routed_rerank_x12:dev" in runs:  # the larger model on X1 and X2: much better than the small model on the same cases, still below the rules
        same = lambda key: [r for r in runs[key]["rows"] if r["scenario"] in ("X1", "X2")]  # noqa: E731
        big, small = runs["routed_rerank_x12:dev"]["metrics"], metrics(same("haiku_rerank_std:dev"))
        assert big["cases"] == 40 and big["fraud_called_green"] == 0 and small["right_band"] < big["right_band"] < 1.0
    if "haiku_none_std_heldout:heldout" in runs:  # the held-out result agrees with dev, so the dev finding was not an overfit
        for key in ("haiku_none_std_heldout:heldout", "haiku_rerank_std_heldout:heldout"):
            m = runs[key]["metrics"]
            assert m["cases"] == 100 and m["fraud_called_green"] == 0 and m["honest_called_red"] == 0 and m["right_band"] < runs["baseline:heldout"]["metrics"]["right_band"]
        assert runs["haiku_rerank_hard_heldout:heldout"]["metrics"]["right_band"] == 1.0 > runs["haiku_none_hard_heldout:heldout"]["metrics"]["right_band"]


def test_the_evaluation_page_renders(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CLAIM_TRIAGE_DATA_DIR", str(tmp_path))  # a scratch folder, so the test never touches (or creates) the real data/ folder
    monkeypatch.setenv("CLAIM_TRIAGE_APP_DB", str(tmp_path / "app.duckdb"))
    at = AppTest.from_file(str(ROOT / "src" / "claim_triage" / "app" / "dashboard.py"), default_timeout=60)
    at.session_state["page"] = "Evaluation"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value == "Evaluation" and any("Read this first" in w.value for w in at.warning)
    titles = [s.value for s in at.subheader]
    assert {"Runs so far", "Not measured yet", "Fairness check (rules only)"} <= set(titles)
