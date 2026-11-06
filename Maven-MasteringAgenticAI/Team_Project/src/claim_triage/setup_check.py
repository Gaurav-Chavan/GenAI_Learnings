"""Check that this computer is ready to run the project, and say what to do next. It never prints a key, a password or a login.

    uv run python -m claim_triage.setup_check            # a plain report
    uv run python -m claim_triage.setup_check --graph    # also try to reach Neo4j (takes a few seconds)

The checks are grouped in three levels, so a teammate can stop at the level they need:
    Level 1  no keys needed: the dashboard on rules-only scores and the saved agent runs
    Level 2  your own Neo4j instance: the knowledge base and its safety check, and hybrid retrieval
    Level 3  your own model keys: running the agents and the evaluation
"""
from __future__ import annotations

import glob
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .apikeys import load_api_keys
from .data.config import ROOT, load_config, paths

LEVELS = {1: "no keys needed: dashboard, rules-only scores, saved agent runs",
          2: "your own Neo4j instance: knowledge base and safety check, hybrid retrieval",
          3: "your own model keys: run the agents and the evaluation"}


@dataclass
class Check:
    level: int
    name: str
    ok: bool | None  # True = fine, False = to do, None = optional and not set
    detail: str = ""
    fix: str = ""


def _app_assessments(db: Path) -> int:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute("SELECT count(*) FROM assessments").fetchone()[0]
    except duckdb.Error:
        return 0
    finally:
        con.close()


def check_all(root: Path = ROOT, try_graph: bool = False) -> list[Check]:
    out: list[Check] = []
    ok = sys.version_info >= (3, 11)
    out.append(Check(1, "Python 3.11 or newer", ok, sys.version.split()[0], "install uv (https://docs.astral.sh/uv/); it installs the right Python for you"))
    missing = [m for m in ("duckdb", "streamlit", "yaml", "pydantic") if importlib.util.find_spec(m) is None]
    out.append(Check(1, "Project packages installed", not missing, "missing: " + ", ".join(missing) if missing else "", "run: uv sync"))

    cfg = load_config()
    data = paths(cfg)
    built = data["silver"].exists() and data["gold"].exists()
    out.append(Check(1, "Data built (synthetic claims)", built, "", "run: uv run python -m claim_triage.data.build"))
    app_db = Path(cfg["data_dir"]) / "app.duckdb"
    n = _app_assessments(app_db) if app_db.exists() else 0
    out.append(Check(1, "Dashboard database filled", n > 0, f"{n} stored assessments" if n else "", "run: uv run python -m claim_triage.app.backfill"))
    runs = len(glob.glob(str(root / "saved_runs" / "*.jsonl"))) + len(glob.glob(str(root / "traces" / "*.jsonl")))
    out.append(Check(1, "Saved agent runs present", runs > 0, f"{runs} files in saved_runs/ and traces/" if runs else "",
                     "they come with the repository (saved_runs/); if you removed them, run the agents with your own keys instead"))

    try:
        from .data.graph_conn import credentials

        creds = credentials(root)
        names = sorted(k for k in creds if k.startswith("NEO4J_"))
        out.append(Check(2, "Neo4j login found", True, "found: " + ", ".join(names)))
        have_login = True
    except Exception:  # noqa: BLE001 - no credentials, or the driver is not installed
        out.append(Check(2, "Neo4j login found", False, "", "copy .env.example to .env and fill in the NEO4J_ lines for YOUR own Aura instance"))
        have_login = False
    if try_graph and have_login:
        try:
            from .data.graph_conn import connect

            driver, _ = connect(retries=1, wait=0, log=lambda *a, **k: None)
            driver.close()
            out.append(Check(2, "Neo4j reachable", True))
        except Exception as exc:  # noqa: BLE001
            out.append(Check(2, "Neo4j reachable", False, type(exc).__name__, "resume the instance in the Aura console, or check the NEO4J_ lines in .env"))
    elif have_login:
        out.append(Check(2, "Neo4j reachable", None, "not tried", "add --graph to try"))

    keys = load_api_keys(root)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        out.append(Check(3, f"{name} found", name in keys, "", f"add {name}=... to your .env (your own key; never commit it)"))
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        out.append(Check(3, f"{name} (optional tracing)", True if name in keys else None, "", "only if you want Langfuse tracing"))
    return out


def render(checks: list[Check]) -> str:
    lines = ["Setup check (nothing secret is shown)", ""]
    for level, title in LEVELS.items():
        mine = [c for c in checks if c.level == level]
        done = all(c.ok is not False for c in mine)
        lines.append(f"Level {level} - {title}: {'READY' if done else 'not ready yet'}")
        for c in mine:
            mark = {True: "OK      ", False: "TO DO   ", None: "optional"}[c.ok]
            lines.append(f"  [{mark}] {c.name}" + (f" ({c.detail})" if c.detail else "") + (f"\n             -> {c.fix}" if c.ok is False and c.fix else ""))
        lines.append("")
    todo = next((c for c in sorted(checks, key=lambda c: c.level) if c.ok is False), None)
    lines.append("Next step: " + (f"{todo.fix} ({todo.name})" if todo else "everything is ready. Start the dashboard: uv run streamlit run src/claim_triage/app/dashboard.py"))
    return "\n".join(lines)


def main() -> None:
    print(render(check_all(try_graph="--graph" in sys.argv)))


if __name__ == "__main__":
    main()
