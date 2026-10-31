"""Offline checks on the eval set and the string metrics (no Neo4j/OpenAI)."""

import yaml

from orggraph_rag.config import settings
from orggraph_rag.evaluate import _p95, distractor_hits, entity_recall, normalize, system_summary

QUERIES = yaml.safe_load((settings.eval_dir / "queries.yaml").read_text(encoding="utf-8"))


def test_eval_set_shape():
    assert len(QUERIES) == 14  # handout asks for at least 10
    assert len({q["id"] for q in QUERIES}) == len(QUERIES)
    for q in QUERIES:
        assert {"id", "category", "question", "expected"} <= q.keys()


def test_reference_answers_contain_their_own_expected_entities():
    # Guards against typos in expected_entities that would make recall unreachable.
    for q in QUERIES:
        if q.get("expected_entities"):
            assert entity_recall(q["expected"], q["expected_entities"]) == 1.0, q["id"]


def test_only_unanswerable_questions_lack_expected_entities():
    for q in QUERIES:
        assert bool(q.get("expected_entities")) == q.get("answerable", True), q["id"]


def test_normalize_ignores_case_hyphens_and_spacing():
    assert normalize("Zero-Trust  Security") == normalize("zero trust security")


def test_entity_recall_and_distractors():
    answer = "Alice Chen and Bob Martinez used Drata and Okta."
    assert entity_recall(answer, ["Alice Chen", "Frank Lee", "Drata", "Okta"]) == 0.75
    assert entity_recall(answer, []) is None
    assert distractor_hits(answer, ["Henry Okafor", "drata"]) == ["drata"]


def test_p95():
    assert _p95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 10
    assert _p95([3.0]) == 3.0


def test_system_summary_skips_missing_faithfulness():
    rows = [
        {"correctness": 1, "faithfulness": 1, "entity_recall": 1.0, "refusal_ok": True, "distractors": [],
         "cited": True, "latency_s": 2.0, "input_tokens": 100, "output_tokens": 10},
        {"correctness": 0.5, "faithfulness": None, "entity_recall": None, "refusal_ok": True, "distractors": ["x"],
         "cited": False, "latency_s": 4.0, "input_tokens": 50, "output_tokens": 5},
    ]
    s = system_summary(rows)
    assert s["Correctness (avg)"] == 0.75
    assert s["Faithfulness (avg)"] == 1
    assert s["Distractor mentions"] == 1
    assert s["Fully correct"] == "1/2"
