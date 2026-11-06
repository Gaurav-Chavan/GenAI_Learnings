"""Run the whole Data 360 pipeline: raw -> privacy split -> silver checks -> gold -> gold checks.

    uv run python -m claim_triage.data.build [--seed 42] [--customers 600]

A failed check stops the pipeline before the next layer is built.
"""
from __future__ import annotations

import argparse
import hashlib
import time

import duckdb

from .build_gold import run_gold
from .config import ROOT, load_config, paths
from .dictionary import write_dictionary
from .generate import generate_raw
from .privacy_split import run_privacy_split
from .quality import check_gold, check_privacy, check_silver, write_report


def dataset_hash(cfg: dict) -> str:
    """Hash of the silver and gold tables, so any teammate can verify they rebuilt identical data."""
    p = paths(cfg)
    h = hashlib.sha256()
    for layer in ("silver", "gold"):
        con = duckdb.connect(str(p[layer]), read_only=True)
        for (table,) in sorted(con.execute("select table_name from information_schema.tables where table_schema='main'").fetchall()):
            cols = [c for (c,) in con.execute(f"select column_name from information_schema.columns where table_name='{table}' order by ordinal_position").fetchall()]
            h.update(table.encode())
            for row in con.execute(f"select * from {table} order by {', '.join(cols)}").fetchall():
                h.update(repr(row).encode())
        con.close()
    return h.hexdigest()[:16]


def build(cfg: dict, verbose: bool = True) -> dict:
    log = print if verbose else (lambda *a, **k: None)
    sections, summary = {}, {}
    t0 = time.time()
    log("1/5 generating raw layer ...")
    summary["raw"] = generate_raw(cfg)
    log(f"    {summary['raw']}  ({time.time() - t0:.1f}s)")
    log("2/5 privacy split (vault + silver) ...")
    summary["privacy_split"] = run_privacy_split(cfg)
    log(f"    k-anonymity: {summary['privacy_split']['k_anonymity']}")
    log("3/5 silver + privacy checks ...")
    sections["silver"] = check_silver(cfg)
    sections["privacy"] = check_privacy(cfg, ("silver",))
    if not cfg.get("keep_raw"):
        paths(cfg)["raw"].unlink(missing_ok=True)  # raw holds the same fake personal data as the vault
        log("    raw layer removed (use --keep-raw to keep it for debugging)")
    log(f"    {len(sections['silver']) + len(sections['privacy'])} checks passed")
    log("4/5 gold layer ...")
    summary["gold"] = run_gold(cfg)
    log(f"    {summary['gold']}")
    log("5/5 gold checks ...")
    sections["gold"] = check_gold(cfg)
    sections["privacy (after gold)"] = check_privacy(cfg)
    summary["dataset_hash"] = dataset_hash(cfg)
    summary["dictionary"] = str(write_dictionary(cfg))
    extra = f"Seed {cfg['seed']} · dataset hash `{summary['dataset_hash']}` · k-anonymity {summary['privacy_split']['k_anonymity']}"
    report = write_report(cfg, sections, extra)
    log(f"done in {time.time() - t0:.1f}s · dataset hash {summary['dataset_hash']} · report {report}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int)
    ap.add_argument("--customers", type=int)
    ap.add_argument("--keep-raw", action="store_true", help="keep raw.duckdb (contains fake personal data) after the privacy split")
    args = ap.parse_args()
    overrides = {"keep_raw": args.keep_raw}
    if args.seed is not None:
        overrides["seed"] = args.seed
    if args.customers is not None:
        overrides["n_customers"] = args.customers
    cfg = load_config(overrides)
    summary = build(cfg)
    write_dictionary(cfg, ROOT / "docs" / "data_dictionary.md")


if __name__ == "__main__":
    main()
