"""Vector RAG baseline: dense top-k retrieval over entity cards stored in Chroma.

    uv run python -m orggraph_rag.vector_rag --build              # (re)build the index
    uv run python -m orggraph_rag.vector_rag "Who approved the pricing change?"

Corpus: one card per entity (its properties + every relationship, see verbalize.py),
so it holds the same facts as the graph. Documents' full text is inside their cards.
Each card is short (well under the embedding model's limit), so one card = one chunk.
Same embeddings, answer model and prompt as GraphRAG; top-k = 4.
"""

import argparse
import shutil
import time
from functools import lru_cache
from typing import List

from langchain_chroma import Chroma
from langchain_core.documents import Document

from .config import get_embeddings, settings
from .data import mock_data_ext as data
from .generation import RAGResult, Source, generate
from .verbalize import display_name, entity_cards

COLLECTION = "org_entity_cards"
TOP_K = 4


def build_index() -> int:
    """Rebuild the Chroma collection from scratch (idempotent)."""
    shutil.rmtree(settings.chroma_dir, ignore_errors=True)
    nodes = data.all_nodes()
    cards = entity_cards(nodes, data.RELATIONSHIPS)
    docs = [
        Document(page_content=cards[n["id"]], metadata={"id": n["id"], "kind": n["kind"], "name": display_name(n)})
        for n in nodes
    ]
    Chroma.from_documents(
        docs,
        get_embeddings(),
        ids=[n["id"] for n in nodes],
        collection_name=COLLECTION,
        persist_directory=str(settings.chroma_dir),
        collection_metadata={"hnsw:space": "cosine"},
    )
    _store.cache_clear()
    return len(docs)


def ensure_index() -> None:
    """Build the index if it's missing - e.g. a fresh deployment, where chroma_db/ isn't in git."""
    if not settings.chroma_dir.exists():
        build_index()


@lru_cache
def _store() -> Chroma:
    if not settings.chroma_dir.exists():
        raise RuntimeError("Vector index missing - run: uv run python -m orggraph_rag.vector_rag --build")
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=get_embeddings(),
        persist_directory=str(settings.chroma_dir),
    )


def retrieve(question: str, k: int = TOP_K) -> List[Source]:
    hits = _store().similarity_search_with_relevance_scores(question, k=k)
    return [Source(id=doc.metadata["id"], text=doc.page_content, score=round(score, 3)) for doc, score in hits]


def answer(question: str, k: int = TOP_K) -> RAGResult:
    started = time.perf_counter()
    sources = retrieve(question, k)
    return generate("vector", question, sources, started, top_k=k)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", help="question to answer")
    parser.add_argument("--build", action="store_true", help="rebuild the Chroma index")
    parser.add_argument("-k", type=int, default=TOP_K, help=f"top-k cards to retrieve (default {TOP_K})")
    args = parser.parse_args()

    if args.build:
        print(f"Indexed {build_index()} entity cards into {settings.chroma_dir.name}/{COLLECTION}.")
    if args.question:
        result = answer(args.question, args.k)
        print("Retrieved:")
        for s in result.sources:
            print(f"  {s.score:.3f}  {s.id}")
        print(f"\n{result.answer}\n")
        print(f"citations={result.citations}  latency={result.latency_s}s  "
              f"tokens={result.input_tokens}+{result.output_tokens}")
    if not (args.build or args.question):
        parser.print_help()


if __name__ == "__main__":
    main()
