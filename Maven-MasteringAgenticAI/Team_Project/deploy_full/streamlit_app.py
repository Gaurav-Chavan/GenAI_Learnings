"""Entry point for the FULL dashboard on Streamlit Community Cloud, behind a password.

Main file path to give Streamlit Community Cloud (relative to the repository root):
    <folder of this project>/deploy_full/streamlit_app.py
    for example  Maven-MasteringAgenticAI/Team_Project/deploy_full/streamlit_app.py

Secrets (Advanced settings > Secrets), names only; see secrets.example.toml:
    APP_PASSWORD, NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE

Everything works as on a laptop: decisions, tags, owners, escalations and the knowledge-base safety check, which searches the Neo4j graph and writes approved
lessons to it (ApprovedSignal nodes only). The dashboard calls no language model, so no model keys are needed. The data is the synthetic snapshot in demo_data/,
copied to the server's temporary disk, so decisions and lesson records are lost when the app restarts or sleeps (lessons already written to Neo4j stay there).
Without APP_PASSWORD the app stays closed.
"""
from __future__ import annotations

import math
import os
import runpy
import shutil
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SOURCE = ROOT / "demo_data"
WORK = Path(tempfile.gettempdir()) / "claim_triage_full"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claim_triage.app.signin import GATE  # noqa: E402

NEO4J_NAMES = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")


def secret(name: str) -> str:
    try:
        return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - no secrets file, or no such name
        return ""


def sign_in_page(expected: str) -> None:
    st.set_page_config(page_title="Claim triage", page_icon="\U0001F6E1️")
    st.title("\U0001F6E1️ Claim triage")
    if not expected:
        st.error("This app is closed. Its owner has not set a password (APP_PASSWORD) in the app's Secrets.")
        st.stop()
    wait = GATE.paused_for()
    if wait:
        st.warning(f"Too many wrong passwords. Sign-in is paused for about {math.ceil(wait / 60)} minute(s) for everyone. Please try again later.")
        st.stop()
    with st.form("sign-in"):
        given = st.text_input("Password", type="password")
        go = st.form_submit_button("Open the dashboard", type="primary")
    if go:
        result = GATE.attempt(given, expected)
        if result == "ok":
            st.session_state["signed_in"] = True
            st.rerun()
        st.error("Sign-in is paused for everyone for a few minutes." if result == "paused" else "That password is not right.")
    st.stop()


if not st.session_state.get("signed_in"):
    sign_in_page(secret("APP_PASSWORD"))

for name in NEO4J_NAMES:  # the graph login reaches the code through environment variables, and only after a successful sign-in
    value = secret(name)
    if value:
        os.environ[name] = value

WORK.mkdir(parents=True, exist_ok=True)
for name in ("silver.duckdb", "app.duckdb"):  # one working copy shared by everyone who knows the password
    if not (WORK / name).exists():
        shutil.copyfile(SOURCE / name, WORK / name)
os.environ["CLAIM_TRIAGE_DATA_DIR"] = str(WORK)
os.environ["CLAIM_TRIAGE_DEMO"] = "0"
st.session_state["app_db_path"] = str(WORK / "app.duckdb")
runpy.run_path(str(SRC / "claim_triage" / "app" / "dashboard.py"), run_name="__main__")
