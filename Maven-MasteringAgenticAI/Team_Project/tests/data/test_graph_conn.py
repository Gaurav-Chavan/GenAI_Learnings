"""Credential parsing for the Neo4j connection (no network, no real credentials)."""
from claim_triage.data import graph_conn


def test_parses_aura_credentials_file_and_ignores_comments(tmp_path):
    f = tmp_path / "Neo4j-test.txt"
    f.write_text("# Wait a few minutes before connecting\nNEO4J_URI=neo4j+s://x.databases.neo4j.io\nNEO4J_USERNAME=user\n"
                 "NEO4J_PASSWORD=secret-value\nNEO4J_DATABASE=db\nAURA_INSTANCEID=abc\n", encoding="utf-8")
    creds = graph_conn.parse_credentials(f)
    assert creds["NEO4J_URI"].startswith("neo4j+s://") and creds["NEO4J_DATABASE"] == "db"
    assert "# Wait" not in creds


def test_credentials_are_found_in_a_neo4j_file_and_never_printed(tmp_path, monkeypatch):
    for key in graph_conn.KEYS:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "Neo4j-abc.txt").write_text("NEO4J_URI=neo4j+s://a\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=p\n", encoding="utf-8")
    creds = graph_conn.credentials(tmp_path)
    assert creds["NEO4J_USERNAME"] == "u"


def test_missing_credentials_give_a_clear_error_without_leaking_anything(tmp_path, monkeypatch):
    for key in graph_conn.KEYS:
        monkeypatch.delenv(key, raising=False)
    try:
        graph_conn.credentials(tmp_path)
    except RuntimeError as exc:
        assert "No Neo4j credentials found" in str(exc)
    else:
        raise AssertionError("expected an error")
