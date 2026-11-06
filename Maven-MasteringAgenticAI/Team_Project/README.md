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

Read [docs/DESIGN.md](docs/DESIGN.md) section 0 for the full status. In short: the system works end to end and its guard rails are tested (206 tests), but
**on the clean original data the plain rules beat the agents** (99–99.5% against 91.5–93% right band on dev and held-out, with the small model; the two splits agree).
On that data the agents never put fraud in green, but they send some real fraud to "review needed" instead of red. They win on 80 "hard" cases designed to need reading
documents and looking up past decisions (71% without retrieval and 100% with it on both splits, against 50% for the rules). On the two weakest original scenarios (X1, X2) the larger model closed most of the gap
(95% against 65–78% for the small model; rules 100%) at about 18 times the cost per claim. Agent runs so far cost about $10.4.

## Set up and run

You run this project **on your own computer with your own keys and your own Neo4j**. Nobody shares a key or a database. Follow the steps in order; the whole setup takes about 15 minutes.

### 1. Install two tools, once

You need **git** and **[uv](https://docs.astral.sh/uv/)**. uv installs the right Python for you, so you do not install Python yourself.

```bash
# macOS or Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Get the code and install it

This project lives in a folder of a larger repository, so move into it after cloning:

```bash
git clone https://github.com/Gaurav-Chavan/GenAI_Learnings.git
cd GenAI_Learnings/Maven-MasteringAgenticAI/Team_Project
uv sync
```

Behind a company network that inspects TLS you may see a certificate error. Add `--system-certs` to the `uv` command (older uv versions: `--native-tls`).

### 3. Add your own keys: the `.env` file

Create your settings file from the example. It is git-ignored, so it never leaves your computer.

```bash
cp .env.example .env            # macOS, Linux, Git Bash
```

```powershell
Copy-Item .env.example .env     # Windows PowerShell
```

Open `.env` and fill in these lines:

| Setting | Get it from |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | **your own** free Aura instance at [console.neo4j.io](https://console.neo4j.io) |

Set a **spending limit** with both model providers first; agent runs cost money (see [Costs](#costs)).
For Neo4j, Aura gives you a file called `Neo4j-<id>-Created-<date>.txt`. Either copy its `NEO4J_` lines into `.env`, or keep that file in this folder (it is git-ignored too).
**Use your own Neo4j instance and never a teammate's**: loading the graph (step 6) deletes everything in the target database.

Then ask the project to check your computer. It tells you in plain words what is ready and what to do next, and it never prints a key:

```bash
uv run python -m claim_triage.setup_check
```

### 4. Build the data

```bash
uv run python -m claim_triage.data.build
```

Builds the synthetic claims, customers, documents and the privacy vault. About 15 seconds, and it is deterministic: everyone gets the same data.

### 5. Load the knowledge graph into your Neo4j

```bash
uv run python -m claim_triage.data.load_neo4j --clear
uv run python -m claim_triage.setup_check --graph
```

`--clear` deletes every node in your Neo4j database first, so point it at your own empty instance. The second command confirms the connection and that the graph is loaded.
A free Aura instance pauses after a few days of no use; if a check says it cannot reach Neo4j, resume the instance in the Aura console and try again.

### 6. Fill the dashboard database

```bash
uv run python -m claim_triage.app.backfill
```

Free: no model is called. It stores the rules-only score for every claim and the saved agent runs that ship with the repository (`saved_runs/`).

### 7. Start the dashboard

```bash
uv run streamlit run src/claim_triage/app/dashboard.py
```

Open the address Streamlit prints (usually http://localhost:8501). You can filter the queue, open a claim, record your decision, tick findings for the knowledge base
(its safety check uses your Neo4j) and read the Evaluation page. Your decisions are stored in `data/app.duckdb` on your own computer. For a guided tour, follow [docs/DEMO.md](docs/DEMO.md).

If you only want to look at the dashboard, steps 1, 2, 4, 6 and 7 are enough; it opens without any key. The keys and Neo4j are needed for the knowledge base safety check and for running the agents.

### 8. Run the agents yourself

Start small, and tune on the dev split only:

```bash
uv run python -m claim_triage.evaluation.harness --system agents --plan all_haiku --retrieval hybrid_rerank --split dev --per-cell 1 --workers 4 --out my_run
```

Your runs are written to `traces/` (git-ignored). Run step 6 again and the dashboard shows them in the "Saved agent runs" view; your own runs win over the shipped ones.

## Costs

Building the data, the graph load, the backfill, the dashboard and the rules-only baseline are **free**. Agent runs **cost money**:

| What | Roughly |
|---|---|
| one claim with the small model | about $0.004 |
| one claim with the larger model (used for customers with several policies) | about $0.07 |
| the full 200-claim dev run with the small model | $1.80 |

Options: `--retrieval none|bm25|graph|hybrid|hybrid_rerank`, `--plan routed|all_haiku|all_sonnet`, `--tier standard|hard|all`. Reports land in `eval/`.

## Keys and settings

Every setting lives in your `.env` file. Rules that keep the team safe:
- **Never commit a key** and never paste one into chat, an issue or a document. `.gitignore` already blocks `.env`, `Neo4j-*.txt` and `API_Keys*.txt`, and the test suite scans the repository for key-like text.
- Alternatives that also work: environment variables, or `NAME=value` lines in `API_Keys.txt` (keys) and the `Neo4j-*.txt` file from Aura (graph).
- If you ever commit a key by mistake, **revoke it at the provider straight away**; removing it from git is not enough.

## Documentation

Everything written about the project is in [`docs/`](docs). A good reading order for a new teammate:

| Read | What it is |
|---|---|
| [docs/DEMO.md](docs/DEMO.md) | a 5-minute guided tour of the dashboard, and questions you should expect |
| [docs/DESIGN.md](docs/DESIGN.md) (also `docs/DESIGN.pdf`) | the design, what is built and what is not, and the honest evaluation results |
| `docs/ARCHITECTURE.pdf`, [docs/images/](docs/images) | the architecture pictures and the explainer pictures (agents, retrieval, the framework) |
| [docs/DATA_360.md](docs/DATA_360.md) | the data foundation: privacy split, quality checks, known limits |
| [Insurance Claim Triage Dashboard Description.pdf](docs/Insurance%20Claim%20Triage%20Dashboard%20Description.pdf) | every page and component of the dashboard, with screenshots and a worked example |
| `docs/Insurance_Claim_Triage_Technical_Deck.pptx` | a 19-slide technical walkthrough plus a 10-slide appendix, with speaker notes |
| [docs/tools.md](docs/tools.md), [docs/data_dictionary.md](docs/data_dictionary.md) | generated reference: the agents' tools and every data field |
| [eval/README.md](eval/README.md) | how to read the evaluation results in `eval/` |

## Evaluate

The rules-only baseline costs nothing:

```bash
uv run python -m claim_triage.evaluation.harness --system baseline --split both
uv run python -m claim_triage.evaluation.harness --system baseline --tier hard --split both --out baseline_hard
```

## Optional: trace agent runs in Langfuse

Put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in `.env`, then install the extra packages once:

```bash
uv sync --group tracing
```

Every model call is then filed under its claim ID (session = claim) in Langfuse. Without the keys or packages nothing is traced and nothing breaks; set `CLAIM_TRIAGE_TRACING=0` to switch it off.
Use synthetic data only: traces contain claim text.

## Run the tests

```bash
uv run pytest
```

206 tests, about two minutes. A few tests skip themselves when Neo4j is unreachable or you have not built the data; that is normal before step 5.
Please run them before you push a change.

## Troubleshooting

| You see | Do this |
|---|---|
| a certificate or TLS error from `uv` or `git` | add `--system-certs` to `uv` (older uv: `--native-tls`); for git on Windows use `git -c http.sslBackend=schannel ...` |
| `No module named ...` | run `uv sync`, and start commands with `uv run` |
| the dashboard shows no claims | run steps 4 and 6: `data.build`, then `app.backfill` |
| "Saved agent runs" looks empty | run `uv run python -m claim_triage.app.backfill` again |
| `Missing API key(s)` | add your own key to `.env`; run `setup_check` to see which one is missing |
| `could not be reached` (Neo4j) | resume your Aura instance in its console and check the `NEO4J_` lines; then `setup_check --graph` |
| "port already in use" | start the dashboard on another port: `uv run streamlit run src/claim_triage/app/dashboard.py --server.port 8502` |
| characters look broken in the Windows console | set `PYTHONIOENCODING=utf-8` for the session |
| something else | run `uv run python -m claim_triage.setup_check` first; it names the next step |

## Working together

- Use **your own** keys and **your own** Neo4j instance. Never share them, and never commit them.
- The dashboard database `data/app.duckdb` is yours alone; your decisions and lessons are not shared unless you share the file.
- Do not commit `data/`, `traces/` or `.env`; they are git-ignored on purpose. Commit code, docs and tests.
- Run `uv run pytest` before you push. Keep the data synthetic.

## What is where

| Path | What |
|---|---|
| `src/claim_triage/` | data, tools, scoring, agents, retrieval, learning loop, evaluation, dashboard, `setup_check` |
| `tests/` | 206 tests, one folder per area |
| [docs/](docs) | all documentation (see the table above) |
| `knowledge/` | fraud-pattern cards, policy clauses, the frozen test searches for the safety check |
| `config/` | data size, models, score weights |
| `eval/` | evaluation results (CSV and report per run; `eval/archive/` holds superseded trial runs) |
| `saved_runs/` | saved agent runs that ship with the repository, so the dashboard has agent results without spending money |
| `.env.example` | the list of settings; copy it to `.env` |
| `data/` | built on your computer: DuckDB files and `app.duckdb` (git-ignored, rebuilt from the seed) |
| `traces/` | your own agent run records (git-ignored) |

## Safety notes

- The privacy vault is an unencrypted file and k-anonymity on every quasi-identifier is not achievable at 600 customers (see DATA_360.md section 13).
- This is a learning and demo system, not a production fraud decision engine. A person decides every claim.
- The knowledge graph is **yours**: the safety check writes to it, and loading it deletes what is there.
