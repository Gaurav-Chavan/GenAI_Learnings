"""Entry point for Streamlit Community Cloud: the reviewer dashboard on the bundled synthetic demo data.

Main file path to give Streamlit Community Cloud (relative to the repository root):
    Maven-MasteringAgenticAI/Team_Project/deploy/streamlit_app.py

Locally:  uv run streamlit run deploy/streamlit_app.py

Nothing here needs an API key. The safety check for lessons is switched off (CLAIM_TRIAGE_DEMO), and each visitor's session works on a private
copy of the app database, so decisions are never shared between visitors and disappear when the app restarts.
"""
from __future__ import annotations

import os
import runpy
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SOURCE = ROOT / "demo_data"
WORK = Path(tempfile.gettempdir()) / "claim_triage_demo"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

WORK.mkdir(parents=True, exist_ok=True)
for name in ("silver.duckdb", "app.duckdb"):  # one shared, read-only copy of the data and one pristine app database to copy from
    if not (WORK / name).exists():
        shutil.copyfile(SOURCE / name, WORK / name)
for old in WORK.glob("session_*.duckdb*"):  # tidy up the private copies of visitors who left long ago
    if time.time() - old.stat().st_mtime > 6 * 3600:
        old.unlink(missing_ok=True)
if "app_db_path" not in st.session_state:
    mine = WORK / f"session_{uuid.uuid4().hex[:10]}.duckdb"
    shutil.copyfile(WORK / "app.duckdb", mine)
    st.session_state["app_db_path"] = str(mine)

os.environ["CLAIM_TRIAGE_DATA_DIR"] = str(WORK)
os.environ["CLAIM_TRIAGE_DEMO"] = "1"
runpy.run_path(str(SRC / "claim_triage" / "app" / "dashboard.py"), run_name="__main__")
