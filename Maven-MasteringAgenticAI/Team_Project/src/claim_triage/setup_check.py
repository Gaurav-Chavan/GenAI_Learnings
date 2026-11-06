"""Check that this computer is ready to run the project, and say what to do next. It never prints a key, a password or a login.

    uv run python -m claim_triage.setup_check            # a plain report
    uv run python -m claim_triage.setup_check --graph    # also connect to your Neo4j and check that the graph is loaded (takes a few seconds)

The steps follow the README, in order: install, settings (.env), data, graph, dashboard database.
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


@dataclass
class Check:
    step: str
    name: str
    ok: bool | None  # True = fine, False = to do, None = optional or not tried
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


def _graph_nodes() -> int:
    from .data.graph_conn import connect

    driver, database = connect(retries=1, wait=0, log=lambda *a, **k: None)
    try:
        with driver.session(database=database) as session:
            return session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
    finally:
        driver.close()


def check_all(root: Path = ROOT, try_graph: bool = False) -> list[Check]:
    out: list[Check] = []
    inst = "Install"
    out.append(Check(inst, "Python 3.11 or newer", sys.version_info >= (3, 11), sys.version.split()[0], "install uv (https://docs.astral.sh/uv/); it installs the right Python for you"))
    missing = [m for m in ("duckdb", "streamlit", "yaml", "pydantic") if importlib.util.find_spec(m) is None]
    out.append(Check(inst, "Project packages installed", not missing, "missing: " + ", ".join(missing) if missing else "", "run: uv sync"))

    sett = "Settings (.env)"
    keys = load_api_keys(root)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        out.append(Check(sett, f"{name} found", name in keys, "", f"copy .env.example to .env and add your own {name}"))
    try:
        from .data.graph_conn import credentials

        found = sorted(k for k in credentials(root) if k.startswith("NEO4J_"))
        out.append(Check(sett, "Neo4j login found", True, "found: " + ", ".join(found)))
        have_login = True
    except Exception:  # noqa: BLE001 - no credentials, or the driver is not installed
        out.append(Check(sett, "Neo4j login found", False, "", "copy .env.example to .env and fill in the NEO4J_ lines for YOUR own Aura instance"))
        have_login = False
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        out.append(Check(sett, f"{name} (optional tracing)", True if name in keys else None, "", "only if you want Langfuse tracing"))

    cfg = load_config()
    data = paths(cfg)
    out.append(Check("Data", "Data built (synthetic claims)", data["silver"].exists() and data["gold"].exists(), "", "run: uv run python -m claim_triage.data.build"))

    gr = "Graph"
    if try_graph and have_login:
        try:
            nodes = _graph_nodes()
            out.append(Check(gr, "Neo4j reachable", True))
            out.append(Check(gr, "Graph loaded (claims and knowledge)", nodes > 0, f"{nodes} nodes" if nodes else "empty", "run: uv run python -m claim_triage.data.load_neo4j --clear"))
        except Exception as exc:  # noqa: BLE001
            out.append(Check(gr, "Neo4j reachable", False, type(exc).__name__, "resume your instance in the Aura console, or check the NEO4J_ lines in .env"))
    elif have_login:
        out.append(Check(gr, "Neo4j reachable and graph loaded", None, "not tried", "add --graph to try"))

    app_db = Path(cfg["data_dir"]) / "app.duckdb"
    n = _app_assessments(app_db) if app_db.exists() else 0
    out.append(Check("Dashboard", "Dashboard database filled", n > 0, f"{n} stored assessments" if n else "", "run: uv run python -m claim_triage.app.backfill"))
    runs = len(glob.glob(str(root / "saved_runs" / "*.jsonl"))) + len(glob.glob(str(root / "traces" / "*.jsonl")))
    out.append(Check("Dashboard", "Saved agent runs present", runs > 0, f"{runs} files in saved_runs/ and traces/" if runs else "",
                     "they come with the repository (saved_runs/); if you removed them, run the agents yourself"))
    return out


def render(checks: list[Check]) -> str:
    lines = ["Setup check (nothing secret is shown)", ""]
    for step in dict.fromkeys(c.step for c in checks):
        mine = [c for c in checks if c.step == step]
        lines.append(f"{step}: {'ready' if all(c.ok is not False for c in mine) else 'not ready yet'}")
        for c in mine:
            mark = {True: "OK      ", False: "TO DO   ", None: "optional"}[c.ok]
            lines.append(f"  [{mark}] {c.name}" + (f" ({c.detail})" if c.detail else "") + (f"\n             -> {c.fix}" if c.ok is False and c.fix else ""))
        lines.append("")
    todo = next((c for c in checks if c.ok is False), None)
    lines.append("Next step: " + (f"{todo.fix} ({todo.name})" if todo else "everything is ready. Start the dashboard: uv run streamlit run src/claim_triage/app/dashboard.py"))
    return "\n".join(lines)


def main() -> None:
    print(render(check_all(try_graph="--graph" in sys.argv)))


if __name__ == "__main__":
    main()
