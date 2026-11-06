"""Tracing is optional and only observes: off without keys, off on request, and it adds nothing to a model call when off."""
from claim_triage import tracing


def _reset(monkeypatch, tmp_path, **env):
    monkeypatch.setattr(tracing, "_state", {"on": None})
    monkeypatch.setattr("claim_triage.apikeys.SOURCES", ())  # ignore the real API_Keys.txt
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "CLAIM_TRIAGE_TRACING"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_off_without_keys(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    assert tracing.enable() is False
    assert tracing.metadata("verifier") == {}


def test_off_when_switched_off(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path, LANGFUSE_PUBLIC_KEY="pk-test", LANGFUSE_SECRET_KEY="sk-test", CLAIM_TRIAGE_TRACING="0")
    assert tracing.enable() is False
    assert tracing.metadata("investigator") == {}


def test_claim_goes_into_the_session_when_on(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    monkeypatch.setattr(tracing, "_state", {"on": True})
    assert tracing.metadata("verifier")["metadata"] == {"generation_name": "verifier", "tags": ["verifier"]}
    with tracing.claim_trace("CLM-000001"):
        meta = tracing.metadata("explainer")["metadata"]
        assert meta["session_id"] == "CLM-000001" and meta["generation_name"] == "explainer"
    assert "session_id" not in tracing.metadata("explainer")["metadata"]  # the claim does not leak past its block


def test_flush_is_safe_when_off(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    tracing.flush()
