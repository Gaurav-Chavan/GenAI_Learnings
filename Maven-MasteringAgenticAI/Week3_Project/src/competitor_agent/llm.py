"""Chat model factories. Both read their config from the environment at call time."""
import os


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def get_orchestrator_llm():
    """Orchestrator / discovery / writer model, e.g. ORCHESTRATOR_MODEL=anthropic:claude-sonnet-5-5."""
    from langchain.chat_models import init_chat_model

    return init_chat_model(_require("ORCHESTRATOR_MODEL"))


def get_analyst_llm():
    """Analyst extraction model served by Nebius Token Factory (OpenAI-compatible endpoint)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=_require("ANALYST_MODEL"),
        base_url=_require("NEBIUS_BASE_URL"),
        api_key=_require("NEBIUS_API_KEY"),
        temperature=0.1,
        max_tokens=3500,  # a full profile is ~1.5k tokens; a runaway generation must be cut off
        timeout=120,
        max_retries=1,
    )
