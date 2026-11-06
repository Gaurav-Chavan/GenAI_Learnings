"""What a teammate relies on after cloning: the setup check, the settings file, the shipped saved runs, no secrets in the repository, a README that works."""
from __future__ import annotations

import json
import re
from urllib.parse import unquote

import pytest

from claim_triage import setup_check
from claim_triage.app import backfill
from claim_triage.data import graph_conn
from claim_triage.data.config import ROOT

SECRET_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGSMITH_API_KEY",
                "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")


@pytest.fixture
def clean(monkeypatch, tmp_path):
    """No key in the environment, and a scratch data folder, so the check sees a computer that has just cloned the repository."""
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, "x")  # registers the old state for restoring (the key loader sets variables in the process)
        monkeypatch.delenv(name)
    monkeypatch.setenv("CLAIM_TRIAGE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def by_name(checks):
    return {c.name: c for c in checks}


# ---------- the setup check ----------

def test_a_fresh_clone_is_told_what_to_do_first_and_nothing_is_demanded_that_is_optional(clean):
    checks = by_name(setup_check.check_all(root=clean))
    assert checks["Data built (synthetic claims)"].ok is False and checks["Dashboard database filled"].ok is False
    assert checks["Neo4j login found"].ok is False and checks["ANTHROPIC_API_KEY found"].ok is False
    assert checks["LANGFUSE_PUBLIC_KEY (optional tracing)"].ok is None  # optional never counts as a problem
    text = setup_check.render(list(checks.values()))
    assert "Level 1" in text and "Level 2" in text and "Level 3" in text
    assert text.rstrip().endswith("(Data built (synthetic claims))") and "claim_triage.data.build" in text.splitlines()[-1]


def test_the_report_shows_which_keys_exist_but_never_their_values(clean, monkeypatch):
    secrets = {"ANTHROPIC_API_KEY": "fake-anthropic-value-1", "OPENAI_API_KEY": "fake-openai-value-2", "NEO4J_URI": "neo4j+s://fake-host-3.example.invalid",
               "NEO4J_USERNAME": "fake-user-4", "NEO4J_PASSWORD": "fake-password-5"}
    (clean / ".env").write_text("\n".join(f"{k}={v}" for k, v in secrets.items()) + "\n", encoding="utf-8")
    checks = by_name(setup_check.check_all(root=clean))
    assert checks["ANTHROPIC_API_KEY found"].ok and checks["OPENAI_API_KEY found"].ok and checks["Neo4j login found"].ok
    shown = setup_check.render(list(checks.values())) + " ".join(c.detail + c.fix for c in checks.values())
    assert not any(v in shown for v in secrets.values())  # names only
    assert "NEO4J_PASSWORD" in checks["Neo4j login found"].detail


def test_a_copied_settings_file_with_empty_lines_counts_as_not_set(clean, monkeypatch):
    (clean / ".env").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    checks = by_name(setup_check.check_all(root=clean))
    assert checks["Neo4j login found"].ok is False and checks["ANTHROPIC_API_KEY found"].ok is False and checks["OPENAI_API_KEY found"].ok is False


def test_the_saved_runs_that_ship_with_the_repository_are_counted(clean):
    assert by_name(setup_check.check_all(root=ROOT))["Saved agent runs present"].ok is True
    assert by_name(setup_check.check_all(root=clean))["Saved agent runs present"].ok is False


# ---------- the settings file and the Neo4j login ----------

def test_the_neo4j_login_reads_quotes_and_ignores_empty_lines(clean):
    (clean / ".env").write_text('NEO4J_URI="neo4j+s://fake-host.example.invalid"\nNEO4J_USERNAME=\'neo4j\'\nNEO4J_PASSWORD=\nNEO4J_DATABASE=neo4j\n', encoding="utf-8")
    with pytest.raises(RuntimeError):  # the password is empty, so the login is incomplete
        graph_conn.credentials(clean)
    (clean / ".env").write_text('NEO4J_URI="neo4j+s://fake-host.example.invalid"\nNEO4J_USERNAME=\'neo4j\'\nNEO4J_PASSWORD="fake-pw"\n', encoding="utf-8")
    creds = graph_conn.credentials(clean)
    assert creds["NEO4J_URI"] == "neo4j+s://fake-host.example.invalid" and creds["NEO4J_USERNAME"] == "neo4j" and creds["NEO4J_PASSWORD"] == "fake-pw"


def test_the_example_settings_file_names_every_setting_and_holds_no_value():
    lines = [ln for ln in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if "=" in ln and not ln.lstrip().startswith("#")]
    values = dict(ln.split("=", 1) for ln in lines)
    assert set(values) >= {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"}
    assert all(not v.strip() or k in ("NEO4J_USERNAME", "NEO4J_DATABASE") for k, v in values.items())  # only the harmless defaults are filled in


# ---------- the saved agent runs ----------

def test_the_dashboard_gets_saved_agent_runs_straight_from_the_repository():
    runs = backfill.saved_agent_runs()
    assert len(runs) >= 300 and all(set(rec) >= {"claim_id", "band", "trust", "reasons", "trace"} for _, rec in list(runs.values())[:20])


def test_your_own_runs_win_over_the_shipped_ones(tmp_path, monkeypatch):
    (tmp_path / "saved_runs").mkdir()
    (tmp_path / "traces").mkdir()
    rec = lambda band: json.dumps({"claim_id": "CLM-000001", "case_id": "c", "band": band, "trust": 0.1, "reasons": [], "trace": {}}) + "\n"  # noqa: E731
    (tmp_path / "saved_runs" / "a_20260101.jsonl").write_text(rec("red"), encoding="utf-8")
    (tmp_path / "traces" / "mine_20261105.jsonl").write_text(rec("amber"), encoding="utf-8")
    monkeypatch.setattr(backfill, "ROOT", tmp_path)
    name, record = backfill.saved_agent_runs()["CLM-000001"]
    assert name.startswith("mine_") and record["band"] == "amber"


# ---------- nothing secret in the repository ----------

TEXT_SUFFIXES = {".py", ".md", ".toml", ".yaml", ".yml", ".txt", ".json", ".jsonl", ".csv", ".example", ".cfg"}
SKIP_DIRS = {".venv", "data", "traces", "__pycache__", ".pytest_cache", ".git", "docs_temp"}
KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|neo4j\+s://[^\s\"']*:[^\s\"'@]+@[^\s\"']+|"
                         r"(?:API_KEY|SECRET_KEY|PASSWORD)[ \t]*[=:][ \t]*[\"']?[A-Za-z0-9_-]{16,}")  # [ \t], not \s: a value never continues on the next line


def test_no_file_in_the_repository_contains_a_key_or_a_login():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts) or path.name == ".env":
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name == ".env.example":
            if KEY_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders


