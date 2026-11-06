# Insurance Claim Triage

A reviewer dashboard that gives every insurance claim (health, term life, motor) a **trust score from 0 to 1**, a colour band and plain reasons.
An agentic system with hybrid retrieval (graph + BM25) investigates; **a human reviewer decides every claim**. Reviewer-approved signals can be added to
a knowledge base, but only after a safety check. Everything runs on **synthetic data**; no real personal data is used.

| Band | Trust score | Meaning |
|---|---|---|
| Red | below 0.3 | strong signs of a problem, with reasons and evidence |
| Review needed | 0.3 to 0.8 | something to check, or evidence missing |
| Green | above 0.8 | nothing found by the checks that were run. **Not an approval.** |

## Where things stand

Read [docs/DESIGN.md](docs/DESIGN.md) section 0 for the full status. In short: the system works end to end and its guard rails are tested (198 tests), but
**on the clean original data the plain rules beat the agents** (99–99.5% against 91.5–93% right band on dev and held-out, with the small model; the two splits agree).
On that data the agents never put fraud in green, but they send some real fraud to "review needed" instead of red. They win on 80 "hard" cases designed to need reading
documents and looking up past decisions (71% without retrieval and 100% with it on both splits, against 50% for the rules). On the two weakest original scenarios (X1, X2) the larger model closed most of the gap
(95% against 65–78% for the small model; rules 100%) at about 18 times the cost per claim. Agent runs so far cost about $10.4.

## Run it

You need [uv](https://docs.astral.sh/uv/). Behind TLS inspection (common on work laptops) add `--native-tls`.

```bash
uv sync --native-tls
```

The project pins Python 3.14 (`.python-version`; `pyproject.toml` allows 3.11 or newer) and `uv` installs it for you.

Secrets stay out of git (both files are in `.gitignore`): put your model keys in `API_Keys.txt` as `NAME=value` lines (see `.env.example` for the names), and keep
the Neo4j Aura credentials file as downloaded (`Neo4j-*.txt`). The keys are only needed for agent runs, not for the dashboard.

Build the data (deterministic; about 15 seconds), then load the graph. **`--clear` deletes every node in the target Neo4j database**, so point it at
your own empty instance:

```bash
uv run python -m claim_triage.data.build
uv run python -m claim_triage.data.load_neo4j --clear
```

Fill the dashboard's database (free: it runs the rules-only system and stores saved agent runs) and open the dashboard:

```bash
uv run python -m claim_triage.app.backfill
uv run streamlit run src/claim_triage/app/dashboard.py
```

Run the tests (the graph tests skip themselves if Neo4j is unreachable):

```bash
uv run pytest
```

## Public demo on Streamlit Community Cloud

The repository carries a ready-made demo: `deploy/streamlit_app.py` runs the dashboard on `demo_data/` (synthetic claims plus the stored assessments; no decisions,
no lessons, no identity data, no answer keys). It needs **no API keys**. In demo mode the safety check for lessons is switched off (it would call paid models), and
every visitor's session works on a private copy of the app database, so decisions are never shared and disappear when the app restarts.

To publish it: push the repository to GitHub, then on [share.streamlit.io](https://share.streamlit.io) choose **Create app**, pick the repository and branch, and set
the main file path to the entry file's path from the repository root. If this project sits in a subfolder, that is `<subfolder>/deploy/streamlit_app.py`
(for example `Maven-MasteringAgenticAI/Team_Project/deploy/streamlit_app.py`). Under **Advanced settings** choose Python 3.12 or 3.13 and leave **Secrets** empty.
The app installs only `deploy/requirements.txt`, because Streamlit looks for a dependency file next to the entry file before the repository root.

Try it locally first: `uv run streamlit run deploy/streamlit_app.py`. To rebuild the snapshot after rebuilding the data (needs the built `data/`):

```bash
uv run python -m claim_triage.app.demo_data
```

## Evaluate

The rules-only baseline costs nothing:

```bash
uv run python -m claim_triage.evaluation.harness --system baseline --split both
uv run python -m claim_triage.evaluation.harness --system baseline --tier hard --split both --out baseline_hard
```

Agent runs **cost money** (about $0.07 per case with the larger model, about $0.004 with the small one). Start small, and tune on the dev split only:

```bash
uv run python -m claim_triage.evaluation.harness --system agents --plan all_haiku --retrieval hybrid_rerank --split dev --per-cell 1 --workers 4 --out my_run
```

Options: `--retrieval none|bm25|graph|hybrid|hybrid_rerank`, `--plan routed|all_haiku|all_sonnet`, `--tier standard|hard|all`. Reports land in `eval/`, run traces in `traces/`.

## Optional: trace agent runs in Langfuse

Put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in `API_Keys.txt`, then install the extra packages once:

```bash
uv sync --native-tls --group tracing
```

Every model call is then filed under its claim ID (session = claim) in Langfuse. Without the keys or packages nothing is traced and nothing breaks; set `CLAIM_TRIAGE_TRACING=0` to switch it off.
Use synthetic data only: traces contain claim text.

## What is where

| Path | What |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md), `docs/DESIGN.pdf`, `docs/images/ARCHITECTURE.png` | the design, what is built and what is not, and the picture |
| `docs/images/INFOGRAPHIC.png` | one-page walkthrough of a single claim, for explaining the solution to teammates |
| `docs/Insurance_Claim_Triage_Technical_Deck.pptx` | 19-slide technical walkthrough plus a 10-slide appendix, aligned with the agentic-systems course framework, with speaker notes on every slide |
| `docs/images/dashboard/` | screenshots of every dashboard page, annotated page maps and the end-to-end example used in the dashboard description document |
| `docs/images/` | explainer pictures: which model does what, what an AI agent is, searching three ways, the design of the four AI parts, the six-layer agentic framework, the autonomy ladder, and the two architecture diagrams (agent with harness; multi-agent view) |
| [docs/DATA_360.md](docs/DATA_360.md) | the data foundation: privacy split, quality checks, known limits |
| [docs/DEMO.md](docs/DEMO.md) | a 5-minute demo script and likely questions |
| `docs/tools.md`, `docs/data_dictionary.md` | generated reference (rewritten by the build) |
| `src/claim_triage/` | data, tools, scoring, agents, retrieval, learning loop, evaluation, dashboard |
| `tests/` | 198 tests, one folder per area |
| `knowledge/` | fraud-pattern cards, policy clauses, the frozen test searches for the safety check |
| `config/` | data size, models, score weights |
| `eval/` | evaluation results (CSV and report per run; `eval/archive/` holds superseded trial runs); see `eval/README.md` |
| `data/` | the built DuckDB files and `app.duckdb` (all git-ignored, rebuilt from the seed) |
| `deploy/`, `demo_data/` | the Streamlit Community Cloud entry file with its small dependency list, and the safe synthetic snapshot it runs on (committed on purpose) |
| `traces/` | per-claim agent run records (git-ignored) |

## Safety notes

- **Rotate the API keys** that were shared during the build and set spending limits at both providers.
- The privacy vault is an unencrypted file and k-anonymity on every quasi-identifier is not achievable at 600 customers (see DATA_360.md section 13).
- This is a learning and demo system, not a production fraud decision engine.
