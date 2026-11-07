"""UI-facing controller around the compiled graph. No Streamlit imports, so it is testable.

One ResearchSession = one LangGraph thread. The UI never inspects graph internals; it asks
`snapshot()` which phase the run is in and calls start / resume / retry to move it forward.

Phases
  new                   nothing started
  awaiting_competitors  paused at human checkpoint 1 (confirm / edit the competitor list)
  awaiting_briefing     paused at human checkpoint 2 (approve / request changes)
  failed                a node raised after its retries; the last checkpoint is intact, so
                        retry() continues from there instead of starting over
  done                  graph finished (saved_path is set only if the human approved)
"""
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from langgraph.types import Command

CHECKPOINT_PHASE = {
    "approve_competitors": "awaiting_competitors",
    "approve_briefing": "awaiting_briefing",
}


@dataclass
class Event:
    """One graph step, for the live progress view."""
    node: str
    lines: list[str] = field(default_factory=list)  # human-readable log lines it produced


@dataclass
class Snapshot:
    phase: str
    checkpoint: dict | None = None  # the interrupt payload shown to the human
    values: dict = field(default_factory=dict)
    error: str | None = None


class ResearchSession:
    def __init__(self, graph, thread_id: str | None = None):
        self.graph = graph
        self.thread_id = thread_id or str(uuid.uuid4())

    @property
    def _cfg(self) -> dict:
        return {"configurable": {"thread_id": self.thread_id}}

    # ------------------------------------------------------------------ moving the run
    def start(self, company: str) -> Iterator[Event]:
        return self._stream({"company": company.strip(), "last_step": "start"})

    def resume(self, payload: dict) -> Iterator[Event]:
        """Answer the current checkpoint. Payload must be non-empty: LangGraph ignores {}."""
        if not payload:
            raise ValueError("resume payload must not be empty")
        return self._stream(Command(resume=payload))

    def retry(self) -> Iterator[Event]:
        """Continue from the last good checkpoint after a failure (input=None)."""
        return self._stream(None)

    def _stream(self, graph_input: Any) -> Iterator[Event]:
        for chunk in self.graph.stream(graph_input, self._cfg, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    continue
                lines = update.get("log", []) if isinstance(update, dict) else []
                yield Event(node, list(lines))

    # --------------------------------------------------------------------- reading state
    def snapshot(self) -> Snapshot:
        state = self.graph.get_state(self._cfg)
        if not state.values:
            return Snapshot("new")
        values = dict(state.values)
        if not state.next:
            return Snapshot("done", values=values)
        for task in state.tasks:
            if task.interrupts:
                payload = task.interrupts[0].value
                return Snapshot(CHECKPOINT_PHASE.get(payload.get("checkpoint"), "failed"),
                                checkpoint=payload, values=values)
        errors = [t.error for t in state.tasks if getattr(t, "error", None)]
        return Snapshot("failed", values=values, error=str(errors[0]) if errors else None)