def test_the_ignore_file_keeps_every_kind_of_secret_out_of_git():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in (".env", "Neo4j-*.txt", "API_Keys*.txt", "secrets.toml", "traces/", "data/*.duckdb", ".venv/"):
        assert pattern in ignore, pattern
    assert "saved_runs/" not in ignore and ".env.example" not in ignore  # these two are meant to be shared


# ---------- the README and the documentation ----------

def test_every_link_in_the_readme_and_docs_leads_somewhere():
    broken = []
    for md in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "eval" / "README.md"]:
        if not md.exists():
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text(encoding="utf-8")):
            if not re.match(r"^(https?:|mailto:)", target) and not (md.parent / unquote(target)).exists():
                broken.append((md.name, target))
    assert not broken, broken


def test_the_readme_points_to_every_document_in_the_docs_folder():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [p.name for p in (ROOT / "docs").iterdir() if p.is_file() and p.suffix in (".md", ".pdf", ".pptx") and p.name not in readme]
    assert not missing, missing
    assert (ROOT / "docs" / "images").is_dir() and "docs/images" in readme


def test_the_readme_guides_a_teammate_through_the_three_levels():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for must in ("Level 1", "Level 2", "Level 3", "setup_check", ".env.example", "your own Neo4j", "claim_triage.data.build", "claim_triage.app.backfill", "streamlit run",
                 "Troubleshooting", "git clone"):
        assert must in readme, must
    assert not any(gone in readme for gone in ("Community Cloud", "deploy_full", "demo_data"))  # the deployment work was removed


def test_the_streamlit_deployment_files_are_gone():
    assert not any((ROOT / name).exists() for name in ("deploy", "deploy_full", "demo_data"))
