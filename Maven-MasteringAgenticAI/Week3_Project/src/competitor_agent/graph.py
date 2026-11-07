"""Wires the supervisor graph.

        START -> orchestrator <-> {discovery, gather x N, analyze x N, critic, writer, save}
                      |
                      +-> competitor_review (interrupt)   human checkpoint 1
                      +-> final_approval   (interrupt)    human checkpoint 2

Every worker returns to the orchestrator, which decides the next move.
"""
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from . import nodes
from .state import ResearchState

WORKERS = ("discovery", "gather", "analyze", "critic", "writer", "save")


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(ResearchState)
    retry = RetryPolicy(max_attempts=3, initial_interval=1.0)  # tool/network flakiness

    g.add_node("orchestrator", nodes.orchestrator,
               destinations=(*WORKERS, "competitor_review", "final_approval", END))
    g.add_node("discovery", nodes.discovery, retry_policy=retry)
    g.add_node("gather", nodes.gather, retry_policy=retry)
    g.add_node("analyze", nodes.analyze, retry_policy=retry)
    g.add_node("critic", nodes.critic)
    g.add_node("writer", nodes.writer)
    g.add_node("save", nodes.save)
    g.add_node("competitor_review", nodes.competitor_review, destinations=("orchestrator",))
    g.add_node("final_approval", nodes.final_approval, destinations=("orchestrator",))

    g.add_edge(START, "orchestrator")
    for w in WORKERS:
        if w != "save":
            g.add_edge(w, "orchestrator")
    g.add_edge("save", END)

    return g.compile(checkpointer=checkpointer)
