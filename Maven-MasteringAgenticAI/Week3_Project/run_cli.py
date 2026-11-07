"""Drive the graph from the terminal, auto-approving both human checkpoints.

    python run_cli.py "Cursor"
"""
import sys
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.competitor_agent.graph import build_graph


def run(company: str) -> dict:
    graph = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke({"company": company, "last_step": "start"}, cfg)
    while result.get("__interrupt__"):
        checkpoint = result["__interrupt__"][0].value["checkpoint"]
        print(f"[human checkpoint] {checkpoint} -> auto-approving")
        result = graph.invoke(Command(resume={"approved": True}), cfg)
    return result


if __name__ == "__main__":
    final = run(sys.argv[1] if len(sys.argv) > 1 else "Cursor")
    print("\n".join(final["log"]))
    print("\nSaved:", final.get("saved_path"))
