# OrgGraph RAG — GraphRAG for Organizational Knowledge

Week 2 project (Project 3): model an organization's people, projects, skills, tools,
documents and decisions as a Neo4j knowledge graph, answer questions with **GraphRAG**,
and compare it against a **vector RAG** baseline on the same questions.

> **One-liner:** My RAG app helps PMs, tech leads and executives answer who/what/why
> questions about people, projects, tools and decisions from an org knowledge graph
> (~44 entities, 6 internal docs) in a Streamlit chat, with ≥90% answer correctness on
> multi-hop questions and <6s p95 latency.

## Results (14 questions)

| | GraphRAG | Vector RAG |
|---|---|---|
| Correctness (avg, human-reviewed) | **0.96** | 0.75 |
| Fully correct | **13 / 14** | 8 / 14 |
| Entity recall | **1.00** | 0.76 |
| Faithfulness | 1.00 | 1.00 |
| Median / p95 latency | 5.45 s / 5.91 s | **2.19 s / 3.85 s** |
| Tokens per question | 2,548 | **588** |

GraphRAG wins on aggregation ("who worked on more than one project") and two-hop questions
("who does Bob report to and what has that person approved"); on single-entity lookups the
two systems tie, and vector RAG is 2.5× faster. Full write-up:
[docs/comparison_analysis.md](docs/comparison_analysis.md).

## Tech stack

| Layer | Choice |
|---|---|
| Graph DB | Neo4j Aura Free (vector + full-text indexes) |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) |
| LLMs | `gpt-4o-mini` (answers), `gpt-4o` (Text2Cypher, eval judge) |
| Orchestration | LangChain + LangGraph |
| Vector baseline | Chroma |
| UI | Streamlit |

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill in `.env` (OpenAI key + Neo4j Aura credentials), then check everything connects:

```bash
uv run python -m orggraph_rag.config
```

Build the knowledge graph (idempotent — safe to re-run; prints a verification report):

```bash
uv run python -m orggraph_rag.ingest
```

If the database still holds the Session 2 tutorial's recruitment graph, the script stops
because its `:Skill` nodes and constraints collide with this graph. `--clear-db` deletes
**every** node in the database first:

```bash
uv run python -m orggraph_rag.ingest --clear-db
```

Build the vector RAG baseline (Chroma, one card per entity) and ask it a question:

```bash
uv run python -m orggraph_rag.vector_rag --build
```

```bash
uv run python -m orggraph_rag.vector_rag "Who approved the pricing change?"
```

Ask GraphRAG (prints the route taken, the generated Cypher, sources and citations):

```bash
uv run python -m orggraph_rag.graph_rag "Who does Bob report to, and what has that person approved?"
```

Print the LangGraph pipeline as a Mermaid diagram:

```bash
uv run python -m orggraph_rag.graph_rag --mermaid
```

Run the evaluation (both systems × 14 questions, `gpt-4o` judge; ~4 min; writes
`eval/results.csv`, `results.jsonl`, `summary.md`):

```bash
uv run python -m orggraph_rag.evaluate
```

Start the chatbot UI (Chat with side-by-side mode, Evaluation dashboard, Knowledge graph view):

```bash
uv run streamlit run app.py
```

Run the offline tests:

```bash
uv run pytest
```

## Deploy (Replit or Streamlit Community Cloud)

The graph already lives in Neo4j Aura, so a host only needs to run the Streamlit app.

1. **Secrets** — add `OPENAI_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
   in the host's Secrets panel. Never upload `.env`.
2. **Password (recommended on a public URL)** — add an `APP_PASSWORD` secret; the app then asks for it,
   so strangers can't spend your OpenAI credits.
3. **Chroma index** — nothing to do: `chroma_db/` isn't in git, and the app builds it on first start
   (~44 embedding calls).

**Streamlit Community Cloud (recommended):** at [share.streamlit.io](https://share.streamlit.io) → *Create app* →
repo `Gaurav-Chavan/GenAI_Learnings`, branch `main`, main file
`Maven-MasteringAgenticAI/Week2_Project/app.py`; under *Advanced settings* pick Python 3.12 and paste the
secrets as TOML (`OPENAI_API_KEY = "..."` etc.). It installs from `requirements.txt` next to `app.py`, and
`app.py` puts `src/` on the import path itself, so the subfolder layout works. After dependency changes:
`uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt -o requirements.txt`.


Notes: Aura Free pauses when idle — resume it before a demo. Files written at runtime
(`feedback.jsonl`, `chroma_db/`) are not permanent on most hosts.

## Project layout

```
src/orggraph_rag/
  config.py               settings + Neo4j/OpenAI client factories
  verbalize.py            node -> text, and entity cards (node + all its relationships)
  ingest.py               Neo4j schema, nodes, edges, chunks, embeddings, verification
  generation.py           shared answer prompt, refusal rule, RAGResult (used by both systems)
  vector_rag.py           vector RAG baseline (Chroma, top-k = 4)
  prompts.py              GraphRAG prompts: router, Text2Cypher (schema + few-shots), grader
  graph_rag.py            GraphRAG LangGraph pipeline (Text2Cypher -> grade -> anchor fallback)
  evaluate.py             eval harness: LLM judges, entity recall, refusals, latency, reports
  ui_graph.py             interactive graph rendering (vis-network) for the UI
  data/mock_data.py       original dataset (32 nodes, 48 edges) — untouched
  data/mock_data_ext.py   extension (44 nodes, 75 edges) + graph schema
app.py                    Streamlit chatbot UI
tests/                    offline tests (dataset, gold answers, cards, Cypher safety, metrics)
eval/queries.yaml         14 questions with reference answers and expected entities
eval/results.*            latest evaluation run (csv, jsonl, summary.md)
docs/PLAN.md              build plan, design decisions, iteration log
docs/comparison_analysis.md   GraphRAG vs. vector RAG analysis (deliverable)
```
