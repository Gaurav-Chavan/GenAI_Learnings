"""A small, safe snapshot of the data for a public demo (Streamlit Community Cloud).

    uv run python -m claim_triage.app.demo_data

Only two files are written to demo_data/: the reviewer-safe data layer (silver) and an app database that holds the stored assessments but no decisions,
no knowledge-base signals and no claim owners. The raw layer, the identity layer, the gold layer and the hidden answer keys are never read or copied.
The data is synthetic, so the snapshot can be committed to a public repository.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import duckdb

from ..data.config import ROOT, load_config, paths
from .store import Store

DEMO_DIR = ROOT / "demo_data"
DEMO_FILES = ("silver.duckdb", "app.duckdb")


COLUMNS = "claim_id, system, band, trust_score, evidence_status, result_json, source, created_at"


def _stored_assessments(source: Store | Path) -> list[tuple]:
    """The stored assessments of an app database: through an open Store, or by reading a database file without changing it."""
    if isinstance(source, Store):
        return [tuple(r.values()) for r in source.rows(f"SELECT {COLUMNS} FROM assessments")]
    con = duckdb.connect(str(source), read_only=True)
    try:
        return con.execute(f"SELECT {COLUMNS} FROM assessments").fetchall()
    finally:
        con.close()


def build_demo_data(cfg: dict, out_dir: Path = DEMO_DIR, source: Store | Path | None = None) -> list[Path]:
    """Write the two demo files into out_dir (replacing old ones) and return their paths. source: the app database to take assessments from."""
    rows = _stored_assessments(source if source is not None else Path(cfg["data_dir"]) / "app.duckdb")
    out_dir.mkdir(parents=True, exist_ok=True)
    silver_out, app_out = out_dir / "silver.duckdb", out_dir / "app.duckdb"
    for stale in (app_out, out_dir / "app.duckdb.wal", silver_out, out_dir / "silver.duckdb.wal"):
        stale.unlink(missing_ok=True)
    shutil.copyfile(paths(cfg)["silver"], silver_out)
    fresh = Store(cfg, path=app_out)  # creates the empty tables, so the demo starts with no decisions, signals or owners
    try:
        fresh.con.executemany(f"INSERT INTO assessments ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        fresh.con.execute("CHECKPOINT")
    finally:
        fresh.close()
    return [silver_out, app_out]


def main() -> None:
    for path in build_demo_data(load_config()):
        print(f"{path.name}: {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
