"""Central settings and client factories.

Every model name and connection detail lives here (overridable via .env), so the
GraphRAG pipeline, the vector baseline, and the evaluator all use the same setup.

Run `uv run python -m orggraph_rag.config` to check your .env, Neo4j and OpenAI.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")  # keep Chroma from phoning home


@dataclass(frozen=True)
class Settings:
    # Neo4j (Aura Free)
    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_username: str = os.getenv("NEO4J_USERNAME", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")

    # OpenAI models
    chat_model: str = os.getenv("CHAT_MODEL", "gpt-4o-mini")      # answer generation (both systems)
    cypher_model: str = os.getenv("CYPHER_MODEL", "gpt-4o")       # Text2Cypher
    judge_model: str = os.getenv("JUDGE_MODEL", "gpt-4o")         # LLM-as-judge in eval
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "1536"))

    # Streamlit app: when set, the UI asks for this password (protects your API credits on a public URL)
    app_password: str = os.getenv("APP_PASSWORD", "")

    # Local stores / outputs
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
    eval_dir: Path = PROJECT_ROOT / "eval"


settings = Settings()


@lru_cache
def get_driver():
    """Shared Neo4j driver (thread-safe; reuse it, don't create one per query)."""
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        # Aura flags db.index.*.queryNodes as deprecated in favour of SEARCH; they still
        # work, and the warnings would bury every script's output.
        notifications_disabled_classifications=["DEPRECATION"],
    )


@lru_cache
def get_embeddings():
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=settings.embedding_model)


def get_chat_llm(model: str | None = None, temperature: float = 0.0):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model or settings.chat_model, temperature=temperature)


def check_setup() -> bool:
    """Verify .env values, Neo4j connectivity and the OpenAI embedding model."""
    ok = True

    missing = [
        name
        for name, value in [
            ("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY")),
            ("NEO4J_URI", settings.neo4j_uri),
            ("NEO4J_PASSWORD", settings.neo4j_password),
        ]
        if not value
    ]
    if missing:
        print(f"[FAIL] Missing in .env: {', '.join(missing)}")
        return False
    print("[ OK ] .env values present")

    try:
        get_driver().verify_connectivity()
        with get_driver().session(database=settings.neo4j_database) as session:
            n = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        print(f"[ OK ] Neo4j reachable ({settings.neo4j_database}, {n} nodes)")
    except Exception as e:
        print(f"[FAIL] Neo4j: {e}")
        ok = False

    try:
        dim = len(get_embeddings().embed_query("ping"))
        if dim != settings.embedding_dim:
            print(f"[FAIL] Embedding dim {dim} != EMBEDDING_DIM {settings.embedding_dim}")
            ok = False
        else:
            print(f"[ OK ] OpenAI embeddings ({settings.embedding_model}, dim={dim})")
    except Exception as e:
        print(f"[FAIL] OpenAI: {e}")
        ok = False

    return ok


if __name__ == "__main__":
    raise SystemExit(0 if check_setup() else 1)
