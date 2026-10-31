"""Run both systems on eval/queries.yaml and score them.

    uv run python -m orggraph_rag.evaluate               # all questions, both systems
    uv run python -m orggraph_rag.evaluate --only Q1 Q8  # a subset

Per question x system:
  correctness     LLM judge (gpt-4o) vs. the reference answer: 1 / 0.5 / 0
  faithfulness    LLM judge: are the answer's claims supported by the retrieved context?
                  1 / 0.5 / 0 (blank for refusals - nothing to check)
  entity_recall   share of expected_entities present in the answer (string match)
  distractors     must_not strings that appear in the answer
  refusal_ok      refused exactly when the question is unanswerable
  latency_s, tokens (all LLM calls of the pipeline), citations

Writes eval/results.csv, eval/results.jsonl (full answers, context, Cypher) and
eval/summary.md (overall, per-category and per-question tables).
"""

import argparse
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal

import yaml
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from . import graph_rag, vector_rag
from .config import get_chat_llm, settings
from .generation import RAGResult, format_context

QUERIES_FILE = settings.eval_dir / "queries.yaml"
SYSTEMS: Dict[str, Callable[[str], RAGResult]] = {"graph": graph_rag.answer, "vector": vector_rag.answer}

# ---------------------------------------------------------------- judges

class Grade(BaseModel):
    score: Literal[0, 0.5, 1]
    reason: str


CORRECTNESS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You grade an assistant's answer against a reference answer.

score 1   - every key fact of the reference is present and nothing stated is wrong.
score 0.5 - partially correct: some key facts missing, or a minor error.
score 0   - wrong, mostly missing, or a refusal when the reference has an answer.
If the reference says the information does not exist, score 1 only if the assistant
declines to answer, and 0 if it invents an answer.
Extra true details beyond the reference are fine. Wording and format do not matter.
Give a one-sentence reason."""),
    ("human", "Question: {question}\n\nReference answer: {expected}\n\nAssistant answer: {answer}"),
])

FAITHFULNESS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You check whether an answer is grounded in the context it was given.

score 1   - every factual claim in the answer is supported by the context.
score 0.5 - mostly supported, with a minor unsupported detail.
score 0   - contains significant claims not supported by the context.
Judge only support by the context, not real-world truth. Give a one-sentence reason."""),
    ("human", "Context:\n{context}\n\nAnswer: {answer}"),
])


def judge(prompt: ChatPromptTemplate, **inputs) -> Grade:
    return (prompt | get_chat_llm(settings.judge_model).with_structured_output(Grade)).invoke(inputs)


# ---------------------------------------------------------------- string metrics

def normalize(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())


def entity_recall(answer: str, expected: List[str]) -> float | None:
    if not expected:
        return None
    text = normalize(answer)
    return round(sum(normalize(e) in text for e in expected) / len(expected), 3)


def distractor_hits(answer: str, must_not: List[str]) -> List[str]:
    text = normalize(answer)
    return [d for d in must_not if normalize(d) in text]


# ---------------------------------------------------------------- run

def evaluate_one(query: Dict[str, Any], system: str) -> Dict[str, Any]:
    answerable = query.get("answerable", True)
    row: Dict[str, Any] = {"id": query["id"], "category": query["category"], "system": system,
                           "question": query["question"]}
    try:
        result = SYSTEMS[system](query["question"])
    except Exception as e:  # keep going; a crash counts as a wrong answer
        return {**row, "error": f"{type(e).__name__}: {e}", "correctness": 0, "refusal_ok": False}

    correctness = judge(CORRECTNESS_PROMPT, question=query["question"], expected=query["expected"],
                        answer=result.answer)
    faithfulness = None if result.refused else judge(
        FAITHFULNESS_PROMPT, context=format_context(result.sources), answer=result.answer)

    return {
        **row,
        "answer": result.answer,
        "correctness": correctness.score,
        "correctness_reason": correctness.reason,
        "faithfulness": faithfulness.score if faithfulness else None,
        "faithfulness_reason": faithfulness.reason if faithfulness else None,
        "entity_recall": entity_recall(result.answer, query.get("expected_entities", [])),
        "distractors": distractor_hits(result.answer, query.get("must_not", [])),
        "refused": result.refused,
        "refusal_ok": result.refused == (not answerable),
        "cited": bool(result.citations),
        "citations": result.citations,
        "latency_s": result.latency_s,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "path": " -> ".join(result.extra.get("path", [])),
        "cypher": result.extra.get("cypher"),
        "sources": [s.id for s in result.sources],
        "context": format_context(result.sources),
    }


def warm_up():
    graph_rag.warm_up()
    vector_rag.retrieve("warm up", k=1)


# ---------------------------------------------------------------- reports

CSV_FIELDS = ["id", "category", "system", "question", "correctness", "faithfulness", "entity_recall",
              "distractors", "refused", "refusal_ok", "cited", "latency_s", "input_tokens",
              "output_tokens", "path", "correctness_reason", "faithfulness_reason", "answer", "error"]


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 2) if values else None


