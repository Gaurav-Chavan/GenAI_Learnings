"""Regression tests for the rules-only baseline against the labelled cases (offline: no LLM, no graph)."""
import pytest

from claim_triage.baseline import assess_claim
from claim_triage.evaluation.harness import compute, load_cases, run, verdict
from claim_triage.scoring.scorer import load_weights
from claim_triage.tools import ToolBox

W = load_weights()


@pytest.fixture(scope="module")
def rows(built_dataset):
    cfg, _ = built_dataset
    return run(cfg, load_cases(cfg, "all"), lambda box, cid: assess_claim(box, cid, W))


def test_baseline_meets_the_design_targets_on_dev_and_heldout(rows):
    for split in ("dev", "heldout"):
        m = compute([r for r in rows if r["split"] == split])
        assert all(v.startswith("PASS") for v in verdict(m).values()), (split, verdict(m))


def test_missing_evidence_is_never_green_and_every_indicator_is_backed_by_evidence(rows):
    assert all(r["predicted"] != "green" for r in rows if r["kind"] == "missing")
    assert all(not r["dropped"] for r in rows)


def test_the_baseline_names_the_missing_document_it_was_asked_about(rows):
    m = compute(rows)
    assert m["missing_item_recall"] == 1.0


def test_results_are_deterministic(built_dataset, rows):
    cfg, _ = built_dataset
    again = run(cfg, load_cases(cfg, "dev")[:40], lambda box, cid: assess_claim(box, cid, W))
    first = {r["case_id"]: (r["trust"], r["predicted"], r["fired"]) for r in rows}
    assert all(first[r["case_id"]] == (r["trust"], r["predicted"], r["fired"]) for r in again)


def test_an_assessment_is_readable_and_cites_real_evidence(built_dataset):
    cfg, _ = built_dataset
    box = ToolBox(cfg, graph=False)
    case = next(r for r in load_cases(cfg, "dev") if r["scenario_id"] == "H1" and r["kind"] == "suspicious")
    result = assess_claim(box, case["claim_id"], W)
    assert result.band == "red" and result.trust_score <= 0.29
    assert "earlier claim" in result.reasons[0] and result.indicators[0].evidence_ids
    assert box.unknown_evidence(result.indicators[0].evidence_ids) == []
    assert "fraud proven" not in " ".join(result.reasons).lower()
    box.close()


def test_the_baseline_code_never_touches_labels():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "claim_triage"
    for path in [root / "baseline.py", *root.joinpath("scoring").glob("*.py"), *root.joinpath("tools").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert "evaluation" not in text.replace("evaluation harness", "") or path.name == "baseline.py" and "from .evaluation" not in text
        assert 'paths(cfg)["labels"]' not in text and "labels.duckdb" not in text
