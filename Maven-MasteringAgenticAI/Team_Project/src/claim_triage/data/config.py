"""Build configuration and file locations for the Data 360 pipeline."""
from __future__ import annotations

import copy
import os
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(overrides: dict | None = None) -> dict:
    with open(ROOT / "config" / "data.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg = _merge(copy.deepcopy(cfg), overrides or {})
    if os.environ.get("CLAIM_TRIAGE_DATA_DIR"):  # a deployed demo keeps its data copy in a temporary folder
        cfg["data_dir"] = os.environ["CLAIM_TRIAGE_DATA_DIR"]
    data_dir = Path(cfg["data_dir"])
    cfg["data_dir"] = str(data_dir if data_dir.is_absolute() else ROOT / data_dir)
    return cfg


def paths(cfg: dict) -> dict[str, Path]:
    base = Path(cfg["data_dir"])
    base.mkdir(parents=True, exist_ok=True)
    return {name: base / f"{name}.duckdb" for name in ("raw", "vault", "silver", "gold", "labels")}


def when(cfg: dict, key: str) -> date:
    return date.fromisoformat(cfg[key])