def _p95(values) -> float:
    values = sorted(values)
    return values[max(0, round(0.95 * len(values)) - 1)]


def _fmt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def system_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    latencies = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    answered = [r for r in rows if not r.get("refused")]
    return {
        "Correctness (avg)": _mean(r["correctness"] for r in rows),
        "Fully correct": f"{sum(r['correctness'] == 1 for r in rows)}/{len(rows)}",
        "Faithfulness (avg)": _mean(r.get("faithfulness") for r in rows),
        "Entity recall (avg)": _mean(r.get("entity_recall") for r in rows),
        "Refusal behaviour OK": f"{sum(bool(r['refusal_ok']) for r in rows)}/{len(rows)}",
        "Distractor mentions": sum(len(r.get("distractors") or []) for r in rows),
        "Non-refusals with citations": f"{sum(bool(r.get('cited')) for r in answered)}/{len(answered)}",
        "Latency median (s)": round(statistics.median(latencies), 2) if latencies else None,
        "Latency p95 (s)": _p95(latencies) if latencies else None,
        "Tokens per question (avg)": _mean((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in rows),
    }


def write_summary(rows: List[Dict[str, Any]], path: Path):
    by_system = {s: [r for r in rows if r["system"] == s] for s in SYSTEMS}
    lines = [f"# Evaluation summary", "",
             f"Run {datetime.now():%Y-%m-%d %H:%M} · answer model `{settings.chat_model}` · "
             f"Cypher model `{settings.cypher_model}` · judge `{settings.judge_model}` · "
             f"{len(by_system['graph'])} questions", "",
             "## Overall", "", "| Metric | GraphRAG | Vector RAG |", "|---|---|---|"]
    summaries = {s: system_summary(r) for s, r in by_system.items()}
    for metric in summaries["graph"]:
        lines.append(f"| {metric} | {_fmt(summaries['graph'][metric])} | {_fmt(summaries['vector'][metric])} |")

    lines += ["", "## By category (avg correctness)", "", "| Category | Questions | GraphRAG | Vector RAG |",
              "|---|---|---|---|"]
    categories = list(dict.fromkeys(r["category"] for r in by_system["graph"]))
    for cat in categories:
        g = [r for r in by_system["graph"] if r["category"] == cat]
        v = [r for r in by_system["vector"] if r["category"] == cat]
        lines.append(f"| {cat} | {', '.join(r['id'] for r in g)} | {_fmt(_mean(r['correctness'] for r in g))} | "
                     f"{_fmt(_mean(r['correctness'] for r in v))} |")

    lines += ["", "## Per question", "",
              "| Q | Category | GraphRAG correct / faithful / recall | Vector correct / faithful / recall | Winner | GraphRAG path |",
              "|---|---|---|---|---|---|"]
    wins = {"GraphRAG": 0, "Vector RAG": 0, "Tie": 0}
    vector_by_id = {r["id"]: r for r in by_system["vector"]}
    for g in by_system["graph"]:
        v = vector_by_id[g["id"]]
        winner = "GraphRAG" if g["correctness"] > v["correctness"] else (
            "Vector RAG" if v["correctness"] > g["correctness"] else "Tie")
        wins[winner] += 1
        cell = lambda r: f"{_fmt(r['correctness'])} / {_fmt(r.get('faithfulness'))} / {_fmt(r.get('entity_recall'))}"
        lines.append(f"| {g['id']} | {g['category']} | {cell(g)} | {cell(v)} | {winner} | {g.get('path', '')} |")
    lines += ["", f"**Wins:** GraphRAG {wins['GraphRAG']} · Vector RAG {wins['Vector RAG']} · Tie {wins['Tie']}", ""]

    lines += ["## Judge notes on answers that were not fully correct", ""]
    for r in rows:
        if r["correctness"] != 1:
            name = "GraphRAG" if r["system"] == "graph" else "Vector RAG"
            lines.append(f"- **{r['id']} · {name} ({_fmt(r['correctness'])})**: "
                         f"{r.get('correctness_reason') or r.get('error')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="question ids to run, e.g. Q1 Q8")
    args = parser.parse_args()

    queries = yaml.safe_load(QUERIES_FILE.read_text(encoding="utf-8"))
    if args.only:
        queries = [q for q in queries if q["id"] in args.only]

    warm_up()
    rows = []
    for query in queries:
        for system in SYSTEMS:
            row = evaluate_one(query, system)
            rows.append(row)
            print(f"{row['id']:>4} {system:<6} correct={_fmt(row['correctness']):<4} "
                  f"faithful={_fmt(row.get('faithfulness')):<4} recall={_fmt(row.get('entity_recall')):<4} "
                  f"latency={_fmt(row.get('latency_s'))}s", flush=True)

    out = settings.eval_dir
    with open(out / "results.jsonl", "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    with open(out / "results.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "distractors": "; ".join(row.get("distractors") or [])})
    write_summary(rows, out / "summary.md")
    print(f"\nWrote {out / 'results.csv'}, results.jsonl and summary.md")


if __name__ == "__main__":
    main()
