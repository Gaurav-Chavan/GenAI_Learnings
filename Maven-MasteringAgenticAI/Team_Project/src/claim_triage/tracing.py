"""Optional Langfuse tracing of model calls. Off unless the Langfuse keys and the optional packages (uv sync --group tracing) are present.

Tracing only observes: it never changes a prompt, a score or a decision, and any failure here is swallowed so a run never breaks because of it.
Everything sent is synthetic claim text with customers as tokens; real claims must never be sent to a hosted service.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar

from .apikeys import load_api_keys

_claim: ContextVar[str | None] = ContextVar("trace_claim_id", default=None)
_state = {"on": None}


def enable() -> bool:
    """Turn tracing on once, if possible. Returns whether model calls are being traced."""
    if _state["on"] is not None:
        return _state["on"]
    _state["on"] = False
    available = load_api_keys()
    if os.environ.get("CLAIM_TRIAGE_TRACING", "1") == "0" or not {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"} <= set(available):
        return False
    try:
        import litellm
        import langfuse  # noqa: F401 - only to confirm the optional packages are installed
    except ImportError:
        return False
    host = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
    if host:  # the regional address (for example the Japan cloud) must reach every name the libraries look for
        for name in ("LANGFUSE_HOST", "LANGFUSE_OTEL_HOST"):
            os.environ.setdefault(name, host)
    if "langfuse_otel" not in litellm.callbacks:
        litellm.callbacks.append("langfuse_otel")
    _state["on"] = True
    return True


@contextmanager
def claim_trace(claim_id: str):
    """Everything inside belongs to one claim: every model call is filed under that claim's session."""
    token = _claim.set(claim_id)
    try:
        yield
    finally:
        _claim.reset(token)


def metadata(role: str) -> dict:
    """Extra fields for a model call; empty when tracing is off."""
    if not _state["on"]:
        return {}
    claim = _claim.get()
    meta = {"generation_name": role, "tags": [role]}
    if claim:
        meta.update(session_id=claim, trace_name=f"claim {claim}")
    return {"metadata": meta}


def flush() -> None:
    """Send anything still waiting (call before a short script exits)."""
    if not _state["on"]:
        return
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()
    except Exception:  # noqa: BLE001 - tracing must never break a run
        pass
